"""
Reconstitute root narratives for procedural stories.

Given a procedural story's VDG text description, asks GPT to infer what the
original story was about — producing a narrative equivalent to the agentic
system's root narrative. Uses a fixed one-shot example drawn from worker1.

Output per story: {story_dir}/reconstituted_narrative.json
  {"narrative": "...", "model": "gpt-4o", "source_description": "..."}

Usage:
    python reconstitute.py [--model gpt-4o] [--force] [--dry-run]
"""

import json
import os
import sys
import time
import random
import argparse
from pathlib import Path

# Load .env
_env = Path(__file__).parent.parent / ".env"
if _env.exists():
    with open(_env) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent))

from catalog import load_agentic_stories, load_procedural_stories, select_procedural_matches

DEFAULT_MODEL = "gpt-4o"

# ---------------------------------------------------------------------------
# Fixed one-shot example — always worker1 (group 1)
# We use worker1's VDG description → root narrative as the demonstration.
# ---------------------------------------------------------------------------
def _load_oneshot_example() -> tuple[str, str]:
    """Return (vdg_description, root_narrative) for the fixed one-shot example."""
    from catalog import AGENTIC_BASE
    base = AGENTIC_BASE / "worker1"
    detail = list(base.glob("batch_*/story_detail_gest/detailed_graph/take1/detail_gest.json"))[0]
    texts = list(base.glob("batch_*/story_detail_gest/texts.json"))[0]

    g = json.loads(detail.read_text(encoding="utf-8"))
    t = json.loads(texts.read_text(encoding="utf-8"))

    root = g.get("story_root", {}).get("Properties", {}).get("narrative", "")
    desc = t.get("gpt-4o_withGEST_t-1.0", "")
    return desc, root


RECONSTITUTE_PROMPT = """\
Below is a short text description derived from a video game simulation. \
It describes the actions performed by characters in the scene, \
including their locations and interactions.

Your task is to infer the underlying story — the narrative intent behind \
these actions. Write a concise root narrative (2–5 sentences) that captures: \
who the characters are, what their relationship is, what they are trying to \
do, and how the scene resolves. Do not simply paraphrase the actions — \
infer the story behind them. Be specific about character roles and motivations.

Here is one example:

DESCRIPTION:
{example_description}

ROOT NARRATIVE:
{example_narrative}

Now do the same for this description:

DESCRIPTION:
{description}

ROOT NARRATIVE:"""


def _call_gpt(model: str, prompt: str, max_retries: int = 3) -> str:
    from openai import OpenAI
    client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])

    for attempt in range(max_retries + 1):
        try:
            resp = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": "You are a creative writing analyst."},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.7,
                max_tokens=300,
            )
            return resp.choices[0].message.content.strip()
        except Exception as e:
            if attempt == max_retries:
                raise
            delay = 2.0 * (2 ** attempt) + random.uniform(0, 1)
            print(f"    [RETRY] {type(e).__name__}, waiting {delay:.1f}s")
            time.sleep(delay)


def reconstitute_story(story, example_desc: str, example_narrative: str,
                        model: str = DEFAULT_MODEL, force: bool = False,
                        dry_run: bool = False) -> bool:
    """Generate and save reconstituted_narrative.json for one procedural story."""
    out_path = story.reconstituted_narrative_path

    if out_path.exists() and not force:
        print(f"  [SKIP] {story.story_dir.name} — already reconstituted")
        return True

    desc = story.description
    if not desc:
        print(f"  [SKIP] {story.story_dir.name} — no description")
        return True

    print(f"  [GEN]  {story.story_dir.name} ({story.actor_count} actors)")

    prompt = RECONSTITUTE_PROMPT.format(
        example_description=example_desc,
        example_narrative=example_narrative,
        description=desc,
    )

    if dry_run:
        print(f"    [DRY-RUN] prompt length: {len(prompt)} chars")
        return True

    try:
        narrative = _call_gpt(model, prompt)
        data = {
            "narrative": narrative,
            "model": model,
            "source_description": desc,
        }
        out_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"    saved -> {out_path.name}")
        return True
    except Exception as e:
        print(f"    [ERROR] {story.story_dir.name}: {e}")
        return False


def reconstitute_all(model: str = DEFAULT_MODEL, force: bool = False,
                      dry_run: bool = False, n_per_group: int = 5) -> dict:
    """Reconstitute narratives for all procedural stories that are matched to agentic groups."""
    print("Loading one-shot example (worker1)...")
    example_desc, example_narrative = _load_oneshot_example()
    print(f"  Example description: {len(example_desc)} chars")
    print(f"  Example narrative:   {len(example_narrative)} chars")

    print("\nLoading stories...")
    agentic = load_agentic_stories()
    procedural = load_procedural_stories()
    matches = select_procedural_matches(agentic, procedural, n_per_group=n_per_group)
    print(f"  Procedural matches to reconstitute: {len(matches)}")

    ok = err = skip = 0
    for story in matches:
        result = reconstitute_story(
            story, example_desc, example_narrative,
            model=model, force=force, dry_run=dry_run,
        )
        if result:
            if story.reconstituted_narrative_path.exists() or dry_run:
                ok += 1
            else:
                skip += 1
        else:
            err += 1

    summary = {"total": len(matches), "ok": ok, "skipped": skip, "errors": err}
    print(f"\nDone: {ok} reconstituted, {skip} skipped, {err} errors")
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Reconstitute root narratives for procedural stories")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--n-per-group", type=int, default=5)
    args = parser.parse_args()

    reconstitute_all(
        model=args.model,
        force=args.force,
        dry_run=args.dry_run,
        n_per_group=args.n_per_group,
    )
