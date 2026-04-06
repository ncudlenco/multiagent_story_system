"""
Schemas for the hybrid GEST generation system.

Defines the structured outputs for each stage of the LLM-directed pipeline:
- Stage 1: Story concept with logical/semantic relations
- Stage 2: Casting (character → skin assignment)
- Stage 3: Generation config and constraints
"""

from typing import Dict, List, Optional
from pydantic import BaseModel, Field


class GenerationConfig(BaseModel):
    """Configurable constraints for GEST generation."""

    seed_text: Optional[str] = Field(
        default=None,
        description="Optional story seed text for prompt alignment"
    )
    num_scenes: int = Field(
        default=3,
        ge=1, le=10,
        description="Target number of scenes"
    )
    num_protagonists: int = Field(
        default=2,
        ge=1, le=10,
        description="Number of main characters"
    )
    include_extras: bool = Field(
        default=False,
        description="Include background actors doing routines"
    )
    seed_episodes: Optional[List[str]] = Field(
        default=None,
        description="Force specific episodes (helps LLM diversity)"
    )
    seed_regions: Optional[List[str]] = Field(
        default=None,
        description="Force specific regions"
    )
    max_events_per_scene: int = Field(
        default=20,
        ge=5, le=100,
        description="Maximum events per scene"
    )
    max_chains_per_actor: int = Field(
        default=3,
        ge=1, le=20,
        description="Maximum action chains (POI visits) per actor per scene"
    )
    enable_concept_events: bool = Field(
        default=True,
        description="Create scene/story parent events in GEST (disable to save budget when debugging)"
    )
    enable_logical_relations: bool = Field(
        default=True,
        description="Run logical relations subagent after rounds/scenes/finalize"
    )
    enable_semantic_relations: bool = Field(
        default=True,
        description="Run semantic relations subagent after rounds/scenes/finalize"
    )


class CharacterDescription(BaseModel):
    """A character in the story concept."""

    name: str = Field(description="Character name")
    role: str = Field(description="Role in the story (e.g., 'office worker', 'delivery person')")
    gender: int = Field(description="1=male, 2=female")
    appearance_hint: str = Field(
        description="Appearance description for casting (e.g., 'young man in business casual')"
    )
    personality: Optional[str] = Field(
        default=None,
        description="Personality hint for action selection"
    )


class ScenePlan(BaseModel):
    """A scene in the story concept."""

    scene_id: str = Field(description="Unique scene identifier")
    description: str = Field(description="What happens in this scene")
    episode: Optional[str] = Field(
        default=None,
        description="Assigned episode (may be decided during concept or later)"
    )
    region: Optional[str] = Field(
        default=None,
        description="Assigned region within episode"
    )
    characters_present: List[str] = Field(
        description="Character names present in this scene"
    )
    key_activities: List[str] = Field(
        description="Key activities/actions in this scene"
    )
    mood: Optional[str] = Field(
        default=None,
        description="Scene mood (e.g., 'tense', 'relaxed', 'dramatic')"
    )


class LogicalRelation(BaseModel):
    """Logical relation between scenes."""

    source_scene: str = Field(description="Source scene ID")
    target_scene: str = Field(description="Target scene ID")
    relation_type: str = Field(
        description="Relation type: causes, caused_by, enables, prevents, blocks, "
                    "implies, implied_by, requires, depends_on, equivalent_to, "
                    "contradicts, conflicts_with, and, or, not"
    )


class SemanticRelation(BaseModel):
    """Semantic relation for narrative coherence."""

    event_id: str = Field(description="Source event/scene ID")
    relation_type: str = Field(
        description="Free-text relation type (e.g., 'observes', 'interrupts', 'reflects_on')"
    )
    target_events: List[str] = Field(description="Target event/scene IDs")


class StoryConcept(BaseModel):
    """Complete story concept output from Stage 1."""

    title: str = Field(description="Story title")
    narrative: str = Field(description="2-3 sentence story summary")
    characters: List[CharacterDescription] = Field(
        description="Characters in the story"
    )
    scenes: List[ScenePlan] = Field(
        description="Ordered scenes in the story"
    )
    logical_relations: List[LogicalRelation] = Field(
        default_factory=list,
        description="Logical relations between scenes"
    )
    semantic_relations: List[SemanticRelation] = Field(
        default_factory=list,
        description="Semantic relations for narrative coherence"
    )


class CastingResult(BaseModel):
    """Casting output from Stage 2."""

    assignments: Dict[str, int] = Field(
        description="Mapping of character name → skin_id"
    )
    reasoning: Optional[Dict[str, str]] = Field(
        default=None,
        description="Optional reasoning for each casting choice"
    )
