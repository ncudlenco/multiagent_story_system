"""
FORMALIZED TEMPORAL RULES FOR SCENE DETAIL AGENT
=================================================

This file contains the complete formalized temporal relation rules for the multiagent story system.
These rules should be integrated into the SceneDetailAgent to ensure valid GEST generation.

Author: Generated from analysis of simulation_environment_capabilities.json,
        prompts_about_temporal relations.md, and incredibly_complex.json
Date: 2025-11-02
"""

# =============================================================================
# PART 1: TEMPORAL RELATION TYPES
# =============================================================================

TEMPORAL_RELATION_TYPES = """
VALID TEMPORAL RELATION TYPES
==============================

The system uses THREE temporal relation types:

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

OTHER TYPES:
- **concurrent**: Defined in schema but never used in practice. Do not use this type.
- **next**: NOT a relation type - it's a structural field for same-actor action chains. MUST NOT be used for cross-actor relations. MUST always be set for same-actor events. Last event has next: null.
"""

# =============================================================================
# PART 2: TEMPORAL STRUCTURE ARCHITECTURE
# =============================================================================

TEMPORAL_STRUCTURE = """
TEMPORAL STRUCTURE ARCHITECTURE
================================

The temporal system has TWO LEVELS:

LEVEL 1: Actor Action Chains (via "next" field)
------------------------------------------------
Structure:
{
  "temporal": {
    "starting_actions": {
      "actor1": "event_id_1",
      "actor2": "event_id_2",
      ...
    },
    "event_id_1": {
      "relations": null,      // Cross-actor relations (Level 2)
      "next": "event_id_2"    // Same-actor next action
    },
    "event_id_2": {
      "relations": ["r1"],    // Cross-actor relations (Level 2)
      "next": "event_id_3"    // Same-actor next action
    },
    "event_id_3": {
      "relations": null,      // Cross-actor relations (Level 2)
      "next": null            // Same-actor next action -> end of chain
    }
  }
}

RULES:
1. Every actor MUST have an entry in "starting_actions"
2. Every event MUST have a "next" field (event_id OR null)
3. "next" creates linear chain: starting_actions → event → event → ... → null
4. CRITICAL: "next" ONLY connects SAME actor's events, NEVER cross-actor
5. Chain ends when "next": null
6. No orphaned events (all except Exists events must be reachable from starting_actions)

LEVEL 2: Cross-Actor Relations (via "relations" field)
-------------------------------------------------------
Structure:
{
  "temporal": {
    "event_a1": {
      "relations": ["a1_after_x1", "all_sit_sync"],
      "next": "event_a2"
    },
    "a1_after_x1": {
      "source": "event_a1",
      "type": "after",
      "target": "event_x1"
    },
    "event_a2": {
      "relations": null,
      "next": null
    },
    "event_x1": {
      "relations": ["x1_before_a1"],
      "next": "event_x2"
    },
    "x1_before_a1": {
      "source": "event_x1",
      "type": "before",
      "target": "event_a1"
    }
    "event_x2": {
      "relations": ["all_sit_sync"],
      "next": null
    },
    "event_y1": {
      "relations": ["all_sit_sync"],
      "next": null
    },
    "all_sit_sync": {
      "type": "starts_with"
    }
  }
}

RULES:
1. Use for events from DIFFERENT actors only
2. Same relation of type starts_with MUST be set into ALL events' "relations" arrays for events that start simultaneously. Different starts_with relation for different sincronizations (e.g., one for sitting, one for interaction).
3. Relation definition stored separately with type/source/target.
4. Relation types: starts_with | before | after
5. Relation IDs must be unique across the entire temporal structure.
6. The Level 2 relations MUST NOT be used to connect same-actor events (use "next" instead).
7. The Level 2 relations MUST NOT create circular / blocking dependencies.

EXIST EVENTS IN CHAINS:
-----------------------
- Exist events MUST NOT appear anywhere in the temporal section.
"""

# =============================================================================
# PART 3: ACTION LIFECYCLE RULES
# =============================================================================

ACTION_LIFECYCLE_RULES = """
ACTION LIFECYCLE RULES
======================

These are MANDATORY sequencing rules that MUST be enforced:

1. EXIST EVENTS FIRST
-------------------
Rule: All actors and objects MUST have Exist event before any action involving them
Validation: Check that all entity IDs used in events have corresponding Exist event
Structure: Exist event ID must match entity name exactly

2. SIT/STAND SEQUENCES
--------------------
Rule: SitDown → [seated actions] → StandUp
Prerequisites:
  - Must SitDown before: OpenLaptop, TypeOnKeyboard, Eat (at table), CloseEyes (at chair)
  - While seated: ONLY POI-specific actions allowed (e.g., laptop actions only at laptop chairs)
Consequences:
  - Must StandUp before Move to different location
  - Cannot perform standing actions while seated

Example Chain:
  SitDown → OpenLaptop → TypeOnKeyboard → CloseLaptop → StandUp → Move

3. GET ON/GET OFF SEQUENCES (Equipment & Bed)
------------------------------------------
Rule: GetOn → [equipment actions] → GetOff

A. Gym Equipment:
  - GetOn → JogTreadmill → GetOff (treadmill)
  - GetOn → BenchpressWorkOut → GetOff (benchpress)
  - GetOn → PedalGymBike → GetOff (gym bike)
  - CRITICAL: GetOff REQUIRED before actor can Move to different location

B. Bed:
  - GetOn → Sleep → GetOff (bed)
  - CRITICAL: GetOff REQUIRED before actor can Move to different location

Validation:
  - Every GetOn must have matching GetOff by same actor on same object
  - No Move actions between GetOn and GetOff

4. PICK UP/PUT DOWN SEQUENCES (Portable Objects)
---------------------------------------------
Rule: PickUp → [object usage actions] → PutDown

Object Types:
  - Drinks: PickUp → Drink → PutDown
  - Food: PickUp → Eat → PutDown
  - Remote: PickUp → [LookAt] → PutDown
  - Dumbbells: PickUp → DumbbellsWorkOut → PutDown

CRITICAL RULES:
  - Same actor who PickedUp MUST PutDown (same object)
  - Cannot PickUp multiple objects simultaneously (must PutDown first)
  - Object must exist in location before PickUp

Validation:
  - Every PickUp must have matching PutDown by same actor for same object
  - No PickUp of second object before PutDown of first

5. TAKE OUT/STASH SEQUENCES (Spawnable Objects)
--------------------------------------------
Rule: TakeOut → [spawnable usage actions] → Stash

A. Phone Sequence:
  TakeOut → AnswerPhone → TalkPhone → HangUp → Stash

B. Cigarette Sequence:
  TakeOut → SmokeIn → Smoke → SmokeOut → Stash

CRITICAL RULE:
  - Must use spawnable sequence even if same object type exists in world
  - Spawnable objects are "pulled from inventory", not world objects

Validation:
  - Every TakeOut must have matching Stash by same actor
  - Spawnable actions (AnswerPhone, SmokeIn, etc.) only valid between TakeOut/Stash

6. MUSIC PLAYER SEQUENCE
----------------------
Rule: TurnOn → Dance → TurnOff

Consequences:
  - Must TurnOff before Move to different location
  - Dance only valid while music player is TurnedOn

Validation:
  - TurnOn and TurnOff must be paired
  - Dance events must be temporally between TurnOn and TurnOff

7. OBJECT TRANSFER (Give/INV-Give)
--------------------------------
Rule: Giver and Receiver perform synchronized actions

Structure:
  - Giver: Give(giver, receiver, object)
  - Receiver: INV-Give(receiver, giver, object)
  - MUST have "starts_with" relation to synchronize

Example:
  {
    "give_sync": {"type": "starts_with"},
    "alice_give": {
      "action": "Give",
      "entities": ["alice", "bob", "book"],
      "relations": ["give_sync"],
      "next": "..."
    },
    "bob_receive": {
      "action": "INV-Give",
      "entities": ["bob", "alice", "book"],
      "relations": ["give_sync"],
      "next": "..."
    }
  }

Validation:
  - Every Give must have matching INV-Give
  - Both must reference same relation with type "starts_with"
  - Entity order must match: Give(A,B,obj) ↔ INV-Give(B,A,obj)

8. MULTI-ACTOR INTERACTIONS (Handshake, Hug, Kiss, Talk)
------------------------------------------------------
Rule: Both actors perform same action simultaneously with "starts_with" relation

Actions: Handshake, Hug, Kiss, Talk
Structure:
  - Actor1: Action(actor1, actor2)
  - Actor2: Action(actor2, actor1)
  - MUST have "starts_with" relation

Example (Kiss):
  {
    "kiss_sync": {"type": "starts_with"},
    "alice_kiss": {
      "action": "Kiss",
      "entities": ["alice", "bob"],
      "relations": ["kiss_sync"],
      "next": "..."
    },
    "bob_kiss": {
      "action": "Kiss",
      "entities": ["bob", "alice"],
      "relations": ["kiss_sync"],
      "next": "..."
    }
  }

Validation:
  - Every Handshake/Hug/Kiss/Talk must have matching paired action
  - Both must reference same relation with type "starts_with"
  - Entity order must be mirrored: Action(A,B) ↔ Action(B,A)

9. MOVE BEFORE LOCATION CHANGE
----------------------------
Rule: If actor changes region, Move action must occur first

Structure:
  - Move has TWO locations: Move([actor], [source_region, target_region])
  - Move must be BEFORE any action in target_region

Example:
  {
    "alice_move": {
      "action": "Move",
      "entities": ["alice"],
      "locations": ["kitchen", "bedroom"],
      "relations": [],
      "next": "alice_sleep"
    },
    "alice_sleep": {
      "action": "Sleep",
      "entities": ["alice", "bed"],
      "locations": ["bedroom"],
      "relations": [],
      "next": null
    }
  }

Validation:
  - Track actor location throughout action chain
  - If location changes, ensure Move action exists
  - Move.locations[1] must match next action's location
"""

# =============================================================================
# PART 4: TEMPORAL PATTERNS FROM REFERENCE GRAPHS
# =============================================================================

COMMON_PATTERNS = """
COMMON TEMPORAL PATTERNS
========================

These patterns appear frequently in valid GEST graphs:

PATTERN 1: Synchronized Group Actions (starts_with)
--------------------------------------------------
Use Case: Multiple actors begin same action simultaneously
Example: Group sits down to eat together

Structure:
{
  "all_sit_sync": {"type": "starts_with"},
  "alice_sit": {"relations": ["all_sit_sync"], "next": "alice_eat"},
  "bob_sit": {"relations": ["all_sit_sync"], "next": "bob_eat"},
  "carol_sit": {"relations": ["all_sit_sync"], "next": "carol_eat"}
}

PATTERN 2: Sequential Cross-Actor Dependencies (before/after)
-----------------------------------------------------------
Use Case: One actor's action must complete before another's begins
Example: Bob finishes smoking before Alice stands up

Structure:
{
  "bob_smoke_before_alice_stand": {
    "source": "bob_smoke",
    "type": "before",
    "target": "alice_stand"
  },
  "bob_smoke": {"relations": ["bob_smoke_before_alice_stand"], "next": "bob_sit"},
  "alice_stand": {"relations": ["alice_stand_after_bob_smoke"], "next": "alice_move"}
}

Note: "before" and "after" are inverses, so you need matching relation IDs:
  - bob_smoke has relation: "bob_smoke_before_alice_stand"
  - alice_stand has relation: "alice_stand_after_bob_smoke"
  - Both relations reference the same constraint

PATTERN 3: Branching Dependencies (one-to-many)
--------------------------------------------
Use Case: One actor's action triggers multiple actors to begin their sequences
Example: Bob leaves, triggering Alice, John, and Nancy to start their actions

Structure:
{
  "bob_leave": {
    "relations": ["b_before_a", "b_before_j", "b_before_n"],
    "next": null
  },
  "b_before_a": {"source": "bob_leave", "type": "before", "target": "alice_start"},
  "b_before_j": {"source": "bob_leave", "type": "before", "target": "john_start"},
  "b_before_n": {"source": "bob_leave", "type": "before", "target": "nancy_start"},

  "alice_start": {"relations": ["a_after_b"], "next": "..."},
  "john_start": {"relations": ["j_after_b"], "next": "..."},
  "nancy_start": {"relations": ["n_after_b"], "next": "..."}
}

PATTERN 4: Converging Dependencies (many-to-one)
---------------------------------------------
Use Case: Multiple actors must finish before one actor can begin
Example: Alice and Bob both finish eating before waiter clears table

Structure:
{
  "alice_finish": {"relations": ["a_before_clear"], "next": "alice_stand"},
  "bob_finish": {"relations": ["b_before_clear"], "next": "bob_stand"},

  "a_before_clear": {"source": "alice_finish", "type": "before", "target": "waiter_clear"},
  "b_before_clear": {"source": "bob_finish", "type": "before", "target": "waiter_clear"},

  "waiter_clear": {
    "relations": ["clear_after_a", "clear_after_b"],
    "next": "..."
  }
}

PATTERN 5: Parallel Independent Chains
------------------------------------
Use Case: Multiple actors perform separate action sequences with no cross-dependencies
Example: Alice works at desk while Bob sleeps in bedroom

Structure:
{
  "starting_actions": {
    "alice": "alice_sit",
    "bob": "bob_move"
  },
  "alice_sit": {"relations": [], "next": "alice_open_laptop"},
  "alice_open_laptop": {"relations": [], "next": "alice_type"},
  "alice_type": {"relations": [], "next": null},

  "bob_move": {"relations": [], "next": "bob_get_on_bed"},
  "bob_get_on_bed": {"relations": [], "next": "bob_sleep"},
  "bob_sleep": {"relations": [], "next": "bob_get_off_bed"}
}

Note: No cross-actor relations needed when actions are truly independent

PATTERN 6: Object Lifecycle with Transfer
---------------------------------------
Use Case: Object moves between actors through Give/INV-Give
Example: Alice picks up book, reads it, gives to Bob, Bob reads it

Structure:
{
  "alice_pickup": {"relations": [], "next": "alice_read"},
  "alice_read": {"relations": [], "next": "alice_give"},
  "alice_give": {"relations": ["give_sync"], "next": "alice_putdown"},

  "bob_inv_give": {"relations": ["give_sync"], "next": "bob_read"},
  "give_sync": {"type": "starts_with"},

  "bob_read": {"relations": [], "next": "bob_putdown"},
  "bob_putdown": {"relations": [], "next": null}
}

Note: Alice puts down HER inventory copy, Bob now has the object
"""

# =============================================================================
# PART 5: VALIDATION RULES
# =============================================================================

VALIDATION_RULES = """
VALIDATION RULES CHECKLIST
==========================

MANDATORY CHECKS (must all pass):

1. STRUCTURAL COMPLETENESS
   □ Every actor appears in starting_actions
   □ Every event has "next" field (not undefined/missing)
   □ Every "next" value is either valid event_id OR null if it is the last one
   □ All events reachable from starting_actions via "next" chain
   □ No "next" pointers to different actor's events
   □ All chains end with "next": null

2. EXIST EVENT VALIDATION
   □ Every actor has Exist event
   □ Every object referenced has Exist event
   □ Exist event ID matches entity name exactly
   □ Exist events are in starting_actions
   □ All Exist events occur before usage

3. ACTION LIFECYCLE VALIDATION
   □ Every SitDown has matching StandUp by same actor
   □ Every GetOn has matching GetOff by same actor on same object
   □ Every PickUp has matching PutDown by same actor for same object
   □ Every TakeOut has matching Stash by same actor
   □ Every TurnOn (music) has matching TurnOff by same actor
   □ No Move between GetOn/GetOff pairs
   □ No Move between SitDown/StandUp pairs
   □ StandUp occurs before Move to new location

4. MULTI-ACTOR INTERACTION VALIDATION
   □ Every Give has matching INV-Give with starts_with relation
   □ Every Handshake has matching paired Handshake with starts_with relation
   □ Every Hug has matching paired Hug with starts_with relation
   □ Every Kiss has matching paired Kiss with starts_with relation
   □ Every Talk has matching paired Talk with starts_with relation
   □ Entity order correct: Give(A,B,obj) ↔ INV-Give(B,A,obj)
   □ Entity order correct: Action(A,B) ↔ Action(B,A)

5. CROSS-ACTOR RELATION VALIDATION
   □ All relation IDs in "relations" arrays have definitions
   □ All relation types are valid: starts_with | before | after
   □ before/after relations connect different actors
   □ Relation source/target events both exist
   □ Both events reference the relation in their "relations" arrays

6. LOCATION VALIDATION
   □ Move action exists before location change
   □ Move.locations has exactly 2 elements [source, target]
   □ Move.locations[1] matches next action's location
   □ All locations are valid episode regions
   □ Actions only use objects in current location

7. ACTION VALIDITY
   □ All action names from valid action list (simulation_environment_capabilities.json)
   □ All entity references exist (actors/objects)
   □ Correct number of entities for each action type
   □ POI-specific actions only at correct POI type (e.g., OpenLaptop only at laptop_chair)

8. SPAWNABLE OBJECT VALIDATION
   □ Phone actions only between TakeOut/Stash
   □ Cigarette actions only between TakeOut/Stash
   □ No PickUp of spawnable objects (use TakeOut instead)

9. TEMPORAL CONSISTENCY
   □ No circular dependencies in before/after relations
   □ starts_with relations only between simultaneous-valid actions
   □ Temporal ordering is logically consistent
"""

# =============================================================================
# PART 6: DECISION TREES
# =============================================================================

DECISION_TREES = """
DECISION TREES FOR SCENE DETAIL AGENT
======================================

DECISION 1: When to use temporal vs logical vs semantic relations?
----------------------------------------------------------------
Question: How do I know which relation type to use?

IF relationship is about WHEN events happen:
  → Use TEMPORAL relation (before | after | starts_with)
  Examples:
    - "Alice sits BEFORE Bob arrives"
    - "They kiss AT THE SAME TIME" (starts_with)
    - "Alarm rings AFTER sun rises"

ELIF relationship is about WHY events happen:
  → Use LOGICAL relation (causes | enables | prevents)
  Examples:
    - "Phone ringing CAUSES Alice to answer" (causes)
    - "Getting key ENABLES opening door" (enables)
    - "Locking door PREVENTS entry" (prevents)

ELIF relationship is about WHAT events mean narratively:
  → Use SEMANTIC relation (motivates | interrupts)
  Examples:
    - "Hunger MOTIVATES eating" (motivates)
    - "Phone call INTERRUPTS conversation" (interrupts)

ELSE:
  → No relation needed (independent events)


DECISION 2: When to use "next" vs "before/after" relation?
--------------------------------------------------------
Question: Should I use "next" field or temporal relation?

IF both events belong to SAME actor:
  → Use "next" field
  Example:
    {
      "alice_sit": {"next": "alice_eat"},
      "alice_eat": {"next": "alice_stand"}
    }

ELIF events belong to DIFFERENT actors:
  → Use temporal relation (before | after | starts_with)
  Example:
    {
      "alice_sit": {"relations": ["rel1"], "next": "alice_eat"},
      "bob_arrive": {"relations": ["rel1"], "next": "bob_sit"},
      "rel1": {"source": "bob_arrive", "type": "before", "target": "alice_sit"}
    }


DECISION 3: When to use starts_with vs concurrent?
------------------------------------------------
Question: Both actors do things "at the same time" - which type?

IF actions MUST be synchronized (multi-actor interactions):
  → Use "starts_with"
  Required for: Give/INV-Give, Handshake, Hug, Kiss, Talk
  Example: Alice and Bob kiss

ELIF actions just happen to overlap temporally:
  → Use "concurrent" (or no relation if truly independent)
  Example: Alice types while Bob sleeps
  Note: "concurrent" is defined but rarely used in practice

Recommendation: Default to "starts_with" for synchronized actions,
                no relation for independent parallel actions


DECISION 4: How to handle object lifecycle?
-----------------------------------------
Question: Actor needs to use an object - what sequence?

IF object is PORTABLE (can be picked up):
  → Use: PickUp → [use actions] → PutDown
  Objects: drinks, food, remote, dumbbells
  Example: PickUp(alice, glass) → Drink → PutDown(alice, glass)

ELIF object is EQUIPMENT (must get on/off):
  → Use: GetOn → [use actions] → GetOff
  Objects: treadmill, bed, benchpress, gym_bike
  Example: GetOn(alice, bed) → Sleep → GetOff(alice, bed)

ELIF object is SPAWNABLE (inventory item):
  → Use: TakeOut → [spawnable actions] → Stash
  Objects: phone, cigarette
  Example: TakeOut(phone) → AnswerPhone → TalkPhone → HangUp → Stash(phone)

ELIF object is FURNITURE with POI:
  → Use: SitDown → [POI actions] → StandUp
  Objects: chairs with laptop/table POI
  Example: SitDown(alice, chair) → OpenLaptop → TypeOnKeyboard → CloseLaptop → StandUp


DECISION 5: How to structure starting_actions?
--------------------------------------------
Question: What goes in starting_actions for each actor?

IF actor and objects both need to exist:
  → Use Exist event as starting action
  Example:
    {
      "starting_actions": {
        "alice": "alice",
        "table": "table"
      },
      "alice": {"next": "alice_sit"},
      "table": {"next": null}
    }

ELIF story begins with action (not just existence):
  → Still use Exist, chain to first action
  Example:
    {
      "starting_actions": {"alice": "alice"},
      "alice": {"next": "alice_move"}
    }

CRITICAL: Every actor/object MUST be in starting_actions
"""

# =============================================================================
# PART 7: PYTHON VALIDATION CODE TEMPLATE
# =============================================================================

VALIDATION_CODE_TEMPLATE = """
# Python validation functions for temporal rules
# These should be added to utils/temporal_validation.py

from typing import Dict, List, Set, Tuple, Optional
from schemas.gest import GEST, GESTEvent

class TemporalValidator:
    '''Validates temporal constraints in GEST structures'''

    def __init__(self, gest: GEST):
        self.gest = gest
        self.errors: List[str] = []
        self.warnings: List[str] = []

    def validate_all(self) -> Tuple[bool, List[str], List[str]]:
        '''Run all validation checks'''
        self.errors = []
        self.warnings = []

        self._validate_structural_completeness()
        self._validate_exist_events()
        self._validate_action_lifecycles()
        self._validate_multi_actor_interactions()
        self._validate_cross_actor_relations()
        self._validate_locations()

        return len(self.errors) == 0, self.errors, self.warnings

    def _validate_structural_completeness(self):
        '''Check temporal structure is complete'''
        temporal = self.gest.temporal

        # Check starting_actions exists
        if 'starting_actions' not in temporal:
            self.errors.append("Missing 'starting_actions' in temporal dictionary")
            return

        starting_actions = temporal['starting_actions']

        # Extract all actor names from events
        all_actors = set()
        for event_id, event in self.gest.events.items():
            if event.entities:
                all_actors.add(event.entities[0])  # First entity is usually actor

        # Check every actor in starting_actions
        for actor in all_actors:
            if actor not in starting_actions:
                self.errors.append(f"Actor '{actor}' not in starting_actions")

        # Check all events have "next" field
        for event_id, event_temporal in temporal.items():
            if event_id == 'starting_actions':
                continue
            if isinstance(event_temporal, dict):
                if 'next' not in event_temporal:
                    self.errors.append(f"Event '{event_id}' missing 'next' field")
                elif event_temporal['next'] is not None:
                    next_id = event_temporal['next']
                    if next_id not in self.gest.events:
                        self.errors.append(f"Event '{event_id}' has invalid next: '{next_id}'")

        # Check all chains end with null
        self._check_chain_completeness(starting_actions)

    def _check_chain_completeness(self, starting_actions: Dict[str, str]):
        '''Verify all chains reachable and end with null'''
        for actor, start_event in starting_actions.items():
            visited = set()
            current = start_event

            while current is not None:
                if current in visited:
                    self.errors.append(f"Circular chain detected for actor '{actor}' at event '{current}'")
                    break

                visited.add(current)

                if current not in self.gest.temporal:
                    self.errors.append(f"Event '{current}' in chain but not in temporal dictionary")
                    break

                current = self.gest.temporal[current].get('next')

    def _validate_exist_events(self):
        '''Check all entities have Exist events'''
        exist_events = set()

        for event_id, event in self.gest.events.items():
            if event.action == 'Exist':
                if event.entities:
                    exist_events.add(event.entities[0])

        # Check all entities referenced have Exist events
        for event_id, event in self.gest.events.items():
            if event.action == 'Exist':
                continue
            if event.entities:
                for entity in event.entities:
                    if entity not in exist_events:
                        self.errors.append(f"Entity '{entity}' used in '{event_id}' but has no Exist event")

    def _validate_action_lifecycles(self):
        '''Check action lifecycle sequences (SitDown/StandUp, PickUp/PutDown, etc.)'''

        # Track lifecycle pairs
        sit_down = {}  # actor -> event_id
        get_on = {}    # (actor, object) -> event_id
        pick_up = {}   # (actor, object) -> event_id
        take_out = {}  # actor -> event_id
        turn_on = {}   # actor -> event_id

        for event_id, event in self.gest.events.items():
            actor = event.entities[0] if event.entities else None

            if event.action == 'SitDown':
                sit_down[actor] = event_id
            elif event.action == 'StandUp':
                if actor not in sit_down:
                    self.errors.append(f"StandUp '{event_id}' without matching SitDown for actor '{actor}'")
                else:
                    del sit_down[actor]

            elif event.action == 'GetOn':
                obj = event.entities[1] if len(event.entities) > 1 else None
                get_on[(actor, obj)] = event_id
            elif event.action == 'GetOff':
                obj = event.entities[1] if len(event.entities) > 1 else None
                if (actor, obj) not in get_on:
                    self.errors.append(f"GetOff '{event_id}' without matching GetOn for '{actor}' on '{obj}'")
                else:
                    del get_on[(actor, obj)]

            elif event.action == 'PickUp':
                obj = event.entities[1] if len(event.entities) > 1 else None
                pick_up[(actor, obj)] = event_id
            elif event.action == 'PutDown':
                obj = event.entities[1] if len(event.entities) > 1 else None
                if (actor, obj) not in pick_up:
                    self.errors.append(f"PutDown '{event_id}' without matching PickUp for '{actor}' of '{obj}'")
                else:
                    del pick_up[(actor, obj)]

            elif event.action == 'TakeOut':
                take_out[actor] = event_id
            elif event.action == 'Stash':
                if actor not in take_out:
                    self.errors.append(f"Stash '{event_id}' without matching TakeOut for actor '{actor}'")
                else:
                    del take_out[actor]

            elif event.action == 'TurnOn':
                turn_on[actor] = event_id
            elif event.action == 'TurnOff':
                if actor not in turn_on:
                    self.warnings.append(f"TurnOff '{event_id}' without matching TurnOn for actor '{actor}'")
                else:
                    del turn_on[actor]

        # Check for unclosed lifecycles
        for actor in sit_down:
            self.errors.append(f"SitDown for actor '{actor}' without matching StandUp")
        for (actor, obj) in get_on:
            self.errors.append(f"GetOn for '{actor}' on '{obj}' without matching GetOff")
        for (actor, obj) in pick_up:
            self.errors.append(f"PickUp for '{actor}' of '{obj}' without matching PutDown")
        for actor in take_out:
            self.errors.append(f"TakeOut for actor '{actor}' without matching Stash")

    def _validate_multi_actor_interactions(self):
        '''Check multi-actor interactions have starts_with relations'''

        # Actions that require synchronization
        sync_actions = {'Give', 'Handshake', 'Hug', 'Kiss', 'Talk'}

        for event_id, event in self.gest.events.items():
            if event.action in sync_actions:
                # Check has relations with starts_with
                temporal_entry = self.gest.temporal.get(event_id, {})
                relations = temporal_entry.get('relations', [])

                if not relations:
                    self.errors.append(f"{event.action} '{event_id}' missing starts_with relation")
                    continue

                # Check at least one relation is starts_with
                has_starts_with = False
                for rel_id in relations:
                    if rel_id in self.gest.temporal:
                        rel = self.gest.temporal[rel_id]
                        if isinstance(rel, dict) and rel.get('type') == 'starts_with':
                            has_starts_with = True
                            break

                if not has_starts_with:
                    self.errors.append(f"{event.action} '{event_id}' missing starts_with relation")

    def _validate_cross_actor_relations(self):
        '''Check cross-actor relations are valid'''

        # Collect all relation definitions
        relations = {}
        for key, value in self.gest.temporal.items():
            if isinstance(value, dict) and 'type' in value and 'source' in value:
                relations[key] = value

        # Validate each relation
        for rel_id, rel in relations.items():
            rel_type = rel.get('type')
            source = rel.get('source')
            target = rel.get('target')

            # Check type is valid
            if rel_type not in ['starts_with', 'before', 'after']:
                self.errors.append(f"Invalid relation type '{rel_type}' in '{rel_id}'")

            # Check source/target exist
            if source and source not in self.gest.events:
                self.errors.append(f"Relation '{rel_id}' references non-existent source '{source}'")
            if target and target not in self.gest.events:
                self.errors.append(f"Relation '{rel_id}' references non-existent target '{target}'")

            # Check both events reference the relation
            if source:
                source_temporal = self.gest.temporal.get(source, {})
                source_relations = source_temporal.get('relations', [])
                if rel_id not in source_relations:
                    self.warnings.append(f"Source event '{source}' doesn't reference relation '{rel_id}'")

            if target:
                target_temporal = self.gest.temporal.get(target, {})
                target_relations = target_temporal.get('relations', [])
                if rel_id not in target_relations:
                    self.warnings.append(f"Target event '{target}' doesn't reference relation '{rel_id}'")

    def _validate_locations(self):
        '''Check location consistency and Move actions'''

        # Track actor locations throughout chains
        actor_locations = {}

        for actor, start_event in self.gest.temporal.get('starting_actions', {}).items():
            current = start_event
            current_location = None

            while current:
                event = self.gest.events.get(current)
                if not event:
                    break

                # Get event location
                event_location = event.locations[0] if event.locations else None

                if event.action == 'Move':
                    # Move should have 2 locations
                    if len(event.locations) != 2:
                        self.errors.append(f"Move action '{current}' should have 2 locations")
                    else:
                        current_location = event.locations[1]  # Target location

                elif event_location:
                    # Check location consistency
                    if current_location and event_location != current_location:
                        self.errors.append(
                            f"Event '{current}' in location '{event_location}' "
                            f"but actor '{actor}' is in '{current_location}' "
                            f"(missing Move action?)"
                        )
                    current_location = event_location

                # Next event in chain
                current = self.gest.temporal.get(current, {}).get('next')
"""

# =============================================================================
# PART 8: IMPLEMENTATION GUIDE FOR SCENE DETAIL AGENT
# =============================================================================

IMPLEMENTATION_GUIDE = """
IMPLEMENTATION GUIDE: SceneDetailAgent
======================================

STEP 1: Initialize with Temporal Rules
-------------------------------------
In agents/scene_detail_agent.py:

class SceneDetailAgent(BaseAgent[DualOutput]):
    def __init__(self, config: Dict[str, Any]):
        super().__init__(
            config=config,
            agent_name="scene_detail_agent",
            output_schema=DualOutput
        )

        # Hardcode temporal rules
        self.temporal_rules = {
            'relation_types': ['starts_with', 'before', 'after'],
            'lifecycle_sequences': {
                'SitDown': ['OpenLaptop', 'TypeOnKeyboard', 'Eat', 'CloseEyes'],
                'GetOn': {
                    'treadmill': ['JogTreadmill'],
                    'bed': ['Sleep'],
                    'benchpress': ['BenchpressWorkOut'],
                    'gym_bike': ['PedalGymBike']
                },
                'PickUp': ['Drink', 'Eat', 'LookAt', 'DumbbellsWorkOut'],
                'TakeOut': {
                    'phone': ['AnswerPhone', 'TalkPhone', 'HangUp'],
                    'cigarette': ['SmokeIn', 'Smoke', 'SmokeOut']
                }
            },
            'synchronized_actions': ['Give', 'Handshake', 'Hug', 'Kiss', 'Talk'],
            'mandatory_pairs': {
                'SitDown': 'StandUp',
                'GetOn': 'GetOff',
                'PickUp': 'PutDown',
                'TakeOut': 'Stash',
                'TurnOn': 'TurnOff'
            }
        }

STEP 2: Build System Prompt with Temporal Rules
---------------------------------------------
def build_system_prompt(self, context: Dict[str, Any]) -> str:
    return f'''
You are a Scene Detail Agent for GTA San Andreas story generation.

TEMPORAL RELATION RULES:
========================

1. VALID RELATION TYPES: {', '.join(self.temporal_rules['relation_types'])}
   - starts_with: Events begin simultaneously (multi-actor sync)
   - before: Source completes before target begins
   - after: Source begins after target completes

2. TEMPORAL STRUCTURE:
   - Every actor MUST appear in starting_actions
   - Every event MUST have "next" field (event_id or null)
   - "next" ONLY connects same actor's events, NEVER cross-actor
   - Cross-actor relations use "relations" field with relation IDs

3. MANDATORY ACTION SEQUENCES:
   {self._format_lifecycle_rules()}

4. SYNCHRONIZED ACTIONS (require starts_with):
   {', '.join(self.temporal_rules['synchronized_actions'])}

5. MANDATORY PAIRS:
   {self._format_mandatory_pairs()}

[Rest of prompt with examples and task description]
'''

def _format_lifecycle_rules(self) -> str:
    '''Format lifecycle sequences for prompt'''
    rules = []
    for action, allowed in self.temporal_rules['lifecycle_sequences'].items():
        if isinstance(allowed, dict):
            for obj, actions in allowed.items():
                rules.append(f"   {action} → {' → '.join(actions)} → {self.temporal_rules['mandatory_pairs'][action]} (on {obj})")
        else:
            rules.append(f"   {action} → [{', '.join(allowed)}] → {self.temporal_rules['mandatory_pairs'][action]}")
    return '\\n'.join(rules)

def _format_mandatory_pairs(self) -> str:
    '''Format mandatory action pairs for prompt'''
    return '\\n'.join([f"   {start} ↔ {end}" for start, end in self.temporal_rules['mandatory_pairs'].items()])

STEP 3: Post-Process and Validate Output
---------------------------------------
def execute(self, context: Dict[str, Any]) -> DualOutput:
    '''Execute agent with validation'''

    # Call LLM
    result = super().execute(context)

    # Validate temporal structure
    validator = TemporalValidator(result.gest)
    is_valid, errors, warnings = validator.validate_all()

    if not is_valid:
        logger.error("temporal_validation_failed", errors=errors)
        # Option 1: Retry with error feedback
        # Option 2: Raise exception
        # Option 3: Auto-fix common issues
        raise ValueError(f"Temporal validation failed: {errors}")

    if warnings:
        logger.warning("temporal_validation_warnings", warnings=warnings)

    return result

STEP 4: Include Reference Examples in Context
-------------------------------------------
def build_user_prompt(self, context: Dict[str, Any]) -> str:
    # Load reference graph with good temporal structure
    reference = context.get('reference_graphs', {}).get('incredibly_complex')

    return f'''
REFERENCE EXAMPLE (correct temporal structure):
{json.dumps(reference['temporal'], indent=2)}

NOW YOUR TASK:
Generate scene detail for:
{context['input_scene']}

Ensure all temporal rules are followed!
'''
"""

# =============================================================================
# PART 9: SUMMARY AND RECOMMENDATIONS
# =============================================================================

SUMMARY = """
SUMMARY AND RECOMMENDATIONS
============================

KEY POINTS:
-----------
1. The temporal system uses TWO levels:
   - Level 1: Same-actor chains via "next" field
   - Level 2: Cross-actor relations via "relations" field

2. Only THREE temporal relation types are used in practice:
   - starts_with (synchronization)
   - before (sequential precedence)
   - after (sequential succession)

3. Action lifecycles are MANDATORY and MUST be enforced:
   - SitDown/StandUp
   - GetOn/GetOff
   - PickUp/PutDown
   - TakeOut/Stash
   - TurnOn/TurnOff

4. Multi-actor interactions REQUIRE starts_with relations:
   - Give ↔ INV-Give
   - Handshake, Hug, Kiss, Talk

IMMEDIATE ACTIONS:
------------------
1. Fix schema inconsistency:
   - Update TemporalRelation type to: ["starts_with", "after", "before"]
   - Remove "concurrent" and "meanwhile" or document their use

2. Implement TemporalValidator class (from template above)

3. Create SceneDetailAgent with hardcoded temporal rules

4. Update prompts_about_temporal relations.md with formalized rules

5. Add validation step after GEST generation

LONG-TERM IMPROVEMENTS:
-----------------------
1. Add reference examples for:
   - Spawnable object sequences
   - Gym equipment usage
   - Complex multi-actor coordination

2. Create automated GEST validator CLI tool

3. Add temporal relation visualization tool

4. Consider duration/timing metadata for future enhancements

CRITICAL RULES TO NEVER FORGET:
-------------------------------
✓ Every actor in starting_actions
✓ Every event has "next" field
✓ "next" only for same actor
✓ Cross-actor uses "relations"
✓ All lifecycles must close (SitDown→StandUp, etc.)
✓ Multi-actor interactions need starts_with
✓ Move before location change
✓ Exist events first

These rules are MANDATORY and violations will cause MTA simulation errors.
"""

# =============================================================================
# END OF FORMALIZED TEMPORAL RULES
# =============================================================================

if __name__ == "__main__":
    print("="*80)
    print("FORMALIZED TEMPORAL RULES FOR MULTIAGENT STORY SYSTEM")
    print("="*80)
    print("\nThis file contains comprehensive temporal relation rules.")
    print("Copy the relevant sections into your implementation:\n")
    print("1. TEMPORAL_RELATION_TYPES → schema definition")
    print("2. TEMPORAL_STRUCTURE → documentation")
    print("3. ACTION_LIFECYCLE_RULES → agent prompts and validation")
    print("4. COMMON_PATTERNS → reference examples")
    print("5. VALIDATION_RULES → checklist for testing")
    print("6. DECISION_TREES → agent decision logic")
    print("7. VALIDATION_CODE_TEMPLATE → Python implementation")
    print("8. IMPLEMENTATION_GUIDE → SceneDetailAgent integration")
    print("9. SUMMARY → quick reference")
    print("\n" + "="*80)
