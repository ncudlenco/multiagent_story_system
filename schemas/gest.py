"""
Unified GEST Schema

Single GEST structure used across ALL refinement levels.
Progressive refinement adds more events and detail, but structure stays the same.

Early levels: 1-3 events (concept)
Mid levels: 5-50 events (outline, scenes)
Late levels: 50-200 events (detailed choreography)
"""

from pydantic import BaseModel, Field, model_validator
from typing import Dict, List, Any, Optional, Literal


class GESTEvent(BaseModel):
    """
    Single event in GEST - used at all abstraction levels.

    Events can represent:
    - Actor/object existence (Action="Exists")
    - Actor actions (Action="SitDown", "PickUp", "Eat", etc.)
    - Location transitions (Action="Move")
    """
    Action: str = Field(
        description="Action name (e.g., 'Eat', 'SitDown', 'Exists', 'Move')"
    )
    Entities: List[str] = Field(
        description="Entity IDs involved in this event (actor IDs, object IDs)"
    )
    Location: List[str] = Field(
        description="Location/region names where event occurs"
    )
    Timeframe: Optional[str] = Field(
        None,
        description="Optional time-of-day indicator (e.g., 'morning', 'afternoon', 'evening')"
    )
    Properties: Dict[str, Any] = Field(
        default_factory=dict,
        description="Additional properties (e.g., Type='Chair', Name='John', Gender=2)"
    )



class TemporalEntry(BaseModel):
    """
    Unified model for ALL temporal dictionary entries.

    The temporal dict contains heterogeneous entries. This single model handles all types
    by making all fields optional. Different entries populate different field combinations:

    1. Starting actions entry (key="starting_actions"):
       - starting_actions: Dict mapping actor IDs to first event IDs
       Example: {"starting_actions": {"alice": "a1", "bob": "b1"}}

    2. Event temporal info (key=event_id):
       - relations: List of relation IDs this event participates in
       - next: Next event ID in same actor's chain
       Example: {"relations": ["r1", "r2"], "next": "a2"}

    3. Temporal relation definition (key=relation_id):
       - type: Relation type (starts_with, after, before, concurrent)
       - source: Source event ID (omit for starts_with)
       - target: Target event ID (omit for starts_with)
       Example: {"type": "after", "source": "a2", "target": "b1"}
    """

    # For starting_actions entry
    starting_actions: Optional[Dict[str, str]] = Field(
        None,
        description="Maps actor IDs to their first event IDs (only for key='starting_actions')"
    )

    # For event temporal info
    relations: Optional[List[str]] = Field(
        None,
        description="Relation IDs this event participates in"
    )
    next: Optional[str] = Field(
        None,
        description="Next event in same actor's action chain"
    )

    # For temporal relation definitions
    type: Optional[Literal["starts_with", "after", "before", "concurrent"]] = Field(
        None,
        description="Type of temporal relation"
    )
    source: Optional[str] = Field(
        None,
        description="Source event ID (only for after/before/concurrent)"
    )
    target: Optional[str] = Field(
        None,
        description="Target event ID (only for after/before/concurrent)"
    )

    @model_validator(mode='before')
    @classmethod
    def handle_starting_actions_dict(cls, values):
        """
        Handle special case: starting_actions entry is a plain dict in existing GEST JSON.

        When parsing {"starting_actions": {"actor1": "event1", ...}}, the value is a plain
        dict with no field names. We need to wrap it for Pydantic validation.
        """
        if isinstance(values, dict):
            # Check if this is a raw dict without any known field names
            known_fields = {'starting_actions', 'relations', 'next', 'type', 'source', 'target'}
            has_known_fields = any(key in values for key in known_fields)

            if not has_known_fields and values:
                # This is a raw starting_actions dict (actor_id -> event_id mappings)
                # Wrap it in the starting_actions field
                return {'starting_actions': values}

        return values



class TemporalRelation(BaseModel):
    """
    Temporal relation between events.

    Critical rules:
    - 'next': ONLY between events of SAME actor (action chains)
    - 'after', 'before', 'starts_with', 'concurrent': ONLY between DIFFERENT actors

    NOTE: This class is kept for backwards compatibility but is now part of TemporalEntry.
    """
    source: str = Field(description="Source event ID")
    type: str = Field(
        description="Relation type: 'after', 'before', 'starts_with', 'concurrent', 'next'"
    )
    target: str = Field(description="Target event ID")


class SpatialRelation(BaseModel):
    """Spatial relation between entities (objects or actors)"""
    type: str = Field(
        description="Spatial type: 'near', 'behind', 'left', 'right', 'on', 'in_front'"
    )
    target: str = Field(description="Target entity ID")


class SemanticRelation(BaseModel):
    """
    Semantic relation for narrative coherence.

    Used for Inception-style complexity (meta-references, layered narratives).
    NOT used by simulator - only for narrative generation and coherence checking.

    Examples: writes_about, observes, reads, interrupts, documents, etc.
    """
    type: str = Field(
        description="Relation type (e.g., 'writes_about', 'observes', 'reads', 'interrupts', 'documents')"
    )
    targets: List[str] = Field(
        description="Target event IDs that this event relates to semantically"
    )


class CameraCommand(BaseModel):
    """Camera command for an event"""
    action: str = Field(
        description="Camera action: 'record' (start recording), 'stop' (pause recording)"
    )


class GEST(BaseModel):
    """
    Unified GEST structure used at all refinement levels.

    Progressive refinement strategy:
    - Level 1 (Concept): 1-3 events, meta-structure intent
    - Level 2 (Casting): Same events, specific actors assigned
    - Level 3 (Outline): 5-15 events, scene sequence with semantic relations
    - Level 4 (Scene Breakdown): 20-50 events, scene-level detail
    - Level 5 (Scene Detail): 50-200 events per scene, full choreography
    - Level 6 (Aggregation): All scenes merged, cross-scene temporal relations

    Structure stays the same - only the number of events and level of detail changes.
    """

    events: Dict[str, GESTEvent] = Field(
        default_factory=dict,
        description="All events in the story, keyed by unique event ID"
    )

    temporal: Dict[str, TemporalEntry] = Field(
        default_factory=dict,
        description="""Temporal relations structure with heterogeneous entries.
        Uses TemporalEntry model which handles three entry types:
        - "starting_actions": Maps actor IDs to their first event IDs
        - event_id keys: Event temporal info (relations list, next pointer)
        - relation_id keys: Temporal relation definitions (type, source, target)

        Example:
        {
            "starting_actions": {"alice": "a1", "bob": "b1"},
            "a1": {"relations": ["r1"], "next": "a2"},
            "r1": {"type": "after", "source": "a1", "target": "b1"}
        }
        """
    )

    spatial: Dict[str, Dict[str, List[SpatialRelation]]] = Field(
        default_factory=dict,
        description="Spatial relations: entity_id -> {'relations': [SpatialRelation list]}"
    )

    semantic: Dict[str, SemanticRelation] = Field(
        default_factory=dict,
        description="Semantic relations for narrative coherence: event_id -> SemanticRelation"
    )

    camera: Dict[str, CameraCommand] = Field(
        default_factory=dict,
        description="Camera commands per event: event_id -> CameraCommand"
    )



class DualOutput(BaseModel):
    """
    Standard output structure: GEST + narrative + optional title.

    Used by all agents. The title field is populated only by ConceptAgent
    (movie-style title, 3-7 words). Other agents leave it None.

    Narrative progression across levels:
    - Concept: Movie synopsis/logline (1-3 sentences) + title
    - Casting: Vivid prose with character names and details
    - Outline: Expanded story with scene sequences
    - Scene Detail: Rich screenplay-style description
    """

    gest: GEST = Field(
        description="Graph structure at current refinement level"
    )

    narrative: str = Field(
        description="Rich textual description of the story at current refinement level"
    )

    title: Optional[str] = Field(
        None,
        description="Optional movie-style title (populated by ConceptAgent only)"
    )

