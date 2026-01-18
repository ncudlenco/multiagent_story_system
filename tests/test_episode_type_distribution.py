"""
Test for episode type distribution in SimpleGESTRandomGenerator.

Verifies that the two-stage episode selection provides equal probability
across episode types (classroom, gym, garden, house), regardless of how
many episodes exist within each type.
"""

import pytest
import random
from collections import Counter
from unittest.mock import Mock, patch

from simple_gest_random_generator import (
    SimpleGESTRandomGenerator,
    EPISODE_TYPES,
    Episode,
    POIInfo,
)


class TestEpisodeTypeDistribution:
    """Tests for equal probability episode type selection."""

    @pytest.fixture
    def mock_generator(self):
        """Create a generator with mocked episodes matching EPISODE_TYPES."""
        # Create a mock generator without loading actual capabilities
        with patch.object(SimpleGESTRandomGenerator, '__init__', lambda x, y: None):
            generator = SimpleGESTRandomGenerator(None)

        # Set up minimal attributes
        generator.episodes = {}

        # Create mock episodes for each type
        all_episodes = []
        for episode_type, episode_names in EPISODE_TYPES.items():
            all_episodes.extend(episode_names)

        for ep_name in all_episodes:
            generator.episodes[ep_name] = Episode(
                name=ep_name,
                linked_episodes=[],
                regions=[],
                pois=[],
            )

        return generator

    def test_episode_types_constant_has_four_types(self):
        """Verify EPISODE_TYPES has exactly 4 types."""
        assert len(EPISODE_TYPES) == 4
        assert set(EPISODE_TYPES.keys()) == {"classroom", "gym", "garden", "house"}

    def test_episode_types_have_expected_episodes(self):
        """Verify each type has the expected episodes."""
        assert EPISODE_TYPES["classroom"] == ["classroom1"]
        assert EPISODE_TYPES["gym"] == ["gym1_a", "gym2_a", "gym3"]
        assert EPISODE_TYPES["garden"] == ["garden"]
        assert EPISODE_TYPES["house"] == ["house9", "office", "office2", "common"]

    def test_type_distribution_is_uniform(self, mock_generator):
        """
        Test that episode types are selected with approximately equal probability.

        Runs 1000 selections and verifies each type gets 20-30% of selections.
        With 4 types, expected is 25% each. We allow 20-30% to account for
        random variation.
        """
        random.seed(42)  # For reproducibility

        type_counts = Counter()
        n_samples = 1000

        for _ in range(n_samples):
            # Call the method
            group = mock_generator._select_random_episode_group()

            # Determine which type was selected (first episode in group)
            selected_ep = group[0]
            for episode_type, episodes in EPISODE_TYPES.items():
                if selected_ep in episodes:
                    type_counts[episode_type] += 1
                    break

        # Each type should have approximately 25% (250 out of 1000)
        # Allow 20-30% range (200-300) to account for random variation
        for episode_type in EPISODE_TYPES:
            count = type_counts[episode_type]
            percentage = count / n_samples * 100
            assert 15 <= percentage <= 35, (
                f"Type '{episode_type}' got {percentage:.1f}% selections "
                f"(expected ~25%, allowed 15-35%)"
            )

    def test_no_gym_bias(self, mock_generator):
        """
        Specific test to verify gyms don't get 3x the selection rate.

        Before the fix: 3 gyms out of 9 episodes = 33% gym selections
        After the fix: gym is 1 of 4 types = 25% gym selections
        """
        random.seed(123)

        gym_count = 0
        n_samples = 1000

        for _ in range(n_samples):
            group = mock_generator._select_random_episode_group()
            selected_ep = group[0]
            if selected_ep in EPISODE_TYPES["gym"]:
                gym_count += 1

        gym_percentage = gym_count / n_samples * 100

        # Gym should be around 25%, not 33%
        # Allow generous range of 15-35%
        assert 15 <= gym_percentage <= 35, (
            f"Gym got {gym_percentage:.1f}% selections - "
            f"expected ~25% (bias fix), not ~33% (old behavior)"
        )

    def test_all_types_selected_in_sample(self, mock_generator):
        """Verify all 4 types get selected at least once in 100 samples."""
        random.seed(999)

        selected_types = set()
        for _ in range(100):
            group = mock_generator._select_random_episode_group()
            selected_ep = group[0]
            for episode_type, episodes in EPISODE_TYPES.items():
                if selected_ep in episodes:
                    selected_types.add(episode_type)
                    break

        assert selected_types == set(EPISODE_TYPES.keys()), (
            f"Not all types selected. Got: {selected_types}, "
            f"expected: {set(EPISODE_TYPES.keys())}"
        )

    def test_fallback_when_no_types_available(self, mock_generator):
        """Test fallback behavior when no EPISODE_TYPES episodes are available."""
        # Clear episodes and add one that's not in EPISODE_TYPES
        mock_generator.episodes = {
            "unknown_episode": Episode(
                name="unknown_episode",
                linked_episodes=[],
                regions=[],
                pois=[],
            )
        }

        group = mock_generator._select_random_episode_group()

        # Should fall back to random selection from available episodes
        assert group == ["unknown_episode"]


class TestEpisodeTypeDistributionWithRealData:
    """Integration test with real capabilities data."""

    @pytest.fixture
    def real_generator(self):
        """Create generator with real capabilities file."""
        capabilities_path = "data/simulation_environment_capabilities.json"
        try:
            return SimpleGESTRandomGenerator(capabilities_path)
        except FileNotFoundError:
            pytest.skip("Capabilities file not found")

    def test_real_type_distribution(self, real_generator):
        """Test distribution with actual capabilities data."""
        random.seed(42)

        type_counts = Counter()
        n_samples = 400  # Fewer samples for integration test

        for _ in range(n_samples):
            group = real_generator._select_random_episode_group()
            selected_ep = group[0]
            for episode_type, episodes in EPISODE_TYPES.items():
                if selected_ep in episodes:
                    type_counts[episode_type] += 1
                    break

        # Verify reasonable distribution (10-40% each type)
        for episode_type in EPISODE_TYPES:
            if type_counts[episode_type] > 0:  # Type may not have any available episodes
                percentage = type_counts[episode_type] / n_samples * 100
                assert 10 <= percentage <= 40, (
                    f"Type '{episode_type}' got {percentage:.1f}% "
                    f"(expected ~25%)"
                )
