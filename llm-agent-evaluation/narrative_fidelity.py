"""
Narrative fidelity — LLM jury evaluates how well VDG descriptions preserve
the original root narrative's intent.

For each agentic GEST group (5 unique stories), presents the original root
narrative alongside the VDG description and asks 3 judges to score fidelity
on three dimensions (0–5 each):
  - event_coverage: are all key events present?
  - character_intent: are motivations/relationships preserved?
  - causal_structure: are cause-effect chains intact?

Usage:
    python narrative_fidelity.py [--judges gpt-5.2-pro gemini claude] [--force]
"""

import json
import os
import re
import sys
import time
import random
import argparse
from pathlib import Path

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

RESULTS_DIR = Path(__file__).parent.parent / "output" / "evaluation"
DEFAULT_JUDGES = ["gpt-5.2-pro", "gemini", "claude"]

PROMPT = """\
You are evaluating an AI story generation pipeline. The system starts with \
a root narrative (the intended story) and executes it in a game engine. \
A separate module then generates a textual description (VDG) of what \
actually happened in the simulation.

Your task: evaluate how faithfully the VDG description preserves the \
original narrative's intent.

## Original root narrative
{narrative}

## VDG description (generated from simulation)
{vdg}

## Scoring (0–5 each)

- **event_coverage**: Are all key events from the narrative present in the \
VDG? 0 = most events missing, 5 = all events faithfully represented.
- **character_intent**: Are character motivations, relationships, and roles \
preserved? 0 = completely lost, 5 = fully preserved.
- **causal_structure**: Are cause-effect chains and the logical flow of \
events intact? 0 = no causal logic, 5 = causal structure fully preserved.

Answer with EXACTLY this JSON and nothing else:
{{"event_coverage": 0-5, "character_intent": 0-5, "causal_structure": 0-5, "reason": "<one sentence>"}}"""


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
            if isinstance(r, dict) and "event_coverage" in r:
                return {
                    "event_coverage": int(r["event_coverage"]),
                    "character_intent": int(r["character_intent"]),
                    "causal_structure": int(r["causal_structure"]),
                    "reason": r.get("reason", ""),
                }
        except Exception:
            pass
    return None


def _create_judge(name):
    from vlm_judge import create_judge
    return create_judge(name)


def run_narrative_fidelity(
    judges=DEFAULT_JUDGES,
    force: bool = False,
    output_path: Path = None,
):
    if output_path is None:
        output_path = RESULTS_DIR / "narrative_fidelity.json"
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Load existing for resume
    existing = {}
    if output_path.exists() and not force:
        prev = json.loads(output_path.read_text(encoding="utf-8"))
        for g in prev.get("groups", []):
            existing[g["gest_group"]] = g
        print(f"Resume: {len(existing)} groups already evaluated")

    stories = load_agentic_stories()

    # One representative per GEST group
    group_rep = {}
    for s in sorted(stories, key=lambda s: s.worker_id):
        if s.gest_group not in group_rep:
            group_rep[s.gest_group] = s

    groups_to_eval = []
    for group, story in sorted(group_rep.items()):
        narrative = story.root_narrative
        vdg = story.description
        if not narrative or not vdg:
            print(f"  [SKIP] Group {group}: missing narrative or VDG")
            continue
        groups_to_eval.append({
            "gest_group": group,
            "worker_id": story.worker_id,
            "narrative": narrative,
            "vdg": vdg,
            "judges": {},
        })

    results = {g["gest_group"]: g for g in groups_to_eval}
    # Restore already-completed judge results
    for group_id, prev_group in existing.items():
        if group_id in results:
            results[group_id]["judges"] = prev_group.get("judges", {})

    print(f"Groups to evaluate: {len(results)}")

    for judge_name in judges:
        print(f"\n{'='*60}\nJudge: {judge_name}\n{'='*60}")
        judge = _create_judge(judge_name)

        for group in sorted(results.values(), key=lambda g: g["gest_group"]):
            if judge_name in group["judges"]:
                print(f"  [SKIP] Group {group['gest_group']} — already judged by {judge_name}")
                continue

            print(f"\n  Group {group['gest_group']}: {group['narrative'][:80]}...")

            prompt = PROMPT.format(
                narrative=group["narrative"],
                vdg=group["vdg"],
            )

            try:
                raw = _api_retry(lambda: judge.judge(prompt, frames_b64=None))
            except Exception as e:
                print(f"    [ERROR] {e}")
                continue

            parsed = _parse_response(raw)
            if parsed is None:
                print(f"    [PARSE ERROR] {raw[:100]}")
                parsed = {"event_coverage": None, "character_intent": None,
                          "causal_structure": None, "reason": raw[:200]}

            print(f"    event={parsed['event_coverage']} intent={parsed['character_intent']} causal={parsed['causal_structure']} — {parsed['reason']}")
            group["judges"][judge_name] = parsed

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
    DIMS = ("event_coverage", "character_intent", "causal_structure")
    per_judge = {j: {d: [] for d in DIMS} for j in judge_names}
    overall = {d: [] for d in DIMS}

    for group in groups_list:
        for jname, result in group["judges"].items():
            for dim in DIMS:
                val = result.get(dim)
                if val is not None:
                    per_judge.setdefault(jname, {d: [] for d in DIMS})[dim].append(int(val))
                    overall[dim].append(int(val))

    def _avg(lst):
        return round(sum(lst) / len(lst), 2) if lst else None

    summary = {
        "num_groups": len(groups_list),
        "overall": {d: _avg(overall[d]) for d in DIMS},
        "per_judge": {
            j: {d: _avg(per_judge[j][d]) for d in DIMS}
            for j in judge_names
        },
    }

    print(f"\n{'='*60}")
    print("SUMMARY  (scores 0-5)")
    print(f"  Groups evaluated : {summary['num_groups']}")
    print(f"  Overall          : event={summary['overall']['event_coverage']}  intent={summary['overall']['character_intent']}  causal={summary['overall']['causal_structure']}")
    for j in judge_names:
        s = summary["per_judge"].get(j, {})
        print(f"  {j:20s}: event={s.get('event_coverage')}  intent={s.get('character_intent')}  causal={s.get('causal_structure')}")

    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--judges", nargs="+", default=DEFAULT_JUDGES,
                        choices=["gpt-5.2-pro", "gemini", "claude"])
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()

    run_narrative_fidelity(judges=args.judges, force=args.force, output_path=args.output)
