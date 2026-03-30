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
3. Write the initial plot to "plot.txt" (use write_file with path "plot.txt"): the seed idea, what you discovered, and your planned story
4. Create a story plan as TODOs (use write_todos)
5. For each scene, delegate ONE at a time to the scene_builder subagent (use task tool)
6. After all scenes are done, write "narrative.txt" (use write_file with path "narrative.txt"): the final story as a structured summary with title, characters, scene descriptions, and any adaptations you made from the original plot
7. Call finalize_gest to complete the story

CRITICAL -- NO HALLUCINATIONS:
- ONLY reference actions and objects you confirmed exist by calling get_pois and get_poi_first_actions
- If you didn't verify a POI has a specific action (e.g. laptop, food, drinks), do NOT include it in the plan
- Each region has specific POIs with specific action chains -- check before planning
- The scene_builder will explore POIs independently, but your plan must be grounded in what actually exists

RULES (call get_simulation_rules for full list):
- Interactions (Talk, Hug, Kiss) only while both actors standing
- Hug/Kiss only between opposite genders
- Spawnable objects (phone, cigarette) can't be given or put down
- No spawnable actions while sitting

IMPORTANT -- SEQUENTIAL SCENE BUILDING:
- Delegate ONE scene at a time to scene_builder, wait for it to complete, then delegate the next
- Actors created in scene 1 persist -- tell scene 2 which actor_ids already exist (e.g. "a0 is James, a1 is Sarah, already created")
- Do NOT delegate multiple scenes simultaneously

When delegating a scene to scene_builder, describe:
- Episode and region
- Characters: which actor_ids already exist, which need to be created (with name, gender, skin_id)
- What should happen (using action vocabulary you confirmed exists in that region's POIs)
- Whether to record with camera

{constraints}"""

SCENE_BUILDER_PROMPT = """You build GEST events for one scene in a simulation.

You receive a scene description with episode, region, characters, and what should happen.

Steps:
1. If the task says actors already exist (e.g. "a0 is James"), do NOT create them again -- just use their actor_ids directly
2. Only call create_actor for NEW characters not yet created
3. Explore POIs in the region with get_pois and get_poi_first_actions
4. For each character's actions:
   - start_chain at a suitable POI
   - continue_chain step by step (tool tells you valid next actions)
   - end_chain when done
5. Create interactions (do_interaction) between characters if needed
6. Move actors between regions if needed (move_actor)
7. Control camera (start_recording / stop_recording)
8. For cross-actor timing (e.g. "A answers phone, B does something while A waits, A hangs up after B finishes"):
   - Use add_temporal_dependency(before_event, after_event) to enforce ordering between specific events
9. When the scene is complete, call end_scene() to mark the scene boundary

The tools validate everything -- if you get an error, adapt your plan.
Do NOT call finalize_gest -- the main agent does that after all scenes.
Do NOT move actors to other regions unless the scene description explicitly says to. Stay in the assigned region."""


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

    # Create tools bound to this generator
    building_tools = create_building_tools(gen)
    state_tools = create_state_tools(gen)
    finalize_tool = next(t for t in state_tools if t.name == 'finalize_gest')

    # Main agent tools: exploration + skins + finalize
    main_tools = EXPLORATION_TOOLS + [finalize_tool]

    # Scene builder tools: building + POI exploration
    from tools.exploration_tools import (
        get_episodes, get_regions, get_pois, get_poi_first_actions,
        get_next_actions, get_region_capacity, get_skins,
        get_spawnable_types, get_interaction_types,
    )
    scene_tools = building_tools + [
        get_pois, get_poi_first_actions, get_next_actions, get_region_capacity,
        get_episodes, get_regions, get_skins,
        get_spawnable_types, get_interaction_types,
    ]

    # Define scene builder subagent
    scene_builder = SubAgent(
        name="scene_builder",
        description="Builds one scene of the story as GEST events. Give it: episode, region, character names with actor_ids and skin_ids, and what should happen.",
        system_prompt=SCENE_BUILDER_PROMPT,
        tools=scene_tools,
        model=model_name,
    )

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
        subagents=[scene_builder],
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
