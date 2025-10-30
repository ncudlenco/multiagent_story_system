"""
Preprocessing schemas for game capabilities transformation.

This module defines Pydantic models for LLM-based preprocessing of game_capabilities.json,
including player skin categorization and episode summarization.
"""

from typing import Dict, List, Optional, Any
from pydantic import BaseModel, Field


# ============================================================================
# Player Skins Categorization Schemas
# ============================================================================

class CategoryCounts(BaseModel):
    """Counts and example IDs for a specific category."""
    count: int = Field(..., description="Number of skins in this category")
    example_ids: List[int] = Field(
        ...,
        description="List of 3-5 representative skin IDs from this category"
    )


class GenderCategoryCounts(BaseModel):
    """Gender-specific counts."""
    male: CategoryCounts = Field(..., description="Male skin counts")
    female: CategoryCounts = Field(..., description="Female skin counts")


class AgeCategoryCounts(BaseModel):
    """Age category breakdowns."""
    young: CategoryCounts = Field(..., description="Young characters (teens, 20s)")
    middle_aged: CategoryCounts = Field(..., description="Middle-aged characters (30s-50s)")
    old: CategoryCounts = Field(..., description="Old characters (60s+)")


class AttireCategoryCounts(BaseModel):
    """Attire category breakdowns."""
    casual: CategoryCounts = Field(..., description="Casual clothing (t-shirts, jeans, etc.)")
    formal_suits: CategoryCounts = Field(..., description="Formal business attire")
    worker: CategoryCounts = Field(..., description="Worker/labor clothing")
    athletic: CategoryCounts = Field(..., description="Athletic/sportswear")
    novelty: CategoryCounts = Field(..., description="Costumes, unusual clothing")


class RaceCategoryCounts(BaseModel):
    """Race category breakdowns."""
    black: CategoryCounts = Field(..., description="Black characters")
    white: CategoryCounts = Field(..., description="White characters")
    asian: CategoryCounts = Field(..., description="Asian characters")
    other: CategoryCounts = Field(..., description="Other/ambiguous ethnicities")


class AllCategories(BaseModel):
    """All categorization dimensions."""
    age: AgeCategoryCounts = Field(..., description="Age category breakdowns")
    attire: AttireCategoryCounts = Field(..., description="Attire category breakdowns")
    race: RaceCategoryCounts = Field(..., description="Race category breakdowns")


class RepresentativeExample(BaseModel):
    """A representative skin example with categorization tags."""
    id: int = Field(..., description="Skin ID number")
    description: str = Field(..., description="Original skin description")
    tags: List[str] = Field(
        ...,
        description="Category tags: gender, age, race, attire"
    )


class GenderCount(BaseModel):
    """Count for a specific gender."""
    count: int = Field(..., description="Number of skins for this gender")


class GenderBreakdown(BaseModel):
    """Gender breakdown of skins."""
    male: GenderCount = Field(..., description="Male skin count")
    female: GenderCount = Field(..., description="Female skin count")


class PlayerSkinsSummary(BaseModel):
    """High-level summary of player skins for concept generation."""
    total_count: int = Field(..., description="Total number of skins (should be 249)")
    by_gender: GenderBreakdown = Field(
        ...,
        description="Gender breakdown"
    )
    categories: AllCategories = Field(..., description="All category breakdowns")
    representative_examples: List[RepresentativeExample] = Field(
        ...,
        min_length=10,
        max_length=15,
        description="10-15 diverse representative examples"
    )


class SkinCategory(BaseModel):
    """A category of skins with their IDs."""
    category_name: str = Field(..., description="Category name (e.g., 'young_casual', 'middle_aged_formal')")
    skin_ids: List[int] = Field(..., description="List of skin IDs in this category")


class GenderCategories(BaseModel):
    """Categorized skins for one gender."""
    categories: List[SkinCategory] = Field(..., description="List of skin categories for this gender")


class PlayerSkinsCategorized(BaseModel):
    """Full categorized lists of player skins by gender and subcategories."""
    male: GenderCategories = Field(
        ...,
        description="Male skins categorized by age_attire"
    )
    female: GenderCategories = Field(
        ...,
        description="Female skins categorized by age_attire"
    )


class PlayerSkinsPreprocessingOutput(BaseModel):
    """Complete output from player skins categorization agent."""
    player_skins_summary: PlayerSkinsSummary = Field(
        ...,
        description="High-level summary for concept generation (~150 lines)"
    )
    player_skins_categorized: PlayerSkinsCategorized = Field(
        ...,
        description="Full categorized lists for casting (~400 lines)"
    )


# ============================================================================
# Episode Summarization Schemas
# ============================================================================

class EpisodeSummary(BaseModel):
    """Summary of a single episode for scene breakdown."""
    name: str = Field(..., description="Episode name (e.g., 'classroom1', 'gym2_a')")
    region_count: int = Field(..., description="Number of regions in this episode")
    regions: List[str] = Field(..., description="List of region names")
    object_types_present: List[str] = Field(
        ...,
        description="Distinct object types found in this episode (Chair, Desk, etc.)"
    )
    common_actions: List[str] = Field(
        ...,
        description="Most common/important actions available in this episode"
    )


class EpisodeSummariesOutput(BaseModel):
    """Complete output from episode summarization agent."""
    episode_summaries: List[EpisodeSummary] = Field(
        ...,
        min_length=13,
        max_length=13,
        description="Summaries for all 13 episodes (~250 lines total)"
    )


# ============================================================================
# Validation Report Schema
# ============================================================================

class PreprocessingMetrics(BaseModel):
    """Metrics from preprocessing execution."""
    total_processing_time_seconds: float = Field(..., description="Total time taken")
    api_calls_made: int = Field(..., description="Number of LLM API calls")
    skin_categorization_time_seconds: float = Field(..., description="Time for skin categorization")
    episode_summarization_time_seconds: Optional[float] = Field(
        None,
        description="Time for episode summarization (if enabled)"
    )


class ValidationResults(BaseModel):
    """Results from validating preprocessed cache files."""
    concept_cache_line_count: int = Field(..., description="Line count for concept cache")
    full_indexed_cache_line_count: int = Field(..., description="Line count for full indexed cache")
    all_skins_categorized: bool = Field(..., description="All 249 skins accounted for")
    no_duplicate_skins: bool = Field(..., description="No skin appears multiple times")
    all_episodes_summarized: bool = Field(..., description="All 13 episodes summarized")
    schema_validation_passed: bool = Field(..., description="Pydantic schemas validate")
    spot_check_samples: List[Dict[str, Any]] = Field(
        ...,
        description="Sample categorizations for manual review"
    )


class PreprocessingReport(BaseModel):
    """Complete preprocessing validation report."""
    success: bool = Field(..., description="Overall success status")
    metrics: PreprocessingMetrics = Field(..., description="Performance metrics")
    validation: ValidationResults = Field(..., description="Validation results")
    errors: List[str] = Field(default_factory=list, description="Any errors encountered")
    warnings: List[str] = Field(default_factory=list, description="Any warnings")
