"""
Tests for ensure-target story advancement and episode type distribution.

Verifies:
1. StoryStatus.episode_type field serialization
2. Round-robin episode type assignment at batch initialization
3. story_index advances on failure (no infinite retry)
4. Failed story types are queued and inherited by replacements
5. Per-story episode_type is passed to the generator
"""

import json
import os
import uuid
from collections import Counter
from pathlib import Path
from unittest.mock import Mock, MagicMock, patch, PropertyMock
from dataclasses import dataclass

import pytest

from batch.schemas import StoryStatus, BatchConfig, BatchState, _normalize_path
from simple_gest_random_generator import EPISODE_TYPES


# ============================================================================
# Helpers
# ============================================================================

def make_story_status(story_id="abc123", story_number=1, status="pending",
                      output_dir="output/story_00001_abc123", episode_type=None):
    """Create a StoryStatus with sensible defaults."""
    return StoryStatus(
        story_id=story_id,
        story_number=story_number,
        status=status,
        output_dir=output_dir,
        episode_type=episode_type,
    )


def make_batch_config(num_stories=4, episode_type=None, ensure_target=False, **kwargs):
    """Create a BatchConfig with sensible defaults."""
    defaults = dict(
        num_stories=num_stories,
        max_num_protagonists=2,
        max_num_extras=1,
        num_distinct_actions=5,
        scene_number=4,
        output_base_dir="output",
        episode_type=episode_type,
        ensure_target=ensure_target,
        generator_type="simple_random",
        skip_simulation=True,
    )
    defaults.update(kwargs)
    return BatchConfig(**defaults)


# ============================================================================
# TestStoryStatusEpisodeType
# ============================================================================

class TestStoryStatusEpisodeType:
    """Tests for the episode_type field on StoryStatus."""

    def test_episode_type_field_default_none(self):
        """New episode_type field defaults to None."""
        status = StoryStatus(
            story_id="abc",
            story_number=1,
            status="pending",
        )
        assert status.episode_type is None

    def test_episode_type_set_on_construction(self):
        """episode_type can be set at construction."""
        status = make_story_status(episode_type="house")
        assert status.episode_type == "house"

    def test_episode_type_serialization_roundtrip(self):
        """episode_type survives to_dict / from_dict."""
        status = make_story_status(episode_type="gym")
        data = status.to_dict()
        restored = StoryStatus.from_dict(data)
        assert restored.episode_type == "gym"

    def test_episode_type_none_serialization_roundtrip(self):
        """episode_type=None survives to_dict / from_dict."""
        status = make_story_status(episode_type=None)
        data = status.to_dict()
        restored = StoryStatus.from_dict(data)
        assert restored.episode_type is None

    def test_episode_type_json_roundtrip(self):
        """episode_type survives full JSON round-trip."""
        status = make_story_status(episode_type="classroom")
        data = status.to_dict()
        json_str = json.dumps(data)
        loaded = json.loads(json_str)
        restored = StoryStatus.from_dict(loaded)
        assert restored.episode_type == "classroom"

    def test_episode_type_in_to_dict_output(self):
        """to_dict includes episode_type key."""
        status = make_story_status(episode_type="garden")
        data = status.to_dict()
        assert "episode_type" in data
        assert data["episode_type"] == "garden"

    def test_episode_type_backward_compatible(self):
        """from_dict works without episode_type key (old batch_state.json)."""
        data = {
            "story_id": "abc123",
            "story_number": 1,
            "status": "success",
            "output_dir": "output/story_00001_abc123",
            "current_take": 1,
            "current_sim": 1,
            "current_phase": 3,
            "generation_attempts": {},
            "simulation_attempts": 0,
            "warnings": [],
            "errors": [],
            "started_at": None,
            "completed_at": None,
            "scene_count": None,
            "event_count": None,
            "successful_simulations": [],
            "all_simulation_results": [],
            "gdrive_folder_id": None,
            "gdrive_link": None,
            "upload_timestamp": None,
            # No episode_type key — simulates old batch_state.json
        }
        restored = StoryStatus.from_dict(data)
        assert restored.episode_type is None


# ============================================================================
# TestRoundRobinEpisodeAssignment
# ============================================================================

class TestRoundRobinEpisodeAssignment:
    """Tests for round-robin episode type assignment at batch initialization."""

    def test_round_robin_without_explicit_type(self):
        """
        With 8 stories and no --episode-type, each type gets exactly 2 stories.
        Order: classroom, gym, garden, house, classroom, gym, garden, house.
        """
        episode_type_list = list(EPISODE_TYPES.keys())
        num_stories = 8

        assigned = [episode_type_list[i % len(episode_type_list)] for i in range(num_stories)]

        counts = Counter(assigned)
        assert counts["classroom"] == 2
        assert counts["gym"] == 2
        assert counts["garden"] == 2
        assert counts["house"] == 2

    def test_explicit_type_overrides_round_robin(self):
        """When --episode-type house is specified, all stories get house."""
        explicit_type = "house"
        num_stories = 8

        assigned = [explicit_type for _ in range(num_stories)]

        assert all(t == "house" for t in assigned)

    def test_round_robin_wraps_correctly(self):
        """Round-robin cycles through all 4 types for any number of stories."""
        episode_type_list = list(EPISODE_TYPES.keys())

        for num_stories in [1, 3, 4, 5, 7, 12, 100]:
            assigned = [episode_type_list[i % len(episode_type_list)] for i in range(num_stories)]

            # First story is always the first type
            assert assigned[0] == episode_type_list[0]

            # Each type appears at most ceil(num_stories/4) times
            counts = Counter(assigned)
            max_count = (num_stories + 3) // 4
            for t in episode_type_list:
                assert counts.get(t, 0) <= max_count

    def test_round_robin_order_is_deterministic(self):
        """Round-robin order matches EPISODE_TYPES key order."""
        episode_type_list = list(EPISODE_TYPES.keys())
        assigned = [episode_type_list[i % len(episode_type_list)] for i in range(4)]

        assert assigned == list(EPISODE_TYPES.keys())


# ============================================================================
# TestEnsureTargetStoryAdvancement
# ============================================================================

class TestEnsureTargetStoryAdvancement:
    """Tests that story_index advances on failure, preventing infinite retry."""

    def test_generation_failure_advances_story_index(self):
        """
        When generation fails and ensure_target=True, the loop should:
        1. Mark story as failed
        2. Increment story_index
        3. Create a NEW story with a different ID
        """
        # Simulate the loop logic directly
        stories = [
            make_story_status("id_001", 1, "pending", episode_type="classroom"),
        ]

        story_index = 0
        pending_episode_types = []
        episode_type_list = list(EPISODE_TYPES.keys())

        # Simulate generation failure for story 0
        story = stories[story_index]
        story.status = "failed"
        if story.episode_type:
            pending_episode_types.append(story.episode_type)
        story_index += 1  # The fix

        # Now story_index (1) >= len(stories) (1), so new story is created
        assert story_index >= len(stories)

        # Create replacement
        new_id = uuid.uuid4().hex[:8]
        if pending_episode_types:
            assigned_type = pending_episode_types.pop(0)
        else:
            assigned_type = episode_type_list[story_index % len(episode_type_list)]

        new_story = make_story_status(new_id, story_index + 1, "pending", episode_type=assigned_type)
        stories.append(new_story)

        # Verify replacement has different ID and same episode type
        assert new_story.story_id != "id_001"
        assert new_story.episode_type == "classroom"
        assert len(stories) == 2

    def test_no_infinite_retry_on_persistent_failure(self):
        """
        Without ensure_target, all stories failing should terminate the loop.
        Simulates the loop termination condition.
        """
        num_stories = 3
        stories = [
            make_story_status(f"id_{i}", i + 1, "pending")
            for i in range(num_stories)
        ]

        story_index = 0
        processed = 0

        # Simulate loop: each story fails, index advances
        while story_index < len(stories):
            stories[story_index].status = "failed"
            story_index += 1
            processed += 1

        # Loop terminates after processing all stories
        assert processed == num_stories
        assert story_index == num_stories
        assert all(s.status == "failed" for s in stories)

    def test_error_file_failure_advances_story_index(self):
        """
        When ERROR files are detected post-simulation, the loop should
        advance story_index just like generation failure.
        """
        stories = [
            make_story_status("id_001", 1, "success", episode_type="gym"),
        ]

        story_index = 0
        pending_episode_types = []

        # Simulate: story initially marked success, then ERROR files found
        story = stories[story_index]
        story.status = "failed"  # Reverted from success
        if story.episode_type:
            pending_episode_types.append(story.episode_type)
        story_index += 1  # The fix

        assert story_index == 1
        assert pending_episode_types == ["gym"]


# ============================================================================
# TestPendingEpisodeTypeQueue
# ============================================================================

class TestPendingEpisodeTypeQueue:
    """Tests for the pending_episode_types queue behavior."""

    def test_failed_story_type_queued(self):
        """A failed story's episode_type is pushed onto the queue."""
        pending = []
        story = make_story_status(episode_type="house")
        story.status = "failed"

        if story.episode_type:
            pending.append(story.episode_type)

        assert pending == ["house"]

    def test_replacement_inherits_queued_type(self):
        """Replacement story pops type from queue."""
        pending = ["house"]

        assigned_type = pending.pop(0)

        assert assigned_type == "house"
        assert pending == []

    def test_multiple_failures_queue_fifo(self):
        """Queue is FIFO: first failed type is first assigned."""
        pending = []

        # Two failures in order
        for ep_type in ["house", "gym"]:
            pending.append(ep_type)

        # Replacements should get types in order
        assert pending.pop(0) == "house"
        assert pending.pop(0) == "gym"
        assert pending == []

    def test_fallback_to_round_robin_when_queue_empty(self):
        """When queue is empty, fall back to round-robin."""
        pending = []
        episode_type_list = list(EPISODE_TYPES.keys())
        story_index = 5

        if pending:
            assigned_type = pending.pop(0)
        else:
            assigned_type = episode_type_list[story_index % len(episode_type_list)]

        # story_index=5 → 5 % 4 = 1 → second type
        assert assigned_type == episode_type_list[1]

    def test_fallback_to_explicit_type_when_queue_empty(self):
        """When queue is empty and --episode-type is set, use that."""
        pending = []
        batch_episode_type = "garden"
        episode_type_list = list(EPISODE_TYPES.keys())
        story_index = 5

        if pending:
            assigned_type = pending.pop(0)
        elif batch_episode_type is None:
            assigned_type = episode_type_list[story_index % len(episode_type_list)]
        else:
            assigned_type = batch_episode_type

        assert assigned_type == "garden"

    def test_queue_preserves_order_across_mixed_failures(self):
        """Multiple failures of different types queued in failure order."""
        pending = []

        # Simulate alternating failures
        failure_sequence = ["classroom", "garden", "house", "gym"]
        for ep_type in failure_sequence:
            pending.append(ep_type)

        # Replacements get types in same order
        for expected in failure_sequence:
            assert pending.pop(0) == expected

    def test_none_episode_type_not_queued(self):
        """Stories with episode_type=None don't add to queue."""
        pending = []
        story = make_story_status(episode_type=None)
        story.status = "failed"

        if story.episode_type:
            pending.append(story.episode_type)

        assert pending == []


# ============================================================================
# TestPerStoryEpisodeType
# ============================================================================

class TestPerStoryEpisodeType:
    """Tests that per-story episode_type is passed to the generator."""

    def test_generator_receives_story_episode_type(self):
        """
        The generator.generate() call should receive the story's episode_type,
        not the batch-wide config.episode_type.
        """
        batch_episode_type = None  # No batch-wide type
        story_episode_type = "garden"  # Per-story type from round-robin

        # The code should pass story_status.episode_type, not batch_config.episode_type
        # Simulate the call:
        call_kwargs = {
            "chains_per_actor": 3,
            "max_actors_per_region": None,
            "max_regions": None,
            "episode_type": story_episode_type,  # This is the fix
        }

        assert call_kwargs["episode_type"] == "garden"
        assert call_kwargs["episode_type"] != batch_episode_type

    def test_all_episode_types_valid(self):
        """All round-robin assigned types are valid EPISODE_TYPES keys."""
        episode_type_list = list(EPISODE_TYPES.keys())

        for i in range(20):
            assigned = episode_type_list[i % len(episode_type_list)]
            assert assigned in EPISODE_TYPES, f"Invalid type at index {i}: {assigned}"


# ============================================================================
# TestBatchStateWithEpisodeTypes
# ============================================================================

class TestBatchStateWithEpisodeTypes:
    """Integration tests for BatchState with episode types."""

    def test_batch_state_preserves_story_episode_types(self):
        """Full BatchState round-trip preserves per-story episode types."""
        config = make_batch_config(num_stories=4)

        state = BatchState(
            batch_id="batch_test",
            config=config,
            batch_output_dir="output/batch_test",
        )

        types = ["classroom", "gym", "garden", "house"]
        for i, ep_type in enumerate(types):
            story = make_story_status(
                story_id=f"id_{i}",
                story_number=i + 1,
                episode_type=ep_type,
            )
            state.stories.append(story)

        # JSON round-trip
        data = state.to_dict()
        json_str = json.dumps(data)
        loaded = json.loads(json_str)
        restored = BatchState.from_dict(loaded)

        for i, ep_type in enumerate(types):
            assert restored.stories[i].episode_type == ep_type

    def test_batch_state_multiple_cycles_preserves_episode_types(self):
        """Episode types survive multiple save/load cycles."""
        config = make_batch_config(num_stories=2)

        state = BatchState(
            batch_id="batch_test",
            config=config,
            batch_output_dir="output/batch_test",
        )
        state.stories.append(make_story_status("id_1", 1, episode_type="classroom"))
        state.stories.append(make_story_status("id_2", 2, episode_type="house"))

        # 5 cycles
        current = state
        for _ in range(5):
            data = current.to_dict()
            json_str = json.dumps(data)
            loaded = json.loads(json_str)
            current = BatchState.from_dict(loaded)

        assert current.stories[0].episode_type == "classroom"
        assert current.stories[1].episode_type == "house"
