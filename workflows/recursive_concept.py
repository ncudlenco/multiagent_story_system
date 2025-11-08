"""
Recursive Concept Workflow

LangGraph workflow orchestrating recursive scene expansion.
Expands abstract scenes into sub-scenes until target scene count is reached.
"""

from langgraph.graph import StateGraph, END
from typing import TypedDict, List, Dict, Any
from pathlib import Path
import json
from datetime import datetime
import structlog

from schemas.gest import GEST, DualOutput

logger = structlog.get_logger()


class RecursiveConceptState(TypedDict):
    """State for recursive scene expansion"""
    story_id: str
    narrative_seeds: List[str]
    current_gest: GEST
    target_scene_count: int
    current_scene_count: int
    iteration: int
    expandable_scenes: List[str]
    leaf_scenes: List[str]
    parent_scenes: List[str]
    narrative: str
    title: str
    max_num_protagonists: int
    max_num_extras: int
    num_distinct_actions: int
    concept_capabilities: Dict[str, Any]
    config: Dict[str, Any]
    prompt_logger: Any  # Optional PromptLogger instance
    output_dir_override: Path  # Optional override for batch processing


def count_leaf_scenes(gest: GEST) -> int:
    """Count scenes marked as leaves"""
    return sum(
        1 for event in gest.events.values()
        if event.Properties.get('scene_type') == 'leaf'
    )


def get_leaf_scenes(gest: GEST) -> List[str]:
    """Get IDs of all leaf scenes"""
    return [
        event_id for event_id, event in gest.events.items()
        if event.Properties.get('scene_type') == 'leaf'
    ]


def get_parent_scenes(gest: GEST) -> List[str]:
    """Get IDs of all parent scenes"""
    return [
        event_id for event_id, event in gest.events.items()
        if event.Properties.get('scene_type') == 'parent'
    ]


def get_expandable_scenes(gest: GEST) -> List[str]:
    """Get scenes that can be expanded (parents or abstract leaves)"""
    expandable = []
    for event_id, event in gest.events.items():
        scene_type = event.Properties.get('scene_type')
        child_scenes = event.Properties.get('child_scenes', []) or []

        # Expandable if parent or leaf that has not been expanded yet
        if (scene_type == 'parent' or scene_type == 'leaf') and len(child_scenes) == 0:
            expandable.append(event_id)
    return expandable


def merge_expansion(current_gest: GEST, expansion_gest: GEST) -> GEST:
    """Merge expansion result into current GEST"""
    # Update events - expansion takes precedence
    merged_events = {**current_gest.events, **expansion_gest.events}

    # Merge relations - expansion adds/updates
    merged_temporal = {**current_gest.temporal, **expansion_gest.temporal}
    merged_semantic = {**current_gest.semantic, **expansion_gest.semantic}
    merged_logical = {**current_gest.logical, **expansion_gest.logical}
    merged_spatial = {**current_gest.spatial, **expansion_gest.spatial}
    merged_camera = {**current_gest.camera, **expansion_gest.camera}

    # Create GEST with root-level events (unpack merged_events as kwargs)
    return GEST(
        temporal=merged_temporal,
        spatial=merged_spatial,
        semantic=merged_semantic,
        logical=merged_logical,
        camera=merged_camera,
        **merged_events  # Unpack events at root level
    )


def save_concept_level_artifacts(
    state: RecursiveConceptState,
    output_dir: Path
) -> None:
    """Save GEST and narrative for current recursion level"""
    level = state['iteration']
    level_dir = output_dir / f"concept_{level}"
    level_dir.mkdir(parents=True, exist_ok=True)

    # Save GEST
    gest_path = level_dir / "gest.json"
    with open(gest_path, 'w') as f:
        json.dump(state['current_gest'].model_dump(), f, indent=2)

    # Save narrative
    narrative_path = level_dir / "narrative.txt"
    with open(narrative_path, 'w') as f:
        f.write(f"{state['title']}\n{state['narrative']}")

    # Save metadata
    metadata = {
        "iteration": state['iteration'],
        "current_scene_count": state['current_scene_count'],
        "target_scene_count": state['target_scene_count'],
        "leaf_scenes": state['leaf_scenes'],
        "parent_scenes": state['parent_scenes'],
        "timestamp": datetime.now().isoformat()
    }
    metadata_path = level_dir / "metadata.json"
    with open(metadata_path, 'w') as f:
        json.dump(metadata, f, indent=2)

    logger.info(
        "saved_concept_level_artifacts",
        level=level,
        directory=str(level_dir),
        scene_count=state['current_scene_count']
    )


def should_continue(state: RecursiveConceptState) -> str:
    """Decide whether to continue expansion"""
    # Safety: max 20 iterations to prevent infinite loops
    if state['iteration'] >= state['target_scene_count'] + 2:
        logger.error(
            "max_iterations_reached",
            iteration=state['iteration'],
            current_count=state['current_scene_count'],
            target=state['target_scene_count']
        )
        return "done"

    if state['current_scene_count'] >= state['target_scene_count']:
        logger.info("target_reached", count=state['current_scene_count'])
        return "done"

    if not state['expandable_scenes']:
        logger.warning("no_expandable_scenes", iteration=state['iteration'])
        return "done"

    return "expand"


def expand_scene_node(state: RecursiveConceptState) -> RecursiveConceptState:
    """Expand one scene into sub-scenes"""
    from agents.concept_agent import ConceptAgent

    logger.info(
        "expanding_scene_node",
        iteration=state['iteration'],
        current_count=state['current_scene_count'],
        target=state['target_scene_count']
    )

    # Calculate actor budgets
    max_protagonists = state['max_num_protagonists']
    max_extras = state['max_num_extras']

    # Count currently created actors in the GEST
    protagonists_created = sum(
        1 for e in state['current_gest'].events.values()
        if e.Action == "Exists" and e.Properties.get('IsBackgroundActor') == False
    )
    extras_created = sum(
        1 for e in state['current_gest'].events.values()
        if e.Action == "Exists" and e.Properties.get('IsBackgroundActor') == True
    )

    # Calculate remaining budgets (-1 means unlimited)
    if max_protagonists == -1:
        protagonist_budget = -1
    else:
        protagonist_budget = max(0, max_protagonists - protagonists_created)

    if max_extras == -1:
        extras_budget = -1
    else:
        extras_budget = max(0, max_extras - extras_created)

    logger.info(
        "actor_budget_for_expansion",
        max_protagonists=max_protagonists,
        max_extras=max_extras,
        protagonists_created=protagonists_created,
        extras_created=extras_created,
        protagonist_budget=protagonist_budget,
        extras_budget=extras_budget
    )

    # Initialize agent with optional prompt_logger
    agent = ConceptAgent(state['config'], prompt_logger=state.get('prompt_logger'))

    # Choose scene to expand (first expandable for now)
    scenes_to_expand = state['expandable_scenes'] if state['expandable_scenes'] else []

    if not scenes_to_expand:
        logger.warning("no_scene_to_expand", iteration=state['iteration'])
        return state

    # Expand scene with budgets
    expansion_result = agent.expand_scene(
        current_gest=state['current_gest'],
        scenes_to_expand=scenes_to_expand,
        remaining_budget=state['target_scene_count'] - state['current_scene_count'],
        concept_capabilities=state['concept_capabilities'],
        protagonist_budget=protagonist_budget,
        extras_budget=extras_budget,
        seed_sentences=state['narrative_seeds'],
        iteration=state['iteration']  # Pass iteration for prompt logging
    )

    # Merge expansion
    new_gest = merge_expansion(state['current_gest'], expansion_result.gest)
    new_scene_count = count_leaf_scenes(new_gest)

    new_state = {
        **state,
        'current_gest': new_gest,
        'current_scene_count': new_scene_count,
        'iteration': state['iteration'] + 1,
        'expandable_scenes': get_expandable_scenes(new_gest),
        'leaf_scenes': get_leaf_scenes(new_gest),
        'parent_scenes': get_parent_scenes(new_gest),
        'title': state['title'],  # Preserve old title
        'narrative': expansion_result.narrative  # REPLACES old narrative
    }

    if state['title'] == "" and expansion_result.title:
        new_state['title'] = expansion_result.title

    # Save artifacts for this level
    # Determine output directory
    if state.get('output_dir_override'):
        output_dir = state['output_dir_override']
    else:
        output_dir = Path(state['config']['paths']['output_dir']) / f"story_{state['story_id']}"
    save_concept_level_artifacts(new_state, output_dir)

    logger.info(
        "scene_expanded",
        new_scene_count=new_scene_count,
        scenes_added=new_scene_count - state['current_scene_count']
    )

    return new_state


# Build workflow
workflow = StateGraph(RecursiveConceptState)
workflow.add_node("expand", expand_scene_node)
workflow.set_entry_point("expand")
workflow.add_conditional_edges(
    "expand",
    should_continue,
    {
        "expand": "expand",  # Recursive loop
        "done": END
    }
)


def run_recursive_concept(
    config: Dict[str, Any],
    story_id: str,
    target_scene_count: int,
    max_num_protagonists: int,
    max_num_extras: int,
    num_distinct_actions: int,
    narrative_seeds: List[str],
    concept_capabilities: Dict[str, Any],
    prompt_logger=None,
    output_dir_override: Path = None
) -> DualOutput:
    """
    Run recursive scene expansion workflow.

    Args:
        config: Configuration dictionary
        story_id: Story identifier (8-char UUID)
        target_scene_count: Target number of leaf scenes to generate
        max_num_protagonists: Maximum number of protagonist actors in story
        max_num_extras: Maximum number of background actors (extras) in story
        num_distinct_actions: Number of distinct actions to use
        narrative_seeds: Optional seed sentences
        concept_capabilities: Concept cache data
        prompt_logger: Optional PromptLogger instance for logging prompts
        output_dir_override: Override output directory (for batch processing)

    Returns:
        DualOutput with final GEST and narrative
    """

    logger.info(
        "starting_recursive_concept",
        story_id=story_id,
        target_scene_count=target_scene_count,
        max_num_protagonists=max_num_protagonists,
        max_num_extras=max_num_extras,
        num_distinct_actions=num_distinct_actions
    )

    # Initialize state with empty GEST (no events at root level yet)
    initial_state = {
        'story_id': story_id,
        'current_gest': GEST(
            temporal={},
            spatial={},
            semantic={},
            logical={},
            camera={}
        ),
        'target_scene_count': target_scene_count,
        'max_num_protagonists': max_num_protagonists,
        'max_num_extras': max_num_extras,
        'num_distinct_actions': num_distinct_actions,
        'narrative_seeds': narrative_seeds,
        'current_scene_count': 0,
        'iteration': 0,
        'expandable_scenes': ['initial'],  # Trigger first expansion
        'leaf_scenes': [],
        'parent_scenes': [],
        'narrative': "",
        'title': "",
        'concept_capabilities': concept_capabilities,
        'config': config,
        'prompt_logger': prompt_logger,
        'output_dir_override': output_dir_override
    }

    # Run workflow
    app = workflow.compile()
    final_state = app.invoke(initial_state)

    logger.info(
        "recursive_concept_complete",
        story_id=story_id,
        final_scene_count=final_state['current_scene_count'],
        total_iterations=final_state['iteration']
    )

    return DualOutput(
        gest=final_state['current_gest'],
        narrative=final_state['narrative'],
        title=final_state.get('title', '')  # Use title from state if set by agent
    )
