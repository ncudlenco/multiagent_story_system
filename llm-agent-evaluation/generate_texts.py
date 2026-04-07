"""
Generate texts.json for agentic stories using VideoDescriptionGEST pipeline.

Produces the same format as procedural stories:
  {
    "query_withGEST": "<prompt>",
    "gpt-4o_withGEST_t-1.0": "<description>"
  }

Usage:
    python generate_texts.py [--model gpt-4o] [--force] [--dry-run]
"""

import json
import argparse
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent))

from catalog import load_agentic_stories, Story
from vdg_bridge import (
    describe_graph_for_chatgpt,
    generate_gpt_description,
)

DEFAULT_MODEL = "gpt-4o"


def _infer_location(proto_graph: dict) -> str | None:
    """Extract the primary location from the proto-graph (most frequent action location)."""
    from collections import Counter
    counts = Counter()
    for v in proto_graph.values():
        if not isinstance(v, dict):
            continue
        locs = v.get("Location", [])
        if isinstance(locs, list):
            for loc in locs:
                if isinstance(loc, str):
                    counts[loc] += 1
    if not counts:
        return None
    return counts.most_common(1)[0][0]


def generate_texts_for_story(
    story: Story,
    model: str = DEFAULT_MODEL,
    force: bool = False,
    dry_run: bool = False,
) -> bool:
    """
    Generate and save texts.json for a single agentic story.

    Returns True on success or skip, False on error.
    """
    out_path = story.texts_json_path

    if out_path.exists() and not force:
        print(f"  [SKIP] {story.worker_id} — texts.json already exists")
        return True

    if not story.proto_graph_path.exists():
        print(f"  [SKIP] {story.worker_id} — proto-graph.json not found")
        return True

    print(f"  [GEN]  worker{story.worker_id} (group {story.gest_group}, "
          f"{story.actor_count} actors, {story.action_count} actions)")

    try:
        proto_graph = json.loads(story.proto_graph_path.read_text(encoding="utf-8"))
        location = _infer_location(proto_graph)

        # Step 1: build structured event description list (location_is_room uses
        # per-action room names from the Location field instead of numeric IDs)
        event_descriptions = describe_graph_for_chatgpt(proto_graph, location_is_room=True)

        if dry_run:
            # Dry-run: pass empty model list so generate_gpt_description builds
            # the prompt but skips API calls, returning just {"query_withGEST": ...}
            result = generate_gpt_description(
                event_descriptions, "withGEST", [], location=location, graph=proto_graph
            )
            prompt = result.get("query_withGEST", "")
            print(f"    [DRY-RUN] prompt length: {len(prompt)} chars")
            return True

        # Step 2: call VDG pipeline with our single model — returns dict with
        # {"query_withGEST": prompt, "<model>_withGEST_t-1.0": description}
        data = generate_gpt_description(
            event_descriptions, "withGEST", [model], location=location, graph=proto_graph
        )

        # Step 3: save in same format as procedural texts.json
        out_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"    saved -> {out_path}")
        return True

    except Exception as e:
        print(f"    [ERROR] worker{story.worker_id}: {e}")
        return False


def generate_all(
    model: str = DEFAULT_MODEL,
    force: bool = False,
    dry_run: bool = False,
    groups: list[int] | None = None,
) -> dict:
    """
    Generate texts.json for all (or selected) agentic stories.

    Returns summary dict with counts.
    """
    stories = load_agentic_stories()

    if groups:
        stories = [s for s in stories if s.gest_group in groups]

    print(f"Processing {len(stories)} agentic stories...")
    ok = err = skip = 0

    for story in stories:
        result = generate_texts_for_story(story, model=model, force=force, dry_run=dry_run)
        if result:
            if story.texts_json_path.exists() or dry_run:
                ok += 1
            else:
                skip += 1
        else:
            err += 1

    summary = {"total": len(stories), "ok": ok, "skipped": skip, "errors": err}
    print(f"\nDone: {ok} generated, {skip} skipped, {err} errors")
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate texts.json for agentic stories")
    parser.add_argument("--model", default=DEFAULT_MODEL, help="OpenAI model")
    parser.add_argument("--force", action="store_true", help="Overwrite existing texts.json")
    parser.add_argument("--dry-run", action="store_true", help="Build prompts only, no API calls")
    parser.add_argument("--groups", type=int, nargs="+", help="Only process these GEST groups (1–5)")
    args = parser.parse_args()

    generate_all(
        model=args.model,
        force=args.force,
        dry_run=args.dry_run,
        groups=args.groups,
    )
