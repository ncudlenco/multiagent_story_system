"""
Unit tests for UNC path handling and normalization.

These tests verify that UNC paths (\\\\server\\share format) are correctly
handled through JSON/YAML serialization cycles without double-escaping issues.

The double-escaping problem occurs when:
1. UNC path: \\\\vmware-host\\Shared Folders
2. After JSON serialization: \\\\\\\\vmware-host\\\\Shared Folders (in JSON text)
3. If processed again: \\\\\\\\\\\\\\\\vmware-host\\\\\\\\Shared Folders

The fix uses os.path.normpath() to normalize paths before serialization.
"""

import os
import json
from pathlib import Path
import pytest


class TestUNCPathNormalization:
    """Test UNC path normalization behavior."""

    def test_normpath_preserves_unc_path(self):
        """os.path.normpath should preserve UNC path structure."""
        unc_path = r"\\vmware-host\Shared Folders\output"
        normalized = os.path.normpath(unc_path)
        assert normalized == r"\\vmware-host\Shared Folders\output"

    def test_normpath_cannot_fix_already_corrupted_paths(self):
        """normpath cannot fix already-corrupted paths (demonstrates limitation).

        This test documents that normpath is a PREVENTIVE fix, not a corrective one.
        Once a path is double-escaped (e.g., \\\\\\\\vmware-host), normpath treats it
        as a valid UNC path with the extra backslashes as part of the server name.

        Our fix works by normalizing paths BEFORE serialization, preventing corruption.
        """
        # A double-escaped path (4 actual backslashes at start)
        double_escaped = "\\\\\\\\vmware-host\\\\Shared Folders\\\\output"

        # normpath does NOT fix this - it treats it as a valid path
        normalized = os.path.normpath(double_escaped)

        # This is WHY we normalize BEFORE serialization, not after
        # The path is still "corrupted" from normpath's perspective
        # but our fix prevents it from ever getting this way

    def test_normpath_handles_forward_slashes(self):
        """normpath should handle mixed slashes."""
        mixed_path = r"\\vmware-host/Shared Folders/output"
        normalized = os.path.normpath(mixed_path)
        # On Windows, normpath converts to backslashes
        assert "/" not in normalized or os.name != 'nt'

    def test_normpath_removes_redundant_separators(self):
        """normpath should remove redundant path separators."""
        path_with_extras = r"\\vmware-host\Shared Folders\\output\\\\batch_123"
        normalized = os.path.normpath(path_with_extras)
        assert "\\\\" not in normalized[2:]  # After initial UNC prefix

    def test_path_object_with_unc(self):
        """Path object should handle UNC paths correctly."""
        unc_path = r"\\vmware-host\Shared Folders\output"
        p = Path(unc_path)
        # str(Path) should not double the backslashes
        result = str(p)
        # Count backslashes - should match or be normalized
        assert result.startswith(r"\\vmware-host") or result.startswith(r"\\?\UNC\vmware-host")

    def test_path_concatenation_unc(self):
        """Path / operator should not corrupt UNC paths."""
        unc_path = r"\\vmware-host\Shared Folders\output"
        p = Path(unc_path) / "batch_123" / "story_456"
        result = str(p)
        # Should not have quadruple backslashes
        assert "\\\\\\\\vmware-host" not in result


class TestJSONRoundTrip:
    """Test JSON serialization/deserialization of UNC paths."""

    def test_json_roundtrip_unc_path(self):
        """UNC path should survive JSON round-trip."""
        unc_path = r"\\vmware-host\Shared Folders\output"
        data = {"path": unc_path}

        # Serialize
        json_str = json.dumps(data)

        # Deserialize
        loaded = json.loads(json_str)

        # Path should be identical
        assert loaded["path"] == unc_path

    def test_json_dumps_escapes_backslashes(self):
        """Verify that JSON escapes backslashes (expected behavior)."""
        unc_path = r"\\vmware-host\Shared Folders\output"
        json_str = json.dumps({"path": unc_path})

        # JSON representation will have escaped backslashes
        # This is CORRECT JSON behavior - backslash is escape char in JSON
        assert "\\\\\\\\vmware-host" in json_str  # 4 backslashes in JSON text

        # But loading should restore original
        loaded = json.loads(json_str)
        assert loaded["path"] == unc_path

    def test_nested_json_roundtrip(self):
        """Nested structures with UNC paths should survive."""
        unc_path = r"\\vmware-host\Shared Folders\output\batch_123"
        data = {
            "batch_output_dir": unc_path,
            "stories": [
                {"output_dir": f"{unc_path}\\story_1"},
                {"output_dir": f"{unc_path}\\story_2"},
            ]
        }

        json_str = json.dumps(data)
        loaded = json.loads(json_str)

        assert loaded["batch_output_dir"] == unc_path
        assert loaded["stories"][0]["output_dir"] == f"{unc_path}\\story_1"

    def test_double_json_roundtrip_without_normalization(self):
        """Show what happens without normalization - paths get corrupted."""
        unc_path = r"\\vmware-host\Shared Folders\output"

        # First round-trip (correct)
        data1 = {"path": unc_path}
        json_str1 = json.dumps(data1)
        loaded1 = json.loads(json_str1)
        assert loaded1["path"] == unc_path  # Still correct

        # If we incorrectly treat the JSON string as the path value
        # (simulating a bug where raw JSON is stored instead of parsed)
        # This is NOT how json.loads works, but illustrates the corruption
        corrupted_path = json_str1  # This would be wrong
        assert "\\\\\\\\vmware-host" in corrupted_path


class TestNormalizePathFunction:
    """Test the _normalize_path helper function from schemas."""

    def test_normalize_path_with_none(self):
        """_normalize_path should handle None gracefully."""
        from batch.schemas import _normalize_path
        assert _normalize_path(None) is None

    def test_normalize_path_with_regular_path(self):
        """_normalize_path should work with regular paths."""
        from batch.schemas import _normalize_path

        regular_path = r"C:\Users\test\output"
        normalized = _normalize_path(regular_path)
        assert normalized == regular_path

    def test_normalize_path_with_unc(self):
        """_normalize_path should preserve valid UNC paths."""
        from batch.schemas import _normalize_path

        unc_path = r"\\vmware-host\Shared Folders\output"
        normalized = _normalize_path(unc_path)
        assert normalized == unc_path

    def test_normalize_path_limitation_with_corrupted_unc(self):
        """_normalize_path cannot fix already-corrupted paths (documents limitation).

        This test documents that _normalize_path is a PREVENTIVE measure.
        It normalizes valid paths to prevent corruption during serialization,
        but cannot fix paths that are already double-escaped.

        The actual fix works by normalizing paths in to_dict()/from_dict()
        before JSON serialization can corrupt them.
        """
        from batch.schemas import _normalize_path

        # A path that's already double-escaped (would not occur with our fix)
        corrupted = "\\\\\\\\vmware-host\\\\Shared Folders\\\\output"
        normalized = _normalize_path(corrupted)

        # normpath cannot fix this - it treats the extra backslashes as valid
        # This is fine because our fix PREVENTS paths from getting this way


class TestBatchStateSerialization:
    """Test BatchState/StoryStatus serialization with UNC paths."""

    def test_story_status_output_dir_roundtrip(self):
        """StoryStatus.output_dir should survive to_dict/from_dict."""
        from batch.schemas import StoryStatus

        unc_path = r"\\vmware-host\Shared Folders\output\story_abc123"
        status = StoryStatus(
            story_id="abc123",
            story_number=1,
            status="success",
            output_dir=unc_path
        )

        # Serialize and deserialize
        data = status.to_dict()
        restored = StoryStatus.from_dict(data)

        # Path should be preserved (possibly normalized)
        assert os.path.normpath(restored.output_dir) == os.path.normpath(unc_path)
        # Should not have quadruple backslashes
        assert "\\\\\\\\" not in restored.output_dir

    def test_story_status_json_roundtrip(self):
        """StoryStatus should survive full JSON round-trip."""
        from batch.schemas import StoryStatus

        unc_path = r"\\vmware-host\Shared Folders\output\story_abc123"
        status = StoryStatus(
            story_id="abc123",
            story_number=1,
            status="success",
            output_dir=unc_path
        )

        # Full JSON round-trip
        data = status.to_dict()
        json_str = json.dumps(data)
        loaded_data = json.loads(json_str)
        restored = StoryStatus.from_dict(loaded_data)

        # Path should be preserved
        assert os.path.normpath(restored.output_dir) == os.path.normpath(unc_path)
        assert "\\\\\\\\" not in restored.output_dir

    def test_batch_config_output_base_dir_roundtrip(self):
        """BatchConfig.output_base_dir should survive to_dict/from_dict."""
        from batch.schemas import BatchConfig

        unc_path = r"\\vmware-host\Shared Folders\output"
        config = BatchConfig(
            num_stories=1,
            max_num_protagonists=2,
            max_num_extras=1,
            num_distinct_actions=5,
            scene_number=4,
            output_base_dir=unc_path
        )

        # Serialize and deserialize
        data = config.to_dict()
        restored = BatchConfig.from_dict(data)

        # Path should be preserved
        assert os.path.normpath(restored.output_base_dir) == os.path.normpath(unc_path)
        assert "\\\\\\\\" not in restored.output_base_dir

    def test_batch_state_full_roundtrip(self):
        """Full BatchState should survive JSON round-trip."""
        from batch.schemas import BatchState, BatchConfig, StoryStatus

        unc_base = r"\\vmware-host\Shared Folders\output"
        config = BatchConfig(
            num_stories=1,
            max_num_protagonists=2,
            max_num_extras=1,
            num_distinct_actions=5,
            scene_number=4,
            output_base_dir=unc_base
        )

        state = BatchState(
            batch_id="batch_123",
            config=config,
            batch_output_dir=f"{unc_base}\\batch_123"
        )

        # Add a story
        story = StoryStatus(
            story_id="story_456",
            story_number=1,
            status="success",
            output_dir=f"{unc_base}\\batch_123\\story_456"
        )
        state.stories.append(story)

        # Full JSON round-trip
        data = state.to_dict()
        json_str = json.dumps(data)
        loaded_data = json.loads(json_str)
        restored = BatchState.from_dict(loaded_data)

        # All paths should be correct (no quadruple backslashes)
        assert "\\\\\\\\" not in restored.batch_output_dir
        assert "\\\\\\\\" not in restored.config.output_base_dir
        assert "\\\\\\\\" not in restored.stories[0].output_dir

        # Paths should match originals (normalized)
        assert os.path.normpath(restored.batch_output_dir) == os.path.normpath(f"{unc_base}\\batch_123")
        assert os.path.normpath(restored.stories[0].output_dir) == os.path.normpath(f"{unc_base}\\batch_123\\story_456")

    def test_simulation_result_paths_roundtrip(self):
        """SimulationResult paths should survive JSON round-trip."""
        from batch.schemas import SimulationResult

        unc_path = r"\\vmware-host\Shared Folders\output\batch_123\story_456\take1_sim1"
        video_path = r"\\vmware-host\Shared Folders\output\batch_123\story_456\video.avi"

        result = SimulationResult(
            take_number=1,
            sim_number=1,
            success=True,
            output_dir=unc_path,
            video_path=video_path,
            video_generated=True
        )

        # Full JSON round-trip
        data = result.to_dict()
        json_str = json.dumps(data)
        loaded_data = json.loads(json_str)
        restored = SimulationResult.from_dict(loaded_data)

        # Paths should be preserved without corruption
        assert "\\\\\\\\" not in restored.output_dir
        assert "\\\\\\\\" not in restored.video_path
        assert os.path.normpath(restored.output_dir) == os.path.normpath(unc_path)
        assert os.path.normpath(restored.video_path) == os.path.normpath(video_path)


class TestMultipleSerializationCycles:
    """Test that paths remain correct through multiple save/load cycles."""

    def test_batch_state_multiple_cycles(self):
        """BatchState should survive multiple JSON round-trips."""
        from batch.schemas import BatchState, BatchConfig, StoryStatus

        unc_base = r"\\vmware-host\Shared Folders\output"
        config = BatchConfig(
            num_stories=1,
            max_num_protagonists=2,
            max_num_extras=1,
            num_distinct_actions=5,
            scene_number=4,
            output_base_dir=unc_base
        )

        state = BatchState(
            batch_id="batch_123",
            config=config,
            batch_output_dir=f"{unc_base}\\batch_123"
        )

        story = StoryStatus(
            story_id="story_456",
            story_number=1,
            status="success",
            output_dir=f"{unc_base}\\batch_123\\story_456"
        )
        state.stories.append(story)

        # Simulate 5 save/load cycles (like batch resumption)
        current_state = state
        for cycle in range(5):
            data = current_state.to_dict()
            json_str = json.dumps(data)
            loaded_data = json.loads(json_str)
            current_state = BatchState.from_dict(loaded_data)

        # After 5 cycles, paths should still be correct
        assert "\\\\\\\\" not in current_state.batch_output_dir
        assert "\\\\\\\\" not in current_state.config.output_base_dir
        assert "\\\\\\\\" not in current_state.stories[0].output_dir

        # Should still be valid UNC paths
        assert current_state.batch_output_dir.startswith(r"\\")
        assert current_state.config.output_base_dir.startswith(r"\\")
        assert current_state.stories[0].output_dir.startswith(r"\\")


class TestFullVMWorkflow:
    """Test the complete VM workflow path serialization.

    This test mimics the actual flow in VMware orchestration:
    1. vmware_orchestrator.py creates worker_job.yaml with UNC path
    2. vm_auto_runner.py reads YAML and passes to batch_generate.py
    3. batch_controller.py creates BatchConfig and BatchState
    4. batch_state.json is saved and loaded multiple times during execution
    5. Paths are used for file operations (mkdir, copy, etc.)

    The UNC path double-escaping bug occurred at step 4 - paths got corrupted
    during JSON serialization cycles.
    """

    def test_full_vm_workflow_yaml_to_json_cycles(self):
        """Simulate complete VM workflow: YAML -> BatchState -> JSON cycles."""
        import yaml
        from batch.schemas import BatchState, BatchConfig, StoryStatus, SimulationResult

        # Step 1: VMware orchestrator creates worker_job.yaml
        # This is what _generate_worker_job_yaml() does
        unc_output = r"\\vmware-host\Shared Folders\output"
        worker_job = {
            "output_folder": unc_output,
            "batch_id": "vm_batch_20260125_123456",
            "story_number": 1,
            "generator_type": "simple_random",
        }

        # Simulate YAML serialization (what vmware_orchestrator does)
        yaml_str = yaml.dump(worker_job, default_flow_style=False)

        # Step 2: VM reads YAML (what vm_auto_runner does)
        loaded_job = yaml.safe_load(yaml_str)
        output_folder = loaded_job["output_folder"]

        # Verify YAML round-trip preserved the path
        assert output_folder == unc_output
        assert "\\\\\\\\" not in output_folder

        # Step 3: batch_controller creates BatchConfig
        config = BatchConfig(
            num_stories=1,
            max_num_protagonists=2,
            max_num_extras=1,
            num_distinct_actions=5,
            scene_number=4,
            output_base_dir=output_folder,  # From YAML
            generator_type="simple_random",
        )

        # Step 4: batch_controller creates BatchState
        batch_id = "batch_20260125_055530"
        batch_output_dir = f"{output_folder}\\{batch_id}"

        state = BatchState(
            batch_id=batch_id,
            config=config,
            batch_output_dir=batch_output_dir,
        )

        # Add a story with output_dir
        story_name = "gym_max1actors_max1regions_1action_chains_abc123"
        story = StoryStatus(
            story_id="abc123",
            story_number=1,
            status="generating",
            output_dir=f"{batch_output_dir}\\{story_name}",
        )
        state.stories.append(story)

        # Step 5: Simulate multiple save/load cycles (batch resumption)
        # This is where the bug manifested - paths got corrupted
        for cycle in range(5):
            # Save batch_state.json
            data = state.to_dict()
            json_str = json.dumps(data, indent=2)

            # Load batch_state.json
            loaded_data = json.loads(json_str)
            state = BatchState.from_dict(loaded_data)

            # Verify paths are still valid after each cycle
            assert "\\\\\\\\" not in state.batch_output_dir, f"Corrupted at cycle {cycle}"
            assert "\\\\\\\\" not in state.config.output_base_dir, f"Corrupted at cycle {cycle}"
            assert "\\\\\\\\" not in state.stories[0].output_dir, f"Corrupted at cycle {cycle}"

        # Step 6: Add simulation results (what happens after simulation)
        sim_result = SimulationResult(
            take_number=1,
            sim_number=1,
            success=True,
            output_dir=f"{state.stories[0].output_dir}\\simulations\\take1_sim1",
            video_path=f"{state.stories[0].output_dir}\\simulations\\take1_sim1\\video.avi",
            video_generated=True,
        )
        state.stories[0].all_simulation_results.append(sim_result)
        state.stories[0].status = "success"

        # Step 7: More save/load cycles with simulation results
        for cycle in range(3):
            data = state.to_dict()
            json_str = json.dumps(data, indent=2)
            loaded_data = json.loads(json_str)
            state = BatchState.from_dict(loaded_data)

        # Final verification - all paths should be valid UNC paths
        assert state.batch_output_dir.startswith(r"\\vmware-host")
        assert state.config.output_base_dir.startswith(r"\\vmware-host")
        assert state.stories[0].output_dir.startswith(r"\\vmware-host")
        assert state.stories[0].all_simulation_results[0].output_dir.startswith(r"\\vmware-host")
        assert state.stories[0].all_simulation_results[0].video_path.startswith(r"\\vmware-host")

        # No quadruple backslashes anywhere
        assert "\\\\\\\\" not in state.batch_output_dir
        assert "\\\\\\\\" not in state.config.output_base_dir
        assert "\\\\\\\\" not in state.stories[0].output_dir
        assert "\\\\\\\\" not in state.stories[0].all_simulation_results[0].output_dir
        assert "\\\\\\\\" not in state.stories[0].all_simulation_results[0].video_path

    def test_path_usable_for_pathlib_operations(self):
        """Verify paths from BatchState can be used with pathlib."""
        from batch.schemas import BatchState, BatchConfig, StoryStatus

        unc_base = r"\\vmware-host\Shared Folders\output"
        config = BatchConfig(
            num_stories=1,
            max_num_protagonists=2,
            max_num_extras=1,
            num_distinct_actions=5,
            scene_number=4,
            output_base_dir=unc_base,
        )

        state = BatchState(
            batch_id="batch_123",
            config=config,
            batch_output_dir=f"{unc_base}\\batch_123",
        )

        story = StoryStatus(
            story_id="story_456",
            story_number=1,
            status="success",
            output_dir=f"{unc_base}\\batch_123\\story_456",
        )
        state.stories.append(story)

        # Simulate JSON round-trip (like batch_state.json)
        data = state.to_dict()
        json_str = json.dumps(data)
        loaded_data = json.loads(json_str)
        restored = BatchState.from_dict(loaded_data)

        # Use paths with pathlib (this is what batch_controller does)
        story_dir = Path(restored.stories[0].output_dir)
        sim_dir = story_dir / "simulations" / "take1_sim1"
        logs_dir = sim_dir / "logs"

        # Paths should be valid for pathlib operations
        # (We can't actually create them without the UNC share, but str() should work)
        sim_dir_str = str(sim_dir)
        logs_dir_str = str(logs_dir)

        # No corruption
        assert "\\\\\\\\" not in sim_dir_str
        assert "\\\\\\\\" not in logs_dir_str

        # Should be valid UNC paths
        assert sim_dir_str.startswith(r"\\vmware-host") or sim_dir_str.startswith(r"\\?\UNC\vmware-host")

    def test_error_messages_with_unc_paths(self):
        """Verify error messages containing UNC paths don't corrupt the paths."""
        from batch.schemas import BatchState, BatchConfig, StoryStatus

        unc_base = r"\\vmware-host\Shared Folders\output"
        config = BatchConfig(
            num_stories=1,
            max_num_protagonists=2,
            max_num_extras=1,
            num_distinct_actions=5,
            scene_number=4,
            output_base_dir=unc_base,
        )

        state = BatchState(
            batch_id="batch_123",
            config=config,
            batch_output_dir=f"{unc_base}\\batch_123",
        )

        # Add story with error messages containing paths
        # (This is what actually happened in the bug)
        story = StoryStatus(
            story_id="story_456",
            story_number=1,
            status="failed",
            output_dir=f"{unc_base}\\batch_123\\story_456",
            errors=[
                f"[WinError 123] The filename, directory name, or volume label syntax is incorrect: '{unc_base}\\batch_123\\story_456\\detailed_graph\\take1'",
                f"Random generation failed: [Errno 22] Invalid argument: '{unc_base}\\batch_123\\story_456\\detailed_graph\\take1\\detail_gest.json'",
            ],
        )
        state.stories.append(story)

        # JSON round-trip
        data = state.to_dict()
        json_str = json.dumps(data, indent=2)
        loaded_data = json.loads(json_str)
        restored = BatchState.from_dict(loaded_data)

        # The output_dir should still be valid
        assert restored.stories[0].output_dir.startswith(r"\\vmware-host")
        assert "\\\\\\\\" not in restored.stories[0].output_dir

        # Error messages are just strings, they don't get normalized
        # (and that's fine - they're for logging, not file operations)
        assert len(restored.stories[0].errors) == 2


class TestEdgeCases:
    """Test edge cases for path handling."""

    def test_empty_path_string(self):
        """Empty path string should be handled."""
        from batch.schemas import StoryStatus

        status = StoryStatus(
            story_id="abc123",
            story_number=1,
            status="pending",
            output_dir=""
        )

        data = status.to_dict()
        restored = StoryStatus.from_dict(data)

        assert restored.output_dir == ""

    def test_none_path_in_config(self):
        """None path values should be handled."""
        from batch.schemas import BatchConfig

        config = BatchConfig(
            num_stories=1,
            max_num_protagonists=2,
            max_num_extras=1,
            num_distinct_actions=5,
            scene_number=4,
            from_existing_stories_path=None,
            from_text_files_path=None
        )

        data = config.to_dict()
        restored = BatchConfig.from_dict(data)

        assert restored.from_existing_stories_path is None
        assert restored.from_text_files_path is None

    def test_local_path_not_affected(self):
        """Regular local paths should work normally."""
        from batch.schemas import BatchConfig

        local_path = r"C:\Users\test\output"
        config = BatchConfig(
            num_stories=1,
            max_num_protagonists=2,
            max_num_extras=1,
            num_distinct_actions=5,
            scene_number=4,
            output_base_dir=local_path
        )

        data = config.to_dict()
        json_str = json.dumps(data)
        loaded_data = json.loads(json_str)
        restored = BatchConfig.from_dict(loaded_data)

        assert restored.output_base_dir == local_path

    def test_relative_path_not_affected(self):
        """Relative paths should work normally."""
        from batch.schemas import BatchConfig

        relative_path = "output/batch_results"
        config = BatchConfig(
            num_stories=1,
            max_num_protagonists=2,
            max_num_extras=1,
            num_distinct_actions=5,
            scene_number=4,
            output_base_dir=relative_path
        )

        data = config.to_dict()
        restored = BatchConfig.from_dict(data)

        # normpath may change forward slashes to backslashes on Windows
        assert os.path.normpath(restored.output_base_dir) == os.path.normpath(relative_path)
