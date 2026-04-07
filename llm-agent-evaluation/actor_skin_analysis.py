"""
Actor skin analysis — 3-model jury evaluates whether chosen actor skins match the story narrative.

For each agentic GEST group (5 unique stories), extracts:
  - Root narrative
  - Each actor's name and assigned skin description (cross-referenced from Lua)

Asks all 3 jury judges: does this skin fit this character in this story?
Returns binary match + one-sentence reasoning per actor per judge.

Usage:
    python actor_skin_analysis.py [--judges gpt-5.2-pro gemini claude] [--force]
"""

import json
import os
import re
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

_EXPERIMENTS_DIR = Path(__file__).parent.parent.parent / "gest-mta-experiments" / "experiments"
if str(_EXPERIMENTS_DIR) not in sys.path:
    sys.path.insert(0, str(_EXPERIMENTS_DIR))

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent))

from catalog import load_agentic_stories

LUA_PATH = Path("c:/nick/PhD/repos/mta-sim/src/story/Actions/SetPlayerSkin.lua")
RESULTS_DIR = Path(__file__).parent.parent / "output" / "evaluation"
DEFAULT_JUDGES = ["gpt-5.2-pro", "gemini", "claude"]

PROMPT = """\
You are evaluating an AI story generation system that automatically casts \
characters by assigning visual appearances to actors.

Below is the story's root narrative, followed by one actor's name and the \
visual description of their assigned appearance.

Story narrative:
{narrative}

Actor: {name}
Assigned appearance: {skin_description}

Score how well this appearance fits this character's role in the story on \
three dimensions. Use 0 = mismatch, 1 = neutral/uncertain, 2 = good match.

- gender: does the appearance's gender match what is implied by the character's role?
- age: does the appearance's age match what is implied by the character's role?
- attire: does the clothing/style suit the setting and role described in the narrative?

Answer with EXACTLY this JSON and nothing else:
{{"gender": 0-2, "age": 0-2, "attire": 0-2, "reason": "<one sentence>"}}"""


def load_skins(lua_path: Path) -> dict:
    text = lua_path.read_text(encoding="utf-8")
    skins = {}
    for m in re.finditer(r'SetPlayerSkin\((\d+),\s*"([^"]+)",\s*(\d+)\)', text):
        sid, desc, gender = int(m.group(1)), m.group(2), int(m.group(3))
        skins[sid] = {"description": desc, "gender": gender}
    return skins


def _api_retry(fn, max_retries=3, base_delay=2.0):
    for attempt in range(max_retries + 1):
        try:
            return fn()
        except Exception as e:
            name = type(e).__name__
            if any(k in name for k in ("RateLimit", "Timeout", "ResourceExhausted")):
                if attempt == max_retries:
                    raise
                delay = base_delay * (2 ** attempt) + random.uniform(0, 1)
                print(f"    [RETRY] {name}, waiting {delay:.1f}s")
                time.sleep(delay)
            else:
                raise


def _parse_response(raw: str):
    for pattern in [
        lambda s: json.loads(s),
        lambda s: json.loads(re.search(r'\{[^{}]*\}', s, re.DOTALL).group(0)),
    ]:
        try:
            r = pattern(raw)
            if isinstance(r, dict) and "gender" in r and "age" in r and "attire" in r:
                return {
                    "gender": int(r["gender"]),
                    "age": int(r["age"]),
                    "attire": int(r["attire"]),
                    "reason": r.get("reason", ""),
                }
        except Exception:
            pass
    return None


def _create_judge(name):
    from vlm_judge import create_judge
    return create_judge(name)


def run_actor_skin_analysis(
    judges=DEFAULT_JUDGES,
    force: bool = False,
    output_path: Path = None,
):
    if output_path is None:
        output_path = RESULTS_DIR / "actor_skin_analysis.json"
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Load existing for resume
    existing = {}
    if output_path.exists() and not force:
        prev = json.loads(output_path.read_text(encoding="utf-8"))
        for g in prev.get("groups", []):
            existing[g["gest_group"]] = g
        print(f"Resume: {len(existing)} groups already evaluated")

    skins = load_skins(LUA_PATH)
    print(f"Loaded {len(skins)} skins")

    stories = load_agentic_stories()

    # Collect unique groups with their actors
    seen_groups = set()
    groups_to_eval = []
    for story in sorted(stories, key=lambda s: s.worker_id):
        if story.gest_group in seen_groups:
            continue
        seen_groups.add(story.gest_group)

        narrative = story.root_narrative
        if not narrative:
            continue

        gest_file = story.story_dir / "detailed_graph" / "take1" / "detail_gest.json"
        g = json.loads(gest_file.read_text(encoding="utf-8"))

        actors = []
        for k, v in g.items():
            if not isinstance(v, dict):
                continue
            props = v.get("Properties", {})
            if v.get("Action") != "Exists" or "Name" not in props or "SkinId" not in props:
                continue
            skin = skins.get(props["SkinId"])
            if not skin:
                continue
            actors.append({
                "actor_key": k,
                "actor_name": props["Name"],
                "skin_id": props["SkinId"],
                "skin_description": skin["description"],
                "judges": {},
            })

        groups_to_eval.append({
            "gest_group": story.gest_group,
            "worker_id": story.worker_id,
            "narrative": narrative,
            "actors": actors,
        })

    results = {g["gest_group"]: g for g in groups_to_eval}
    # Restore already-completed judge results
    for group_id, prev_group in existing.items():
        if group_id in results:
            for prev_actor in prev_group.get("actors", []):
                for cur_actor in results[group_id]["actors"]:
                    if cur_actor["actor_key"] == prev_actor["actor_key"]:
                        cur_actor["judges"] = prev_actor.get("judges", {})

    # Run each judge
    for judge_name in judges:
        print(f"\n{'='*60}\nJudge: {judge_name}\n{'='*60}")
        judge = _create_judge(judge_name)

        for group in sorted(results.values(), key=lambda g: g["gest_group"]):
            print(f"\n  Group {group['gest_group']}: {group['narrative'][:80]}...")

            for actor in group["actors"]:
                if judge_name in actor["judges"]:
                    print(f"    [SKIP] {actor['actor_name']} — already judged by {judge_name}")
                    continue

                print(f"    {actor['actor_name']} (skin {actor['skin_id']}): {actor['skin_description'][:55]}")

                prompt = PROMPT.format(
                    narrative=group["narrative"],
                    name=actor["actor_name"],
                    skin_description=actor["skin_description"],
                )

                try:
                    raw = _api_retry(lambda: judge.judge(prompt, frames_b64=None))
                except Exception as e:
                    print(f"      [ERROR] {e}")
                    continue

                parsed = _parse_response(raw)
                if parsed is None:
                    print(f"      [PARSE ERROR] {raw[:100]}")
                    parsed = {"match": None, "reason": raw[:200]}

                print(f"      gender={parsed['gender']} age={parsed['age']} attire={parsed['attire']} — {parsed['reason']}")
                actor["judges"][judge_name] = parsed

            _save(output_path, results, judges)

        judge.cleanup()

    _save(output_path, results, judges, compute_summary=True)
    print(f"\nDone -> {output_path}")
    return json.loads(output_path.read_text(encoding="utf-8"))


def _save(output_path, results, judges, compute_summary=False):
    groups_list = sorted(results.values(), key=lambda g: g["gest_group"])
    out = {"groups": groups_list}
    if compute_summary:
        out["summary"] = _summarize(groups_list, judges)
    output_path.write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")


def _summarize(groups_list, judge_names):
    from collections import defaultdict
    DIMS = ("gender", "age", "attire")
    per_judge = {j: {d: [] for d in DIMS} for j in judge_names}
    overall = {d: [] for d in DIMS}

    for group in groups_list:
        for actor in group["actors"]:
            for jname, result in actor["judges"].items():
                for dim in DIMS:
                    val = result.get(dim)
                    if val is not None:
                        per_judge.setdefault(jname, {d: [] for d in DIMS})[dim].append(int(val))
                        overall[dim].append(int(val))

    def _avg(lst):
        return round(sum(lst) / len(lst), 3) if lst else None

    summary = {
        "num_groups": len(groups_list),
        "num_actors": sum(len(g["actors"]) for g in groups_list),
        "overall": {d: _avg(overall[d]) for d in DIMS},
        "per_judge": {
            j: {d: _avg(per_judge[j][d]) for d in DIMS}
            for j in judge_names
        },
    }

    print(f"\n{'='*60}")
    print("SUMMARY  (scores 0-2: 0=mismatch, 1=neutral, 2=match)")
    print(f"  Groups evaluated : {summary['num_groups']}")
    print(f"  Actors evaluated : {summary['num_actors']}")
    print(f"  Overall          : gender={summary['overall']['gender']}  age={summary['overall']['age']}  attire={summary['overall']['attire']}")
    for j in judge_names:
        s = summary["per_judge"].get(j, {})
        print(f"  {j:20s}: gender={s.get('gender')}  age={s.get('age')}  attire={s.get('attire')}")

    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--judges", nargs="+", default=DEFAULT_JUDGES,
                        choices=["gpt-5.2-pro", "gemini", "claude"])
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()

    run_actor_skin_analysis(judges=args.judges, force=args.force, output_path=args.output)
