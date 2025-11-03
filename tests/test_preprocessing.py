"""
Tests for preprocessing functionality.

Tests the preprocessing pipeline that transforms simulation_environment_capabilities.json
into optimized cache files using GPT-5.
"""

import pytest
import json
from pathlib import Path
from typing import Dict, Any

from core.config import Config
from utils.preprocess_capabilities import CapabilitiesPreprocessor
from schemas.preprocessing import (
    PlayerSkinsPreprocessingOutput,
    EpisodeSummariesOutput
)


# ============================================================================
# Fixtures
# ============================================================================

@pytest.fixture
def config():
    """Load test configuration."""
    return Config.load('config.yaml')


@pytest.fixture
def sample_capabilities():
    """Load sample game capabilities for testing."""
    config = Config.load('config.yaml')
    cap_path = Path(config.paths.simulation_environment_capabilities)

    if not cap_path.exists():
        pytest.skip(f"Game capabilities not found at {cap_path}")

    with open(cap_path, 'r') as f:
        data = json.load(f)

    # Handle list wrapping
    if isinstance(data, list):
        data = data[0] if data else {}

    return data


@pytest.fixture
def concept_cache(config):
    """Load concept cache if it exists."""
    cache_path = Path(config.paths.game_capabilities_concept)

    if not cache_path.exists():
        pytest.skip(f"Concept cache not found at {cache_path}. Run preprocessing first.")

    with open(cache_path, 'r') as f:
        return json.load(f)


@pytest.fixture
def full_cache(config):
    """Load full indexed cache if it exists."""
    cache_path = Path(config.paths.game_capabilities_full_indexed)

    if not cache_path.exists():
        pytest.skip(f"Full indexed cache not found at {cache_path}. Run preprocessing first.")

    with open(cache_path, 'r') as f:
        return json.load(f)


# ============================================================================
# Schema Validation Tests
# ============================================================================

class TestSchemaValidation:
    """Test Pydantic schema validation."""

    def test_player_skins_summary_schema(self, full_cache):
        """Test player skins summary matches schema."""
        summary_data = full_cache.get('player_skins_summary')
        assert summary_data is not None, "player_skins_summary missing from cache"

        # Validate structure
        assert 'total_count' in summary_data
        assert 'by_gender' in summary_data
        assert 'categories' in summary_data
        assert 'representative_examples' in summary_data

        # Validate counts
        assert summary_data['total_count'] == 249, "Should have 249 total skins"

        # Validate representative examples
        examples = summary_data['representative_examples']
        assert 10 <= len(examples) <= 15, "Should have 10-15 representative examples"

        for example in examples:
            assert 'id' in example
            assert 'description' in example
            assert 'tags' in example
            assert isinstance(example['tags'], list)

    def test_player_skins_categorized_schema(self, full_cache):
        """Test categorized skins match schema."""
        categorized = full_cache.get('player_skins_categorized')
        assert categorized is not None, "player_skins_categorized missing from cache"

        # Should have male and female categories
        assert 'male' in categorized
        assert 'female' in categorized

        # Male should have subcategories (age_attire combinations)
        male_cats = categorized['male']
        assert isinstance(male_cats, dict)
        assert len(male_cats) > 0, "Male should have at least one category"

        # Each category should be a list of IDs
        for category, ids in male_cats.items():
            assert isinstance(ids, list), f"Category {category} should be a list"
            assert all(isinstance(id, int) for id in ids), f"All IDs in {category} should be integers"

    def test_episode_summaries_schema(self, full_cache):
        """Test episode summaries match schema (if present)."""
        summaries = full_cache.get('episode_summaries')

        if summaries is None:
            pytest.skip("Episode summaries not in cache (--skip-episodes was used)")

        assert isinstance(summaries, list)
        assert len(summaries) == 13, "Should have 13 episode summaries"

        for summary in summaries:
            assert 'name' in summary
            assert 'region_count' in summary
            assert 'regions' in summary
            assert 'object_types_present' in summary
            assert 'common_actions' in summary

            assert isinstance(summary['regions'], list)
            assert isinstance(summary['object_types_present'], list)
            assert isinstance(summary['common_actions'], list)


# ============================================================================
# Content Validation Tests
# ============================================================================

class TestContentValidation:
    """Test content quality and completeness."""

    def test_all_skins_categorized(self, full_cache):
        """Verify all 249 skins are categorized exactly once."""
        categorized = full_cache['player_skins_categorized']

        # Collect all male IDs
        all_male_ids = []
        for category_list in categorized['male'].values():
            all_male_ids.extend(category_list)

        # Collect all female IDs
        all_female_ids = []
        for category_list in categorized['female'].values():
            all_female_ids.extend(category_list)

        total_skins = len(all_male_ids) + len(all_female_ids)

        assert total_skins == 249, f"Expected 249 skins, got {total_skins}"

    def test_no_duplicate_skins(self, full_cache):
        """Verify no skin appears multiple times."""
        categorized = full_cache['player_skins_categorized']

        # Collect all male IDs
        all_male_ids = []
        for category_list in categorized['male'].values():
            all_male_ids.extend(category_list)

        # Collect all female IDs
        all_female_ids = []
        for category_list in categorized['female'].values():
            all_female_ids.extend(category_list)

        # Check for duplicates
        all_ids = all_male_ids + all_female_ids
        unique_ids = set(all_ids)

        assert len(all_ids) == len(unique_ids), f"Found {len(all_ids) - len(unique_ids)} duplicate IDs"

    def test_category_distributions_reasonable(self, full_cache):
        """Verify category distributions are reasonable."""
        summary = full_cache['player_skins_summary']
        categories = summary['categories']

        # Age categories should all exist
        assert 'age' in categories
        age_cats = categories['age']
        assert 'young' in age_cats
        assert 'middle_aged' in age_cats
        assert 'old' in age_cats

        # Counts should sum to 249
        total_age = (
            age_cats['young']['count'] +
            age_cats['middle_aged']['count'] +
            age_cats['old']['count']
        )
        assert total_age == 249, f"Age counts should sum to 249, got {total_age}"

        # Attire categories should exist
        assert 'attire' in categories
        attire_cats = categories['attire']
        expected_attires = ['casual', 'formal_suits', 'worker', 'athletic', 'novelty']

        for attire in expected_attires:
            assert attire in attire_cats, f"Missing attire category: {attire}"

    def test_episode_summaries_complete(self, full_cache):
        """Verify all episodes are summarized (if enabled)."""
        summaries = full_cache.get('episode_summaries')

        if summaries is None:
            pytest.skip("Episode summaries not in cache")

        assert len(summaries) == 13, "Should have 13 episode summaries"

        # Each summary should have reasonable content
        for summary in summaries:
            assert len(summary['regions']) > 0, f"Episode {summary['name']} has no regions"
            assert len(summary['object_types_present']) > 0, f"Episode {summary['name']} has no objects"
            assert len(summary['common_actions']) > 0, f"Episode {summary['name']} has no actions"


# ============================================================================
# File Structure Tests
# ============================================================================

class TestFileStructure:
    """Test cache file structure and sizes."""

    def test_concept_cache_exists(self, config):
        """Verify concept cache file exists."""
        cache_path = Path(config.paths.game_capabilities_concept)
        assert cache_path.exists(), f"Concept cache not found at {cache_path}"

    def test_full_cache_exists(self, config):
        """Verify full indexed cache file exists."""
        cache_path = Path(config.paths.game_capabilities_full_indexed)
        assert cache_path.exists(), f"Full indexed cache not found at {cache_path}"

    def test_concept_cache_size(self, concept_cache):
        """Verify concept cache is approximately the right size."""
        json_str = json.dumps(concept_cache, indent=2)
        line_count = len(json_str.split('\n'))

        # Should be around 1,200 lines (allow 20% variance)
        assert 960 <= line_count <= 1440, f"Concept cache should be ~1,200 lines, got {line_count}"

    def test_full_cache_size(self, full_cache):
        """Verify full indexed cache is approximately the right size."""
        json_str = json.dumps(full_cache, indent=2)
        line_count = len(json_str.split('\n'))

        # Should be around 2,500 lines (allow 20% variance)
        # Lower bound can be lower if episode summaries were skipped
        assert 1500 <= line_count <= 3000, f"Full cache should be ~2,500 lines, got {line_count}"

    def test_concept_cache_has_static_sections(self, concept_cache):
        """Verify concept cache has all required static sections."""
        required_sections = [
            'action_chains',
            'action_catalog',
            'object_types',
            'episode_catalog',
            'player_skins_summary'
        ]

        for section in required_sections:
            assert section in concept_cache, f"Concept cache missing section: {section}"

    def test_full_cache_has_all_sections(self, full_cache):
        """Verify full cache has all required sections."""
        required_sections = [
            'action_chains',
            'action_catalog',
            'object_types',
            'episode_catalog',
            'player_skins_summary',
            'player_skins_categorized'
        ]

        for section in required_sections:
            assert section in full_cache, f"Full cache missing section: {section}"


# ============================================================================
# Quality Spot Checks
# ============================================================================

class TestQualitySpotChecks:
    """Manual spot checks of categorization quality."""

    def test_spot_check_young_categorization(self, sample_capabilities, full_cache):
        """Spot check that 'young' categorizations are reasonable."""
        # Get original skin descriptions
        player_skins = sample_capabilities.get('player_skins', {})
        male_skins = {skin['id']: skin['description'] for skin in player_skins.get('male', [])}

        # Get categorized skins
        categorized = full_cache['player_skins_categorized']
        young_casual_ids = categorized['male'].get('young_casual', [])

        if not young_casual_ids:
            pytest.skip("No young_casual male skins found")

        # Check a few examples
        for skin_id in young_casual_ids[:5]:
            description = male_skins.get(skin_id, '')
            # Should mention 'young' or have typical young descriptors
            # This is a soft check - just verify description exists
            assert description, f"Missing description for skin ID {skin_id}"

    def test_spot_check_formal_categorization(self, sample_capabilities, full_cache):
        """Spot check that 'formal' categorizations are reasonable."""
        player_skins = sample_capabilities.get('player_skins', {})
        male_skins = {skin['id']: skin['description'] for skin in player_skins.get('male', [])}

        categorized = full_cache['player_skins_categorized']

        # Look for formal categories
        formal_cats = [k for k in categorized['male'].keys() if 'formal' in k]

        if not formal_cats:
            pytest.skip("No formal categories found")

        formal_ids = []
        for cat in formal_cats:
            formal_ids.extend(categorized['male'][cat])

        # Check a few examples
        for skin_id in formal_ids[:5]:
            description = male_skins.get(skin_id, '')
            # Formal skins should mention 'suit', 'formal', 'business', etc.
            # Soft check - just verify description exists
            assert description, f"Missing description for skin ID {skin_id}"
            # Could add more rigorous checks here if needed


# ============================================================================
# Integration Tests
# ============================================================================

@pytest.mark.slow
class TestPreprocessingIntegration:
    """Integration tests for full preprocessing pipeline."""

    def test_full_preprocessing_run(self, config):
        """Test full preprocessing pipeline (requires API key)."""
        # Skip if cache already exists (don't waste API calls)
        concept_path = Path(config.paths.game_capabilities_concept)
        if concept_path.exists():
            pytest.skip("Cache already exists, skipping full run to save API costs")

        preprocessor = CapabilitiesPreprocessor(config)
        report = preprocessor.run(include_episode_summaries=True)

        assert report.success, f"Preprocessing failed: {report.errors}"
        assert report.metrics.api_calls_made >= 2, "Should make at least 2 API calls"
        assert report.validation.all_skins_categorized, "All skins should be categorized"
        assert report.validation.no_duplicate_skins, "No duplicate skins"


# ============================================================================
# Run Tests
# ============================================================================

if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
