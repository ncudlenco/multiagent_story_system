"""
Mapping Tracker for Text-to-GEST Conversion

Tracks all conversion decisions from text to game capabilities, providing
transparency into how text descriptions are mapped to executable GEST elements.
"""

from typing import Dict, List, Optional, Any
from pydantic import BaseModel, Field


class ActorMapping(BaseModel):
    """Mapping for a single actor."""
    text: str = Field(description="Original text description of actor")
    gender: int = Field(description="Gender (1=male, 2=female)")
    skin_id: Optional[int] = Field(default=None, description="Assigned skin ID (null for generic)")
    assignment: str = Field(description="Assignment type: 'generic' or 'specific'")
    archetype_age: Optional[str] = Field(default=None, description="Age archetype if assigned")
    archetype_attire: Optional[str] = Field(default=None, description="Attire archetype if assigned")
    name: Optional[str] = Field(default=None, description="Character name if assigned")


class ActionMapping(BaseModel):
    """Mapping for a single action."""
    text: str = Field(description="Original text description of action")
    game_action: str = Field(description="Mapped game action name")
    object: Optional[str] = Field(default=None, description="Object involved in action")
    confidence: str = Field(description="Confidence level: 'high', 'medium', 'low'")
    event_id: Optional[str] = Field(default=None, description="Associated event ID in GEST")


class LocationMapping(BaseModel):
    """Mapping for a single location."""
    text: str = Field(description="Original text description of location")
    episode: str = Field(description="Assigned GTA SA episode")
    region: Optional[str] = Field(default=None, description="Specific region within episode")
    confidence: str = Field(description="Confidence level: 'high', 'medium', 'low'")


class ObjectMapping(BaseModel):
    """Mapping for a single object."""
    text: str = Field(description="Original text description of object")
    game_object: str = Field(description="Mapped game object name")
    confidence: str = Field(description="Confidence level: 'high', 'medium', 'low'")


class UnmappableItem(BaseModel):
    """Item that could not be mapped."""
    text: str = Field(description="Original text that couldn't be mapped")
    category: str = Field(description="Category: 'actor', 'action', 'location', 'object'")
    reason: str = Field(description="Why it couldn't be mapped")


class MappingTracker:
    """
    Tracks all conversion decisions during text-to-GEST conversion.

    This class provides transparency by recording how each text element
    is mapped to game capabilities, including confidence levels and
    any unmappable items.
    """

    def __init__(self):
        """Initialize empty mapping tracker."""
        self.actors: List[ActorMapping] = []
        self.actions: List[ActionMapping] = []
        self.locations: List[LocationMapping] = []
        self.objects: List[ObjectMapping] = []
        self.unmappable: List[UnmappableItem] = []

    def add_actor(
        self,
        text: str,
        gender: int,
        skin_id: Optional[int] = None,
        archetype_age: Optional[str] = None,
        archetype_attire: Optional[str] = None,
        name: Optional[str] = None
    ):
        """
        Track an actor mapping.

        Args:
            text: Original text description (e.g., "a man", "a police officer")
            gender: Gender code (1=male, 2=female)
            skin_id: Specific skin ID (None for generic actors)
            archetype_age: Age archetype if specific skin assigned
            archetype_attire: Attire archetype if specific skin assigned
            name: Character name if assigned
        """
        assignment = "specific" if skin_id is not None else "generic"

        mapping = ActorMapping(
            text=text,
            gender=gender,
            skin_id=skin_id,
            assignment=assignment,
            archetype_age=archetype_age,
            archetype_attire=archetype_attire,
            name=name
        )

        self.actors.append(mapping)

    def add_action(
        self,
        text: str,
        game_action: str,
        confidence: str,
        object_name: Optional[str] = None,
        event_id: Optional[str] = None
    ):
        """
        Track an action mapping.

        Args:
            text: Original text description of action
            game_action: Mapped game action name
            confidence: Confidence level ('high', 'medium', 'low')
            object_name: Object involved in action
            event_id: Associated event ID in GEST
        """
        mapping = ActionMapping(
            text=text,
            game_action=game_action,
            object=object_name,
            confidence=confidence,
            event_id=event_id
        )

        self.actions.append(mapping)

    def add_location(
        self,
        text: str,
        episode: str,
        confidence: str,
        region: Optional[str] = None
    ):
        """
        Track a location mapping.

        Args:
            text: Original text description of location
            episode: Assigned GTA SA episode
            confidence: Confidence level ('high', 'medium', 'low')
            region: Specific region within episode
        """
        mapping = LocationMapping(
            text=text,
            episode=episode,
            region=region,
            confidence=confidence
        )

        self.locations.append(mapping)

    def add_object(
        self,
        text: str,
        game_object: str,
        confidence: str
    ):
        """
        Track an object mapping.

        Args:
            text: Original text description of object
            game_object: Mapped game object name
            confidence: Confidence level ('high', 'medium', 'low')
        """
        mapping = ObjectMapping(
            text=text,
            game_object=game_object,
            confidence=confidence
        )

        self.objects.append(mapping)

    def add_unmappable(
        self,
        text: str,
        category: str,
        reason: str
    ):
        """
        Track an item that couldn't be mapped.

        Args:
            text: Original text that couldn't be mapped
            category: Category ('actor', 'action', 'location', 'object')
            reason: Explanation of why it couldn't be mapped
        """
        item = UnmappableItem(
            text=text,
            category=category,
            reason=reason
        )

        self.unmappable.append(item)

    def to_dict(self) -> Dict[str, Any]:
        """
        Export all mappings as a dictionary.

        Returns:
            Dictionary with all mapping data
        """
        return {
            "actors": [actor.model_dump() for actor in self.actors],
            "actions": [action.model_dump() for action in self.actions],
            "locations": [location.model_dump() for location in self.locations],
            "objects": [obj.model_dump() for obj in self.objects],
            "unmappable": [item.model_dump() for item in self.unmappable]
        }

    def get_summary(self) -> Dict[str, int]:
        """
        Get summary statistics.

        Returns:
            Dictionary with counts of each mapping type
        """
        return {
            "total_actors": len(self.actors),
            "generic_actors": sum(1 for a in self.actors if a.assignment == "generic"),
            "specific_actors": sum(1 for a in self.actors if a.assignment == "specific"),
            "total_actions": len(self.actions),
            "high_confidence_actions": sum(1 for a in self.actions if a.confidence == "high"),
            "medium_confidence_actions": sum(1 for a in self.actions if a.confidence == "medium"),
            "low_confidence_actions": sum(1 for a in self.actions if a.confidence == "low"),
            "total_locations": len(self.locations),
            "total_objects": len(self.objects),
            "unmappable_items": len(self.unmappable)
        }
