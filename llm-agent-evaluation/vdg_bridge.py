"""
VideoDescriptionGEST bridge — thin import wrapper.

Handles the sys.path manipulation required to import VDG without conflicting
with our own main.py. Exposes describe_graph_for_chatgpt, generate_gpt_description,
run_query_withgpt, and GPT_MODELS directly.

run_query_withgpt will use OPENAI_API_KEY from .env (patched in VDG source).
"""

import importlib.util
import sys
from pathlib import Path
from typing import Any

VDG_ROOT = Path(__file__).parent.parent.parent / "VideoDescriptionGEST"

_cache: dict = {}


def _import_vdg() -> dict:
    if _cache:
        return _cache

    original_path = sys.path.copy()
    original_main = sys.modules.get("main")

    try:
        sys.path.insert(0, str(VDG_ROOT / "GEST"))
        sys.path.insert(0, str(VDG_ROOT))

        # Pre-load VDG's main.py so describe_graph.py finds it, not ours
        spec = importlib.util.spec_from_file_location(
            "main", VDG_ROOT / "GEST" / "main.py"
        )
        vdg_main = importlib.util.module_from_spec(spec)
        sys.modules["main"] = vdg_main
        spec.loader.exec_module(vdg_main)

        from describe_graph import describe_graph_for_chatgpt
        import describe_events_from_engine as _defe

        # VDG references OpenAI at module level in some paths
        from openai import OpenAI
        _defe.OpenAI = OpenAI

        _cache["describe_graph_for_chatgpt"] = describe_graph_for_chatgpt
        _cache["generate_gpt_description"] = _defe.generate_gpt_description
        _cache["run_query_withgpt"] = _defe.run_query_withgpt
        _cache["GPT_MODELS"] = _defe.GPT_MODELS

    finally:
        sys.path = original_path
        if original_main is not None:
            sys.modules["main"] = original_main
        elif "main" in sys.modules:
            del sys.modules["main"]

    return _cache


def describe_graph_for_chatgpt(graph: dict, location_is_room: bool = False) -> Any:
    return _import_vdg()["describe_graph_for_chatgpt"](graph, location_is_room=location_is_room)


def generate_gpt_description(story: Any, query_type: str, gpt_models: list,
                              location: str = None, graph: dict = None) -> str:
    return _import_vdg()["generate_gpt_description"](
        story, query_type, gpt_models, location=location, graph=graph
    )


def run_query_withgpt(gpt_model: str, query: str, temperature: float = None) -> str:
    return _import_vdg()["run_query_withgpt"](gpt_model, query, temperature)


def get_gpt_models() -> list:
    return _import_vdg()["GPT_MODELS"]
