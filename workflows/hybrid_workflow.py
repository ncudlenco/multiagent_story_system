"""
Hybrid GEST Generation Workflow

Deep Agent architecture with scene-building subagents:
  Main Agent: explores world, plans story, picks skins, delegates scene building
  Scene Builder Subagent: builds one scene of GEST events with isolated context

Uses deepagents (langchain-ai/deepagents) for planning, filesystem context,
subagent delegation, and automatic summarization.
"""

import json
from typing import Dict, List, Any, Optional, TypedDict
from pathlib import Path

from langgraph.graph import StateGraph, END, START
from deepagents import create_deep_agent
from deepagents.middleware.subagents import SubAgent
from deepagents.backends.filesystem import FilesystemBackend

import structlog

from core.config import Config
from core.llm_factory import create_chat_model
from schemas.hybrid_planning import GenerationConfig
from simple_gest_random_generator import SimpleGESTRandomGenerator

from tools.exploration_tools import EXPLORATION_TOOLS
from tools.building_tools import create_building_tools
from tools.state_tools import create_state_tools

logger = structlog.get_logger(__name__)


# =============================================================================
# WORKFLOW STATE
# =============================================================================

class HybridState(TypedDict):
    """State passed between workflow stages."""
    seed_text: Optional[str]
    generation_config: Dict[str, Any]
    gest: Optional[Dict[str, Any]]
    metadata: Optional[Dict[str, Any]]


# =============================================================================
# SYSTEM PROMPTS
# =============================================================================

MAIN_AGENT_PROMPT = """You are a story director for a GTA San Andreas simulation environment.

Your job:
1. Explore available episodes, regions, POIs, and actions using tools
2. Pick character skins using get_skins (browse by gender, paginated)
3. Call create_story(title, narrative) to initialize the story
4. Call create_actor for ALL protagonists upfront (and extras if needed) -- create every actor BEFORE delegating any scenes
5. Write the initial plot to "plot.txt" (use write_file): the seed idea, what you discovered, and your planned story
6. For each scene:
   a. Call start_scene(scene_id, action_name, narrative, episode, region, actor_ids) yourself
   b. Delegate to scene_builder subagent (use task tool) to build the rounds and chains
   c. After scene_builder returns: if end_scene returned REQUIRED_NEXT, call relations tasks in parallel
   d. Call move_actors to transition actors to the next region (if needed)
7. After all scenes, write "narrative.txt" (use write_file): final structured summary
8. Call finalize_gest to complete the story -- if it returns REQUIRED_NEXT, call cross-scene relations

CRITICAL -- NO HALLUCINATIONS:
- ONLY reference actions and objects you confirmed exist by calling get_pois and get_poi_first_actions
- If you didn't verify a POI has a specific action (e.g. laptop, food, drinks), do NOT include it in the plan
- Each region has specific POIs with specific action chains -- check before planning
- The scene_builder will explore POIs independently, but your plan must be grounded in what actually exists

ONE REGION PER SCENE:
- Each scene takes place in exactly ONE region. All actors in a scene must be in that region.
- Before choosing a region for a scene, call get_pois to verify it has enough POIs/actions for your planned activities
- Create ALL actors in the FIRST scene's region. Use move_actors between scenes to relocate them.
- Do NOT create actors in a region different from the current scene's region
- FYI: moving between regions within the same episode or linked episode shows actors moving. Unlinked regions teleport them.

VARIETY:
- Don't always default to the same locations -- explore what's available and vary your choices
- Stories can span multiple episodes (e.g. a few scenes in one place, then a few in another)
- Be creative with the setting -- the world has many different environments to discover
- When multiple regions fit the narrative equally, prefer common ones that appear across many episodes

RULES (call get_simulation_rules for full list):
- Interactions only while both actors standing
- Hug/Kiss only between opposite genders
- Actors can carry held objects across scenes. start_round shows what each actor holds.
- Objects can only be put down in their original region.
- Give requires receiver_id parameter in continue_chain.

SEQUENTIAL SCENES:
- Use write_todos to plan your scenes before delegating
- Delegate ONE scene at a time, wait for completion, then delegate the next
- Actors persist across scenes -- tell scene_builder which actor_ids already exist (e.g. "a0 is James, a1 is Sarah")
- Tell scene_builder what new actors to create (name, gender, skin_id, is_extra)
- Call move_actors between scenes to transition actors to the next region
- After each scene completes: if end_scene returns REQUIRED_NEXT, call those tasks in parallel (relations agents)
- After finalize_gest: if it returns REQUIRED_NEXT, call those tasks in parallel (cross-scene relations)

{constraints}"""

SCENE_BUILDER_PROMPT = """You build GEST events for EXACTLY ONE scene using a round-based structure.

CRITICAL:
- You build ONE scene only. After end_scene(), you are DONE. Return your summary immediately.
- Do NOT start another scene. Do NOT test or experiment. Every action is FINAL and cannot be undone.
- Do NOT create extra scenes, retry scenes, or test scenes. Build it right the first time.

You receive a scene description with scene_id, episode, region, characters, and what should happen.
The director will also tell you the max chains per actor for this scene -- respect this limit.

The scene has already been started by the director. You build the content.

MANDATORY FLOW:
1. For each round (as many as the narrative needs):
   a. Call start_round() (or start_round(setup=True) for off-camera preparation)
   b. For each actor: build ONE chain (can interleave across actors):
      - start_chain → continue_chain (step by step) → end_chain
      - OR do_interaction for synchronized actions
      - OR start_chain (without POI) for spawnable/held object actions → continue_chain → end_chain
      Multiple actors CAN have active chains at the same time.
   c. After ALL chains are committed (end_chain), set camera on committed events
   d. Optionally: add_temporal_dependency to order cross-actor events,
      or add_starts_with to synchronize events (e.g. two actors sit simultaneously)
   e. Call end_round()
2. Call end_scene()
3. Return a summary of what was built. DO NOT call any more tools after end_scene.

RULES:
- ONE REGION: all actions in a scene happen in the scene's region. Do NOT use POIs from other regions.
- Only use POIs and actions that exist in THIS region -- call get_pois to verify before building chains.
- Each actor can do at most N chains per scene (told by director). The tool will reject if exceeded.
- Only 1 interaction per round per region (limited by interaction POI capacity)
- Every actor must complete at least one chain action before any interaction in a scene
- All chains in a round must be committed (end_chain) before end_round
- Camera (start_recording/stop_recording) only on committed events (after end_chain)
- No consecutive interactions (must have a chain between them)
- No duplicate actions in a row (except Move)
- Actors must be standing to start chains or interactions
- Give requires receiver_id parameter in continue_chain
- Do NOT call finalize_gest or move_actors -- the main agent handles those
- Do NOT move actors to other regions unless told to"""


# =============================================================================
# WORKFLOW NODE
# =============================================================================

_debug_output_dir: Optional[str] = None


class LiveFileLogger:
    """Writes every LLM call and tool execution to a JSONL file in real-time."""

    def __init__(self, output_dir: str, stage_name: str):
        self.path = Path(output_dir) / f"live_{stage_name}.jsonl"
        Path(output_dir).mkdir(parents=True, exist_ok=True)
        self.path.write_text("", encoding="utf-8")
        self._turn = 0

    def _append(self, entry: Dict[str, Any]) -> None:
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")

    def log_llm_response(self, content: str, tool_calls: List = None) -> None:
        self._turn += 1
        entry = {"turn": self._turn, "type": "llm_response", "content": content[:2000] if content else ""}
        if tool_calls:
            entry["tool_calls"] = [{"name": tc.get("name", ""), "args": tc.get("args", {})} for tc in tool_calls]
        self._append(entry)

    def log_tool_result(self, tool_name: str, result: str) -> None:
        self._append({"turn": self._turn, "type": "tool_result", "tool": tool_name, "result": result[:1000] if result else ""})


def _make_callbacks(stage_name: str) -> Optional[List]:
    """Create LangChain callbacks for live logging."""
    if not _debug_output_dir:
        return None

    from langchain_core.callbacks import BaseCallbackHandler
    file_logger = LiveFileLogger(_debug_output_dir, stage_name)

    class _LiveCB(BaseCallbackHandler):
        def on_llm_end(self, response, **kwargs):
            gen = response.generations[0][0] if response.generations else None
            if gen:
                msg = gen.message if hasattr(gen, "message") else None
                content = getattr(msg, "content", gen.text if hasattr(gen, "text") else "")
                tool_calls = getattr(msg, "tool_calls", []) if msg else []
                file_logger.log_llm_response(content, tool_calls)

        def on_tool_end(self, output, name=None, **kwargs):
            file_logger.log_tool_result(name or "unknown", str(output))

    return [_LiveCB()]


def _build_constraints_text(gen_config: Dict[str, Any]) -> str:
    """Build constraints section for system prompt."""
    parts = []
    config = GenerationConfig(**gen_config) if gen_config else GenerationConfig()

    parts.append(f"Target scenes: {config.num_scenes}")
    parts.append(f"Number of protagonists: {config.num_protagonists}")

    if config.include_extras:
        parts.append("Include background extras doing routines")
    else:
        parts.append("No background extras")

    if config.seed_episodes:
        parts.append(f"MUST use these episodes: {config.seed_episodes}")
    if config.seed_regions:
        parts.append(f"MUST use these regions: {config.seed_regions}")

    parts.append(f"Max events per scene: {config.max_events_per_scene}")
    parts.append(f"Max action chains (POI visits) per actor per scene: {config.max_chains_per_actor}")

    if config.seed_text:
        parts.append(f"\nStory seed (adapt to what's simulatable): {config.seed_text}")

    return "CONSTRAINTS:\n" + "\n".join(f"- {p}" for p in parts)


def deep_agent_node(state: HybridState) -> Dict[str, Any]:
    """Run the deep agent: concept + casting + scene delegation + finalize."""
    logger.info("hybrid_workflow.start")

    config = Config.load()
    model = create_chat_model(config.llm)

    # Ensure ANTHROPIC_API_KEY is set for subagent model initialization
    import os
    from dotenv import load_dotenv
    load_dotenv()
    api_key = os.getenv(config.llm.api_key_env)
    if api_key and config.llm.provider == "anthropic" and not os.getenv("ANTHROPIC_API_KEY"):
        os.environ["ANTHROPIC_API_KEY"] = api_key
    model_name = f"{config.llm.provider}:{config.llm.model}"

    gen_config = state.get('generation_config', {})
    seed = state.get('seed_text', '')
    constraints = _build_constraints_text(gen_config)

    # Create generator instance (holds GEST state)
    capabilities_path = config.paths.simulation_environment_capabilities
    gen = SimpleGESTRandomGenerator(capabilities_path)

    # Build config for tools
    gen_cfg = GenerationConfig(**gen_config) if gen_config else GenerationConfig()
    tool_config = {
        'enable_concept_events': gen_cfg.enable_concept_events,
        'enable_logical_relations': gen_cfg.enable_logical_relations,
        'enable_semantic_relations': gen_cfg.enable_semantic_relations,
        'max_chains_per_actor_per_scene': gen_cfg.max_chains_per_actor,
    }

    # Create tools bound to this generator with config
    building_tools = create_building_tools(gen, config=tool_config)
    state_tools = create_state_tools(gen, config=tool_config)
    finalize_tool = next(t for t in state_tools if t.name == 'finalize_gest')

    # Main agent tools: exploration + director-only building tools + finalize
    director_tools = [t for t in building_tools if t.name in
                      {'create_story', 'create_actor', 'start_scene', 'move_actors'}]
    main_tools = EXPLORATION_TOOLS + director_tools + [finalize_tool]

    # Scene builder tools: building + POI exploration
    from tools.exploration_tools import (
        get_episodes, get_regions, get_pois, get_poi_first_actions,
        get_next_actions, get_region_capacity, get_skins,
        get_spawnable_types, get_interaction_types,
    )
    # Scene builder gets chain/round tools only -- NOT scene/story/actor/move/finalize
    director_only_tools = {'create_story', 'create_actor', 'start_scene', 'move_actors'}
    scene_building_tools = [t for t in building_tools if t.name not in director_only_tools]
    scene_tools = scene_building_tools + [
        get_pois, get_poi_first_actions, get_next_actions, get_region_capacity,
        get_episodes, get_regions, get_skins,
        get_spawnable_types, get_interaction_types,
    ]

    # Define scene builder subagent
    scene_builder = SubAgent(
        name="scene_builder",
        description="Builds one scene of the story as GEST events. Give it: scene_id, action_name, narrative, episode, region, actor_ids, and what should happen.",
        system_prompt=SCENE_BUILDER_PROMPT,
        tools=scene_tools,
        model=model_name,
    )

    # Define relations subagents (sibling to scene_builder, called by main agent)
    all_subagents = [scene_builder]

    if gen_cfg.enable_logical_relations:
        logical_relations_agent = SubAgent(
            name="logical_relations_agent",
            description="Adds logical relations (causes, enables, prevents, etc.) between GEST events. Give it a list of event IDs to analyze.",
            system_prompt=(
                "You add logical relations between events in a simulation story graph. "
                "Use add_logical_relation(source_event, target_event, relation_type) for each relation. "
                "Types: causes, caused_by, enables, prevents, blocks, implies, requires, depends_on. "
                "Analyze the events provided and add meaningful causal/dependency relations."
            ),
            tools=[t for t in building_tools if t.name == 'add_logical_relation'],
            model=model_name,
        )
        all_subagents.append(logical_relations_agent)

    if gen_cfg.enable_semantic_relations:
        semantic_relations_agent = SubAgent(
            name="semantic_relations_agent",
            description="Adds semantic relations (narrative coherence) between GEST events. Give it a list of event IDs to analyze.",
            system_prompt=(
                "You add semantic relations for narrative coherence in a simulation story graph. "
                "Use add_semantic_relation(event_id, relation_type, target_events) for each relation. "
                "Types are free-text: observes, interrupts, reflects_on, contrasts_with, motivates, sets_context_for, etc. "
                "Analyze the events provided and add meaningful narrative coherence relations."
            ),
            tools=[t for t in building_tools if t.name == 'add_semantic_relation'],
            model=model_name,
        )
        all_subagents.append(semantic_relations_agent)

    # System prompt
    system_prompt = MAIN_AGENT_PROMPT.format(constraints=constraints)
    if seed:
        system_prompt += f"\n\nStory seed: {seed}"

    # Create deep agent with filesystem backend for write_file persistence
    backend = None
    if _debug_output_dir:
        Path(_debug_output_dir).mkdir(parents=True, exist_ok=True)
        backend = FilesystemBackend(root_dir=_debug_output_dir, virtual_mode=True)

    agent = create_deep_agent(
        model=model,
        tools=main_tools,
        system_prompt=system_prompt,
        subagents=all_subagents,
        backend=backend,
    )

    # Run
    invoke_config = {"recursion_limit": 500}
    cbs = _make_callbacks("main")
    if cbs:
        invoke_config["callbacks"] = cbs

    user_message = "Create and build a complete story for the simulation."

    result = agent.invoke(
        {"messages": [("user", user_message)]},
        config=invoke_config
    )

    # Build final GEST
    gest = gen._build_gest()

    # Programmatic validation
    from utils.validation_tools import validate_temporal_structure
    meta_keys = {'temporal', 'spatial', 'semantic', 'logical', 'camera'}
    events = {k: v for k, v in gest.items() if k not in meta_keys and isinstance(v, dict)}
    validation = validate_temporal_structure(events, gest.get('temporal', {}))
    if not validation.get('valid'):
        logger.warning("hybrid_workflow.validation_issues", errors=validation.get('errors', [])[:5])

    metadata = {
        'num_actors': len(gen.actors),
        'num_events': len(gen.events),
        'valid': validation.get('valid', False),
    }

    logger.info("hybrid_workflow.done",
                actors=len(gen.actors), events=len(gen.events),
                valid=validation.get('valid', False))

    return {'gest': gest, 'metadata': metadata}


# =============================================================================
# HELPERS
# =============================================================================

def _extract_json(text: str) -> Dict[str, Any]:
    """Extract JSON from LLM response text."""
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    if '```json' in text:
        start = text.index('```json') + 7
        end = text.index('```', start)
        return json.loads(text[start:end].strip())

    if '```' in text:
        start = text.index('```') + 3
        end = text.index('```', start)
        candidate = text[start:end].strip()
        if candidate.startswith('{'):
            return json.loads(candidate)

    for i, char in enumerate(text):
        if char == '{':
            brace_count = 0
            for j in range(i, len(text)):
                if text[j] == '{':
                    brace_count += 1
                elif text[j] == '}':
                    brace_count -= 1
                    if brace_count == 0:
                        try:
                            return json.loads(text[i:j + 1])
                        except json.JSONDecodeError:
                            break

    logger.warning("json_extraction_failed", text_preview=text[:200])
    return {}


# =============================================================================
# WORKFLOW GRAPH
# =============================================================================

def create_hybrid_workflow() -> StateGraph:
    """Create the hybrid generation workflow."""
    workflow = StateGraph(HybridState)

    workflow.add_node("generate", deep_agent_node)

    workflow.add_edge(START, "generate")
    workflow.add_edge("generate", END)

    return workflow


def run_hybrid_generation(
    seed_text: Optional[str] = None,
    generation_config: Optional[Dict[str, Any]] = None,
    output_dir: Optional[str] = None
) -> tuple:
    """
    Run the complete hybrid GEST generation pipeline.

    Args:
        seed_text: Optional story seed for prompt alignment
        generation_config: Optional GenerationConfig dict
        output_dir: Optional directory to save all intermediary results

    Returns:
        Tuple of (gest_dict, metadata_dict)
    """
    global _debug_output_dir
    _debug_output_dir = output_dir

    workflow = create_hybrid_workflow()
    app = workflow.compile()

    initial_state: HybridState = {
        'seed_text': seed_text,
        'generation_config': generation_config or {},
        'gest': None,
        'metadata': None,
    }

    result = app.invoke(initial_state, config={"recursion_limit": 500})

    gest = result.get('gest', {})
    metadata = result.get('metadata', {})

    # Save results if output_dir provided
    if output_dir:
        out = Path(output_dir)
        out.mkdir(parents=True, exist_ok=True)

        with open(out / 'detail_gest.json', 'w', encoding='utf-8') as f:
            json.dump(gest, f, indent=2, ensure_ascii=False)

        with open(out / 'metadata.json', 'w', encoding='utf-8') as f:
            json.dump(metadata, f, indent=2, ensure_ascii=False, default=str)

        logger.info("hybrid_workflow.results_saved", output_dir=str(out))

    return gest, metadata
