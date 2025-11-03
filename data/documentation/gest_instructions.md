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


# V. Relations structure
## 1. Temporal Relations
### Supported temporal relations
Across actors:
            "after",
            "before",
            "starts_with",
            "concurrent",
Within the same actor:
  "next"
### Basic Temporal Chain
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
### Temporal Constraints
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

## 2. LOGICAL Relations
Purpose: Express logical connections and implications between events
Relation Set:

Boolean: and, or, not
Causal: causes, caused_by, enables, prevents, blocks
Conditional: implies, implied_by, requires, depends_on
Equivalence: equivalent_to, contradicts, conflicts_with

Guidelines:

Use for reasoning about event dependencies and constraints
Causal relations express "if A happens, then B happens/can happen"

## 3. SEMANTIC Relations
Purpose: Express the MEANING and NATURE of relationships between events using domain-specific verbs
Relation Structure: Use action verbs that describe how one event relates to another
Categories by Intent:

Interaction: interrupts, disrupts, interferes_with, collaborates_with, cooperates_with, competes_with
Influence: motivates, inspires, discourages, persuades, convinces, influences
Response: responds_to, reacts_to, answers, acknowledges, ignores, dismisses
Support/Opposition: supports, assists, helps, opposes, resists, counters, undermines
Communication: tells, informs, asks, questions, commands, requests, warns
Transformation: transforms_into, evolves_from, replaces, substitutes, modifies
Composition: is_part_of, contains_event, includes, comprises, consists_of

Guidelines for Creating Semantic Relations:

Use active, descriptive verbs that capture the specific nature of the relationship
Think domain-specifically: In narratives, use story-appropriate verbs (betrays, rescues, reveals); in technical domains, use domain verbs (compiles_to, inherits_from, implements)
Maintain verb directionality: "A interrupts B" means A is the interruptor; ensure source→target direction is semantically correct
Avoid overlap with other categories:

If it's purely temporal → use temporal
If it's purely spatial → use spatial
If it's purely logical/causal → use logical
Use semantic when the relationship carries domain meaning beyond time/space/logic


Hierarchical relationships are semantic: is_part_of, is_substory_of, expands, summarizes
Emotional/intentional relationships are semantic: loves, fears, desires, intends, plans


Example Applications
A specific case: "E1 interrupts E2, where E2 is a story, because of E1, E3 happens"
json{
  "type": "semantic",
  "relation": "interrupts",
  "source": "E1",
  "target": "E2"
}
Multi-level example:
json// Temporal: When did it happen?
{"temporal": { "e1": {
  "relations": ["t1"],
  "next": null
},
  "t1": {"type": "concurrent", "source": "e1", "target": "e2"}}
,
// Semantic: What was the nature of the relationship?
"semantic": {
  "e1": {
    "relations": ["s1"]
  },
"s1": {"type": "interrupts", "source": "e1", "target": "e2"}
},

// Logical: What was the effect?
"logical": {
  "e1": {
    "relations": ["l1"]
  },
"l1": {"type": "causes", "source": "e1", "target": "e3"}
  }
}

Instructions for LLMs
When creating edges:

Identify the relationship type first: Ask "Am I describing WHEN (temporal), WHERE (spatial), logical dependency (logical), or the semantic nature (semantic)?"
For semantic relations: Use a verb that intuitively describes the relationship. If you can say "Event A [VERB] Event B" naturally in English, that verb is your relation.
Use multiple edges when needed: The same pair of events can have temporal, spatial, AND semantic relationships simultaneously.
Be specific over generic: Prefer interrupts over affects, betrays over interacts_with, compiles_to over relates_to.

## 4. Spatial relations

### Supported spatial relations
[
            "near",
            "behind",
            "left",
            "right",
            "on",
            "in_front"
]

### Structure
The spatial relations are between entities. Currently used for grounding where exactly an object is located in space when e.g. a specific instance from the simulation environment is assigned to an object.
json{    "spatial": {
        "chair16": {
            "relations": [
                {
                    "type": "behind",
                    "target": "desk6"
                }
            ]
        },
}}

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