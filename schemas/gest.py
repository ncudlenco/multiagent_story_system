"""
Unified GEST Schema

Single GEST structure used across ALL refinement levels.
Progressive refinement adds more events and detail, but structure stays the same.

Early levels: 1-3 events (concept)
Mid levels: 5-50 events (outline, scenes)
Late levels: 50-200 events (detailed choreography)
"""

from pydantic import BaseModel, Field, model_validator, ConfigDict
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
        description="Additional properties (e.g., Type='Chair', Name='John', Gender=2, scene_type='leaf')"
    )

    @property
    def is_parent_scene(self) -> bool:
        """Check if this event is a parent scene (has no temporal relations)"""
        return self.Properties.get('scene_type') == 'parent'

    @property
    def is_leaf_scene(self) -> bool:
        """Check if this event is a leaf scene (can have temporal relations)"""
        return self.Properties.get('scene_type') == 'leaf'

    @property
    def can_have_temporal_relations(self) -> bool:
        """Only leaf scenes can have temporal relations"""
        return self.is_leaf_scene



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
    - 'after', 'before', 'starts_with': ONLY between DIFFERENT actors

    NOTE: This class is kept for backwards compatibility but is now part of TemporalEntry.
    """
    source: Optional[str] = Field(description="Source event ID. Not used by 'starts_with'. Mandatory for 'after'/'before'.")
    type: Literal["after", "before", "starts_with"] = Field(
        description="""
            1. **starts_with**
            - Events begin simultaneously (synchronized start time)
            - ALWAYS used for 2-actor interactions that must be coordinated
            - Both events reference the same relation ID in their "relations" arrays
            - Examples: Give↔INV-Give, Kiss, Hug, Talk, HandShake
            - Optionally used for other simultaneous event actions across different actors
            - ALL events that start at the same time reference the same relation ID in their "relations" arrays
            - Examples: Sitting down together, standing up together.

            2. **before**
            - Source event must COMPLETE before target event BEGINS
            - Used for sequential ordering across different actors
            - Creates dependency: target cannot start until source finishes
            - Example: "Bob finishes smoking BEFORE Alice stands up"

            3. **after**
            - Source event BEGINS after target event COMPLETES
            - Inverse of "before" (semantically equivalent but different perspective)
            - Used for sequential ordering across different actors
            - Example: "Alice sits down AFTER Bob arrives"
        """
    )
    target: Optional[str] = Field(description="Target event ID. Not used by 'starts_with'. Mandatory for 'after'/'before'.")

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
    action: Literal["record", "stop"] = Field(
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

    **Event Storage**: Events are stored at ROOT LEVEL as dynamic fields.
    Reserved fields (temporal, spatial, semantic, logical, camera) are predefined.
    All other fields are treated as events (event_id -> GESTEvent).

    Example structure:
    {
        "lunch_break": {...},        // Event at root level
        "workplace_scandal": {...},   // Event at root level
        "temporal": {...},           // Reserved field
        "semantic": {...}            // Reserved field
    }
    """

    # Reserved fields - these are NOT events
    temporal: Dict[str, Any] = Field(
        default_factory=dict,
        description="""Temporal relations structure with heterogeneous entries.
        Contains three types of entries (distinguished by key and structure, not by Pydantic model):

        1. "starting_actions": Flat dict mapping actor IDs to their first event IDs [CRITICAL]
           Example: {"alice": "a1", "bob": "b1"}

        2. event_id entries: Dict with "relations" (list) and "next" (string or null)
           Example: {"relations": ["r1"], "next": "a2"}

        3. relation_id entries: Dict with "type", "source", "target"
           Example: {"type": "after", "source": "a1", "target": "b1"}

        Full example:
        {
            "starting_actions": {"alice": "a1", "bob": "b1"},
            "a1": {"relations": ["r1"], "next": "a2"},
            "r1": {"type": "after", "source": "a1", "target": "b1"}
        }

        Note: Using Dict[str, Any] instead of Dict[str, TemporalEntry] to avoid
        Pydantic validator wrapping issues with the heterogeneous structure.
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

    logical: Dict[str, Any] = Field(
        default_factory=dict,
        description="""Logical relations expressing dependencies and implications.
        Structure: {event_id: {"relations": [relation_ids]}, relation_id: {"type": ..., "source": ..., "target": ...}}

        Types: causes, caused_by, enables, prevents, blocks, implies, implied_by, requires, depends_on,
               equivalent_to, contradicts, conflicts_with, and, or, not

        Example:
        {
          "a1": {"relations": ["l1"]},
          "l1": {"type": "causes", "source": "a1", "target": "a3"}
        }
        """
    )

    camera: Dict[str, CameraCommand] = Field(
        default_factory=dict,
        description="Camera commands per event: event_id -> CameraCommand"
    )

    # Allow extra fields (these will be events)
    model_config = ConfigDict(extra="allow")

    @model_validator(mode='before')
    @classmethod
    def validate_events_at_root(cls, data: Any) -> Any:
        """
        Validate that all non-reserved fields at root level are valid GESTEvent dicts or instances.

        Events must be at root level alongside reserved fields (temporal, spatial, etc.).
        Old nested 'events' structure is NOT supported.

        Accepts both:
        - dict with 'Action' field (from LLM output or JSON loading)
        - GESTEvent instances (from merging existing GESTs)
        """
        if not isinstance(data, dict):
            return data

        reserved_fields = {'temporal', 'spatial', 'semantic', 'logical', 'camera'}

        # Validate all extra fields are valid event structures
        for key, value in list(data.items()):
            if key not in reserved_fields and not key.startswith('_'):
                # This should be an event at root level
                if isinstance(value, GESTEvent):
                    # Already a GESTEvent instance (from merging GESTs) - convert to dict
                    data[key] = value.model_dump()
                elif isinstance(value, dict):
                    # Dict from LLM output - validate it has Action field
                    if 'Action' not in value:
                        raise ValueError(f"Event '{key}' missing required 'Action' field")
                else:
                    raise ValueError(f"Event '{key}' must be a dict or GESTEvent, got {type(value)}")

        return data

    @model_validator(mode='after')
    def convert_extra_fields_to_events(self) -> 'GEST':
        """
        Convert extra fields (stored as dicts in __pydantic_extra__) to GESTEvent instances.

        With extra="allow", Pydantic stores extra fields as-is in __pydantic_extra__.
        This validator runs after initialization to convert those dicts to proper GESTEvent objects,
        ensuring .events property returns GESTEvent instances with .Properties attribute.
        """
        if hasattr(self, '__pydantic_extra__') and self.__pydantic_extra__:
            converted = {}
            for key, value in self.__pydantic_extra__.items():
                if isinstance(value, dict):
                    # Convert dict to GESTEvent
                    converted[key] = GESTEvent(**value)
                else:
                    # Already a GESTEvent instance
                    converted[key] = value
            self.__pydantic_extra__ = converted
        return self

    @property
    def events(self) -> Dict[str, GESTEvent]:
        """
        Backward compatibility property: access all events via .events

        Returns a dict of all non-reserved fields (which are events).
        Pydantic v2 stores extra fields in __pydantic_extra__.
        """
        # In Pydantic v2 with extra="allow", extra fields are in __pydantic_extra__
        if hasattr(self, '__pydantic_extra__') and self.__pydantic_extra__:
            return dict(self.__pydantic_extra__)
        return {}

    def __setitem__(self, key: str, value: GESTEvent) -> None:
        """Allow dict-style assignment for events: gest['event_id'] = event"""
        setattr(self, key, value)

    def __getitem__(self, key: str) -> GESTEvent:
        """Allow dict-style access for events: event = gest['event_id']"""
        reserved = {'temporal', 'spatial', 'semantic', 'logical', 'camera'}
        if key in reserved:
            return getattr(self, key)
        # Try to get as event
        return getattr(self, key)



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

    @staticmethod
    def from_dict(data: Dict[str, Any]) -> 'DualOutput':
        """Create DualOutput from dict (e.g., loaded from JSON)"""
        return DualOutput(
            gest=GEST(**data['gest']),
            narrative=data['narrative'],
            title=data.get('title', None)
        )

