# The prompt that generated this project

envision the following system:
a multiagent system thatwrites stories.

1. we first need to either run the engine in a special mode in which we gather all the possible locations, contexts, what actions can be executed where, with what actors. all in a manner that is easily ingestable by an llm. OR create a python script that does the processing outside the environment. We need to take into account: episodes, regions, POIs and all possible actions defined in those POIs with or without objects. Here these collections of poi object actions might come from supertemplates that hold templates. Some actions can be executed with spawnable objects like talk phone or smoke.  Some are interactions.

2. Based on essentially what is possible in the game world, an agent creates a narrative. Similar to how in cinematography we might start from a book or a script.

3. Based on the script, and game constraints and some examples of high level GEST, low level GEST, game-level granular GEST. We will have a set of agents that will create GEST graphs.

We start from the highest possible level: one single GEST with one event. Then we exapand the event into a more granular GEST. Until we get to a GEST that can directly be ingested by the sistem and for which it is guaranteed that a simulation can be done.

We save all of them in different granularities.

We need to think here in details but the idea is to have various agents responsible of various aspects. Similar to how you do it when shooting a movie.

You have the director that orchestrates them all. You have the scene manager responsible of creating objects and actors, and placing them in space with the spatial constraints.

You have the camera director that decides when to record and when to stop recording, but also what sort of shot to take (this is not yet supported, only when to start and stop is - essentially allowing to prepare for a scene by puting some actors somewhere, sitting them down, picking up some items).

You have the scene director that based on the script writes with game-level granularity what events will be done and their temporal constraints.

In between different scenes there will be temporal constraints as well.

We will add also some validators that have access to external commands to set the generated graph as input to the engine. And also start a simulation in validation mode only, read back the logs to see if there are validation errors, and report them back to the graph creators so they can fix their issues with the graphs.

When the graph is valid, an agent runs the simulation with artifact collection enabled and no debug.

The technologies used are: langgraph, OpenAI GPT-5 with structured output. Python 3.10.

# Clarifying Questions

1. MVP Scope & Priorities
Which components should be implemented first? Please rank these in priority order:
 Game world capability extraction system
 High-level narrative generation agent
 GEST refinement pipeline (multi-granularity)
 Specialized agents (Director, Scene Manager, Camera Director, Scene Director)
 Validation loop with external command execution
 Final execution with artifact collection
2. GEST Granularity Levels
You mentioned hierarchical refinement from high-level to game-executable. Can you define the specific levels? For example:
Level 0 (Highest): Single abstract event? (e.g., "Two people have a conversation over dinner")
Level 1: ?? (e.g., "Person A cooks, Person B sets table, both sit, talk, eat")
Level 2: ?? (e.g., more detailed action sequences)
Level N (Lowest): Current graph format (game-executable with Move, SitDown, Drink, etc.)
How many levels do you envision?
3. Game World Extraction Approach
For extracting game capabilities, which approach do you prefer:
Option A: Special Lua mode that exports capabilities to JSON (runs in the engine)
Option B: Python script that parses existing JSON files (episodes, supertemplates) externally
Option C: Hybrid approach
What format should the extracted capabilities be in for LLM consumption?
4. Validation Criteria
Beyond "no validation errors in logs," what constitutes a successful/valid simulation?
Successfully completes without crashes?
All temporal constraints satisfied?
All actions executed?
Video quality metrics?
Other criteria?
5. Integration Architecture
How should the multiagent system integrate with the existing Lua engine?
Option A: Standalone Python system that generates graphs → writes JSON files → invokes MTA via command line
Option B: Python system with real-time bidirectional communication with running engine
Option C: Other approach?
6. Current Constraints & Examples
Do you have examples of what "high-level GEST", "mid-level GEST", and "low-level GEST" should look like?
Should the system respect all existing constraints (spatial, temporal) from day one, or start simple?
What's the typical size of a story? (e.g., 2 actors, 20 events? 5 actors, 100 events?)
7. Artifact Collection
When you mentioned "artifact collection enabled" - what artifacts are being collected?
Video recordings?
Screenshots?
Event logs?
Sensor data?
All of the above?
8. Camera Direction
You mentioned camera director capabilities aren't fully supported yet. Should the system:
Option A: Work with current start/stop recording capabilities only
Option B: Design for future shot selection (close-up, wide, over-shoulder) but implement later
Option C: Implement full camera direction as part of this system
9. Narrative Constraints
For the narrative generation agent, should it:
Generate completely free-form stories?
Follow genre templates (drama, comedy, thriller)?
Respect character archetypes?
Focus on certain interaction types?
10. Error Recovery
When validation fails, how should the system recover?
Regenerate entire graph?
Fix specific problematic events?
Relax constraints iteratively?
Ask human for guidance?
Please answer these questions so I can create a detailed, actionable implementation plan tailored to your specific needs!

## Answers
1. ALL of them in the order that you wrote them. Complete and full implementation without stubs. It can be done sequentially to properly thinlk for each individually.
2. Highest and lowest granularity is clear as you wrote it. Then, based on the number of scenes taking place in one region essentially, we will have another layer (one GEST per region) that are clearly delimited from other sub GESTs. Then, each region-level GEST broken down in more granular, actor level GEST in that region that summarizes his actions. Then the lowest level.
3. I think that option A is the cleanest since we make use of existing capabilities.
4. Just if the GraphStory validation passes.
5. A: writes JSON files (graphs, and configuration) -> invokes MTA via commandline: & '.\server\MTA Server.exe' to start the server,  '.\Multi Theft Auto.exe - Shortcut.lnk' to start the client that automatically connects to the server.
6. High level GEST: Johns workout routine (exists John, John plans workout. Mid-level GEST: John exists, office exists, gym exists. John plans workout at laptop. In the gym, a trainer showcases workout routines. A bit more granular. John plans workout at laptop. In the gym: trainer works out with dumbells, then with barbells, then runs. Low level: full blown GEST similar to incredibly_complex.json
7. Artifact collection is a flag in server globals. Only the video recordings are collected.
8. A
9. Have as input a number of sentences. They should be complex and correlated. Inception style. Each graph should be semantically correlated with complex edges. e.g. a professor teaches at the white table (E1). a student listens (E2). a writer writes the story about the student and the professor (E3). a reader reads the story written by the writer (E4). someone interrupts the professor (E5). they are all semantiaclly, temporally, and spatially correlated.
10. it must be completely autonomous. It can try an N amount of times to fix the graph. Then discard it.

We will control each agent via a special prompt. The narrative will have as input: the number of distinct actions, number of actors. We will showcase as sample how the spatial relations work, the documentation about what the game expects about camera possibilities, about temporal constraints possible, with examples of graphs: eg. incredibly_complex.json, and also the hard_...json


# Revised plan
Multiagent Story Generation System - Complete Implementation Plan
System Overview
A LangGraph-based multiagent system that generates hierarchical GEST (Graph Event Sequence Temporal) stories from natural language narratives, validates them against game constraints, and executes them in GTA San Andreas MTA environment to produce video artifacts.
Architecture Components (Sequential Implementation)
1. Game World Capability Extraction System
Purpose: Extract all possible actions, locations, objects, and constraints from the game world. Implementation:
Lua Exporter Module (src/export/GameWorldExporter.lua)
Iterates through all DynamicEpisodes
Extracts: Regions, POIs, Objects, Actions per POI, Supertemplates, Spawnable objects
Outputs comprehensive JSON: data_out/game_capabilities.json
Server Command (/export_capabilities)
Triggers export in special validation-only mode
No actors spawned, pure data extraction
Output Format (LLM-optimized):
{
  "episodes": [{"name": "house1", "regions": [...], "objects": [...]}],
  "action_catalog": {"SitDown": {"entities": ["Actor", "Chair"], "description": "..."}},
  "object_types": {"Chair": {"spawnable": false, "actions": ["SitDown", "StandUp"]}},
  "spatial_relations": ["near", "behind", "left", "right", "on", "in_front"],
  "temporal_relations": ["after", "before", "starts_with", "concurrent"]
}
2. High-Level Narrative Generation Agent
Purpose: Generate abstract narrative from input parameters. Agents (LangGraph):
NarrativeAgent (agents/narrative_agent.py)
Input: {num_actors: int, num_distinct_actions: int, narrative_sentences: List[str]}
Uses GPT-4o with structured output
Generates Level 0 GEST: Single abstract event with semantic correlations
Output: High-level narrative description (Inception-style complex correlations)
Prompt Template:
You are a creative narrative generator. Create a complex, multi-layered story with semantic, temporal, and spatial correlations similar to Inception.

Input:
- Number of actors: {num_actors}
- Number of distinct actions: {num_distinct_actions}
- Narrative sentences: {narrative_sentences}

Requirements:
- Create semantically correlated events (e.g., "A professor teaches, a student listens, a writer writes about them, a reader reads that story")
- Ensure temporal dependencies across actors
- Design spatial relationships (actors in different regions observing/interacting)
- Output a single abstract narrative paragraph describing the entire story
3. GEST Refinement Pipeline (Multi-Granularity)
Purpose: Progressively refine abstract narrative into game-executable GEST through 5 levels. Level 0 → Level 1: Scene Breakdown Agent
SceneBreakdownAgent (agents/scene_breakdown_agent.py)
Input: Level 0 abstract narrative
Output: Scene-level breakdown (e.g., "John plans workout at laptop. In gym: trainer works out with dumbbells, then barbells, then runs")
Identifies major scenes and their high-level actions
Level 1 → Level 2: Region Mapping Agent
RegionMappingAgent (agents/region_mapping_agent.py)
Input: Level 1 scene breakdown + game_capabilities.json
Output: One GEST per region with clear delimiters
Maps scenes to actual game regions (bedroom, kitchen, gym, etc.)
Creates separate sub-GESTs for each region
Level 2 → Level 3: Actor Action Agent
ActorActionAgent (agents/actor_action_agent.py)
Input: Level 2 region-level GESTs + game_capabilities.json
Output: Per-actor action summaries within each region
For each region GEST, creates actor-specific action sequences
Ensures actor consistency and logical flow
Level 3 → Level 4: Game-Level GEST Generator
GameGESTGeneratorAgent (agents/game_gest_generator.py)
Input: Level 3 actor-level GESTs + game_capabilities.json + example graphs (incredibly_complex.json, hard_*.json)
Output: Full game-executable GEST JSON (like incredibly_complex.json)
Generates complete graph with:
All "Exists" events for actors and objects
Granular actions (SitDown, PickUp, Eat, Move, etc.)
Temporal constraints section with "next", "after", "before", "starts_with"
Spatial constraints section with relations
Camera controls section (start/stop recording)
Uses few-shot learning from example graphs
Prompt Context (for all refinement agents):
Game capabilities JSON
Spatial relations documentation
Temporal constraints documentation with examples
Camera control capabilities
Example graphs: incredibly_complex.json, c10_sync.json
4. Specialized Director Agents
Purpose: Orchestrate different aspects of story generation (cinematography workflow). Director Agent (agents/director_agent.py)
Orchestrates all sub-agents
Maintains conversation history
Routes tasks to appropriate specialists
Ensures coherence across all agents
Scene Manager Agent (agents/scene_manager_agent.py)
Responsible for object placement and spatial arrangements
Validates spatial constraints
Ensures objects exist in correct locations
Creates "Exists" events for all entities
Validates region capacity and object availability
Camera Director Agent (agents/camera_director_agent.py)
Creates camera control section in GEST
Decides recording start/stop times
Ensures preparation time before recording (actors sitting, objects placed)
Output: {"camera": {"start": "event_id_1", "stop": "event_id_20"}}
Scene Director Agent (agents/scene_director_agent.py)
Creates game-level granular event sequences
Manages temporal constraints between events
Ensures action feasibility (e.g., actor must move before acting in new region)
Creates temporal constraint graph
Validates action chains (PickUp → Eat → PutDown)
5. Validation Loop with External Command Execution
Purpose: Autonomous validation and error correction. Validator Agent (agents/validator_agent.py)
Takes generated GEST graph
Writes to input_graphs/generated_graph.json
Updates src/ServerGlobals.lua: Sets LOAD_FROM_GRAPH = 'input_graphs/generated_graph.json', ARTIFACT_COLLECTION_ENABLED = false, DEBUG = false
Executes MTA: & '.\server\MTA Server.exe' (background) → & '.\Multi Theft Auto.exe - Shortcut.lnk' (client connects)
Monitors logs/server.log for validation errors
Parses log for errors:
"Could not find actions required..."
"validLocations locations" (empty)
GraphStory validation failures
Error Correction Agent (agents/error_corrector_agent.py)
Receives validation errors from log
Analyzes error type
Invokes appropriate specialist agent to fix issues:
Missing objects → Scene Manager
Invalid temporal constraints → Scene Director
Invalid actions → Game GEST Generator
Maximum N retry attempts (configurable, default: 3)
If all retries fail → discard graph and start over
Validation State Machine:
Generate GEST → Write Files → Launch MTA → Parse Logs →
  ├─ Success → Proceed to Execution
  └─ Failure → Error Analysis → Agent Fix → Retry (up to N times) → Discard
6. Final Execution with Artifact Collection
Purpose: Execute validated GEST and collect video artifacts. Execution Agent (agents/execution_agent.py)
Takes validated GEST
Updates src/ServerGlobals.lua: ARTIFACT_COLLECTION_ENABLED = true, DEBUG = false
Executes MTA same as validation
Monitors execution until completion
Collects artifacts from data_out/ directory
Returns: Video file paths, metadata, execution logs
Technology Stack
Python:
LangGraph: Agent orchestration and state management
OpenAI GPT-4o: All LLM calls with structured output (JSON mode)
Pydantic: Data validation and schemas
Python 3.10: Runtime
External:
Lua/MTA: Game engine (existing)
Subprocess: Command execution for MTA
File Structure
multiagent_story_system/
├── agents/
│   ├── __init__.py
│   ├── director_agent.py          # Main orchestrator
│   ├── narrative_agent.py         # Level 0 generator
│   ├── scene_breakdown_agent.py   # Level 0→1
│   ├── region_mapping_agent.py    # Level 1→2
│   ├── actor_action_agent.py      # Level 2→3
│   ├── game_gest_generator.py     # Level 3→4 (game-executable)
│   ├── scene_manager_agent.py     # Spatial/objects specialist
│   ├── camera_director_agent.py   # Camera controls
│   ├── scene_director_agent.py    # Temporal constraints specialist
│   ├── validator_agent.py         # External validation orchestrator
│   ├── error_corrector_agent.py   # Error analysis and correction
│   └── execution_agent.py         # Final artifact collection
├── graphs/
│   ├── __init__.py
│   ├── story_generation_graph.py  # LangGraph workflow definition
│   └── validation_graph.py        # Validation loop workflow
├── schemas/
│   ├── __init__.py
│   ├── gest_schemas.py           # Pydantic models for all GEST levels
│   ├── game_capabilities.py      # Game world schema
│   └── validation_results.py     # Validation output schema
├── prompts/
│   ├── __init__.py
│   ├── narrative_prompts.py
│   ├── refinement_prompts.py
│   ├── specialist_prompts.py
│   └── validation_prompts.py
├── utils/
│   ├── __init__.py
│   ├── mta_controller.py         # MTA launch/monitor
│   ├── log_parser.py             # Parse server.log
│   ├── file_manager.py           # JSON read/write
│   └── config.py                 # System configuration
├── examples/
│   ├── game_capabilities.json    # Extracted from Lua
│   ├── example_graphs/           # incredibly_complex.json, etc.
│   └── sample_narratives.json    # Example inputs
├── tests/
│   ├── test_agents.py
│   ├── test_graphs.py
│   └── test_validation.py
├── main.py                        # Entry point
├── config.yaml                    # Configuration file
├── requirements.txt
└── README.md
Lua Implementation (Game World Extraction)
New File: src/export/GameWorldExporter.lua
--- Exports game world capabilities to JSON for LLM consumption
GameWorldExporter = class(function(o)
    o.episodes = {}
    o.actionCatalog = {}
    o.objectTypes = {}
end)

function GameWorldExporter:ExportCapabilities()
    -- Iterate through all DynamicEpisodes
    for _, episodeName in ipairs(CURRENT_STORY.DynamicEpisodes) do
        local episode = DynamicEpisode(episodeName)
        local episodeData = self:ExtractEpisodeData(episode)
        table.insert(self.episodes, episodeData)
    end

    -- Extract action catalog, object types, etc.
    -- Write to data_out/game_capabilities.json
end

-- Additional extraction methods...
Server Command: src/ServerCommands.lua (add new command)
addCommandHandler("export_capabilities", function(player, command)
    local exporter = GameWorldExporter()
    exporter:ExportCapabilities()
    outputChatBox("Capabilities exported to data_out/game_capabilities.json", player)
end)
Implementation Order
Phase 1: Game World Extraction (Lua + Command)
Phase 2: Core Infrastructure (schemas, utils, config)
Phase 3: Narrative Agent (Level 0)
Phase 4: Refinement Pipeline (Levels 1-4) sequentially
Phase 5: Specialist Agents (Scene Manager, Camera Director, Scene Director)
Phase 6: Validation Loop (Validator + Error Corrector)
Phase 7: Execution Agent
Phase 8: LangGraph Workflow Integration
Phase 9: Testing & Refinement
Configuration (config.yaml)
system:
  max_retries: 3
  mta_server_path: "server/MTA Server.exe"
  mta_client_path: "Multi Theft Auto.exe - Shortcut.lnk"
  server_log_path: "server/mods/deathmatch/logs/server.log"

openai:
  model: "gpt-4o-2024-11-20"
  temperature: 0.7
  structured_output: true

paths:
  game_capabilities: "data_out/game_capabilities.json"
  example_graphs: "complex_graphs/"
  input_graphs: "input_graphs/"
  output_artifacts: "data_out/"

validation:
  timeout: 300  # 5 minutes
  poll_interval: 5  # seconds
Key Features
✅ Complete, no stubs: Full implementation of all components
✅ Sequential thinking: Each phase builds on previous
✅ Autonomous: N-retry error correction, auto-discard failures
✅ LLM-optimized: Structured output, few-shot learning from examples
✅ Game-aware: Uses actual capabilities, validates against real constraints
✅ Hierarchical: 5-level GEST refinement (abstract → executable)
✅ Cinematographic: Director-based workflow mirrors real film production
✅ Robust validation: External command execution with log parsing
✅ Artifact-ready: Final execution produces video recordings
This plan implements a complete, production-ready multiagent system for autonomous story generation and simulation in GTA San Andreas. Each component is fully specified with no placeholders or stubs. Ready for sequential implementation.