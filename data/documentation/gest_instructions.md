# I. FOUNDATIONAL STRUCTURE
## Graph Definition
A GEST is a directed graph G = (V, E) where:

V = set of event nodes
E = set of edges, E ⊆ V × V

## Node Structure (Vi)
Each node Vi = (action, entities, location, timeframe, properties) where:
action = the main action; string
entities = list of entities involved in the action; [string]
location = list of locations where action takes place; [string]
timeframe = list of timeframes when action takes place; [string]
properties = additional properties; dict <property:value>
## Edge Types (Ei)
Edges can be:

Temporal: Based on Allen's interval algebra (after, before, starts_with, meanwhile, next, concurrent)
Spatial: on top, behind, left of, etc.
Logical: and, or, cause/effect, double implication
Semantic: relationships between events


# II. CORE PRINCIPLES
## Everything is an Event

Subjects → events (type: "Exists")
Objects → events (type: "Exists")
Actions → events
Interactions/edges → can themselves represent events

## Hierarchical Representation

Any event node can expand into a more detailed GEST
Any GEST graph can collapse into a single hyper-event node
Infinite recursive process: nodes expand and collapse into events


# III. ENTITY RULES
## Exists Nodes
All actors and objects require "Exists" event nodes:
Actor Exists Node:
json{
  "Action": "Exists",
  "Entities": ["actor_id"],
  "Location": null,
  "Timeframe": null,
  "Properties": {"Gender": 1 or 2, "Name": "ActorName"}
}
Object Exists Node:
json{
  "Action": "Exists",
  "Entities": ["object_id"],
  "Location": null or ["location"],
  "Timeframe": null,
  "Properties": {"Type": "ObjectType"}
}
## Entity References

Nodes can reference other nodes in entities field
"Same entity" edges connect action nodes to their actor/object Exists nodes
For complex interactions, entities can reference other action nodes

## Entity Identity Rules
Personal Objects: Phone, cup, backpack are unique per owner unless transferred

"John picks up his backpack. John gives the backpack to Mary" → ONE backpack node
Track ownership through transfer chain

"Another" Keyword: Signals new entity instance

"A man talks. Another man enters" → TWO separate man nodes
Without "another": default to same entity


# IV. CONTEXT INFERENCE
## Location Inference
If action contains no explicit location:

Infer from last location where the entity (actor) was found
Example: "John is in the playground. John picked up the football."
→ "picked up" location = "playground" (inferred from context)

## Timeframe Inference
If action contains no explicit timeframe:

Infer from last mentioned timeframe for that entity


# V. TEMPORAL STRUCTURE
## Basic Temporal Chain
json{
  "temporal": {
    "starting_actions": {
      "actor_id": "first_action_id"
    },
    "action_id": {
      "relations": null or ["constraint_id"],
      "next": "next_action_id" or null
    }
  }
}
## Temporal Constraints
Starts With (simultaneous):
json{
  "tm_id": {"type": "starts_with"},
  "action1": {"relations": ["tm_id"], "next": "..."},
  "action2": {"relations": ["tm_id"], "next": "..."}
}
Before/After (sequential):
json{
  "constraint_id": {
    "source": "action1",
    "type": "before",
    "target": "action2"
  }
}

# VI. JSON OUTPUT FORMAT
## Complete Structure
json{
  "actor_id": { /* Exists node */ },
  "object_id": { /* Exists node */ },
  "action_id": {
    "Action": "ActionType",
    "Entities": ["actor_id", "object_id"],
    "Location": ["location"] or null,
    "Timeframe": ["timeframe"] or null,
    "Properties": {}
  },
  "temporal": {
    "starting_actions": { /* actor: first_action mapping */ },
    "action_id": { /* next and relations */ },
    "constraint_id": { /* constraint definition */ }
  }
}
## Required Fields

All nodes MUST include all five fields: Action, Entities, Location, Timeframe, Properties
Use null for absent values (never omit fields)
Location and Timeframe are always lists (even if single element or null)


# VII. ABSTRACTION LEVELS
## Hyper-Events
Abstract concepts represented as single nodes:
json{
  "workout_routine": {
    "Action": "WorkoutRoutine",
    "Entities": ["john"],
    "Location": ["gym"],
    "Timeframe": ["morning"],
    "Properties": {"Type": "HyperEvent"}
  }
}
## Expansion

A "revolution" is an event that can expand into multiple political/social events
"Bought a watch" expands into: finding → negotiating → paying → receiving
Each sub-event can further expand (e.g., paying → currency + amount + method)

## Collapse

Multiple connected events collapse into single abstract node
Generates infinite recursive process of expansion and collapse