"""
Textual Description Generator

Generates textual descriptions from proto-graph using VideoDescriptionGEST.
Outputs prompt.txt and optionally description.txt to textual_description/ folder.

Usage:
    from utils.textual_description_generator import generate_textual_description

    generate_textual_description(
        sim_dir=Path("simulations/take1_sim1"),
        proto_graph_path=Path("detailed_graph/take1/proto-graph.json"),
        location="kitchen",
        mode="prompt"  # or "full"
    )
"""

import sys
import json
import shutil
from pathlib import Path
from typing import Optional, Literal
import structlog

logger = structlog.get_logger(__name__)

# VideoDescriptionGEST path
VIDEO_DESC_GEST_PATH = Path(__file__).parent.parent.parent / "VideoDescriptionGEST"


def _import_video_desc_gest():
    """
    Import VideoDescriptionGEST functions with proper path handling.

    Must be called as a function to avoid import conflicts with main.py.
    Pre-loads VideoDescriptionGEST's main module into sys.modules to prevent
    describe_graph.py from importing our main.py instead.
    """
    import importlib.util

    # Save original state
    original_path = sys.path.copy()
    original_main = sys.modules.get('main')

    try:
        # Step 1: Prepend VideoDescriptionGEST paths
        sys.path.insert(0, str(VIDEO_DESC_GEST_PATH / "GEST"))
        sys.path.insert(0, str(VIDEO_DESC_GEST_PATH))

        # Step 2: Pre-load VideoDescriptionGEST's main module into sys.modules
        # This prevents describe_graph.py from finding our main.py
        vdg_main_path = VIDEO_DESC_GEST_PATH / "GEST" / "main.py"
        spec = importlib.util.spec_from_file_location("main", vdg_main_path)
        vdg_main = importlib.util.module_from_spec(spec)
        sys.modules['main'] = vdg_main
        spec.loader.exec_module(vdg_main)

        # Step 3: Now import describe_graph (it will use our pre-loaded main)
        from describe_graph import describe_graph_for_chatgpt
        import describe_events_from_engine

        # Step 4: Inject missing OpenAI import into describe_events_from_engine module
        # (VideoDescriptionGEST bug: describe_events_from_engine.py uses OpenAI but doesn't import it)
        from openai import OpenAI
        describe_events_from_engine.OpenAI = OpenAI

        return (
            describe_graph_for_chatgpt,
            describe_events_from_engine.generate_gpt_description,
            describe_events_from_engine.run_query_withgpt,
            describe_events_from_engine.GPT_MODELS
        )
    finally:
        # Restore original sys.path
        sys.path = original_path
        # Restore original main module (or remove if it wasn't there)
        if original_main is not None:
            sys.modules['main'] = original_main
        elif 'main' in sys.modules:
            del sys.modules['main']


def generate_textual_description(
    sim_dir: Path,
    proto_graph_path: Path,
    location: Optional[str] = None,
    mode: Literal["prompt", "full"] = "prompt"
) -> bool:
    """
    Generate textual description artifacts for a simulation.

    Args:
        sim_dir: Path to simulation directory (e.g., simulations/take1_sim1/)
        proto_graph_path: Path to proto-graph.json
        location: Episode location (e.g., "kitchen", "garden")
        mode: 'prompt' for GPT prompt only, 'full' for prompt + GPT description

    Returns:
        True if successful, False otherwise
    """
    try:
        # Import VideoDescriptionGEST functions (deferred to avoid import conflicts)
        describe_graph_for_chatgpt, generate_gpt_description, run_query_withgpt, GPT_MODELS = _import_video_desc_gest()

        # Create textual_description folder
        text_dir = sim_dir / "textual_description"
        text_dir.mkdir(parents=True, exist_ok=True)

        # Move/rename labels.txt -> engine_generated.txt (from camera1/ folder)
        labels_src = sim_dir / "camera1" / "labels.txt"
        if labels_src.exists():
            shutil.move(str(labels_src), str(text_dir / "engine_generated.txt"))
            logger.info(
                "labels_moved",
                src=str(labels_src),
                dst=str(text_dir / "engine_generated.txt")
            )

        # Load proto-graph
        with open(proto_graph_path, 'r', encoding='utf-8') as f:
            graph = json.load(f)

        # Generate story description from graph using VideoDescriptionGEST
        story = describe_graph_for_chatgpt(graph)
        logger.debug("story_generated", story_length=len(story))

        # Generate GPT prompt using VideoDescriptionGEST
        prompt = generate_gpt_description(story, "withGEST", GPT_MODELS, location=location)

        # Save prompt
        prompt_path = text_dir / "prompt.txt"
        prompt_path.write_text(prompt, encoding='utf-8')
        logger.info("prompt_saved", path=str(prompt_path))

        # If full mode, call GPT API
        if mode == "full":
            description = run_query_withgpt(GPT_MODELS[0], prompt, temperature=0.5)
            description_path = text_dir / "description.txt"
            description_path.write_text(description, encoding='utf-8')
            logger.info("description_saved", path=str(description_path))

        logger.info(
            "textual_description_generated",
            sim_dir=str(sim_dir),
            mode=mode,
            location=location
        )
        return True

    except Exception as e:
        logger.error(
            "textual_description_failed",
            error=str(e),
            sim_dir=str(sim_dir),
            exc_info=True
        )
        return False


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Generate textual descriptions from proto-graph")
    parser.add_argument("--proto-graph", required=True, help="Path to proto-graph.json")
    parser.add_argument("--sim-dir", required=True, help="Path to simulation directory (output location)")
    parser.add_argument("--mode", choices=["prompt", "full"], default="prompt",
                        help="prompt=GPT prompt only, full=prompt+GPT description")

    args = parser.parse_args()

    success = generate_textual_description(
        sim_dir=Path(args.sim_dir),
        proto_graph_path=Path(args.proto_graph),
        mode=args.mode
    )

    sys.exit(0 if success else 1)
