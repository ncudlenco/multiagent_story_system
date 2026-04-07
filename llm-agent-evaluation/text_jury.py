"""
Text jury — LLM jury compares agentic vs procedural story texts head-to-head.

Three experiment modes:
  vdg-vs-vdg            agentic texts.json desc  vs  procedural texts.json desc
  narrative-vs-vdg      agentic root narrative   vs  procedural texts.json desc
  narrative-vs-reconstituted  agentic root narrative   vs  procedural reconstituted narrative

Each pair is presented to 3 judges (GPT, Gemini, Claude) in randomised A/B order.
Judges score both texts 0–100 with no ties.

Usage:
    python text_jury.py --experiment vdg-vs-vdg [--judges gpt-5.2-pro gemini claude]
    python text_jury.py --experiment all
"""

import argparse
import json
import os
import random
import sys
import time
from datetime import datetime
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

from catalog import load_agentic_stories, load_procedural_stories, select_procedural_matches

RESULTS_DIR = Path(__file__).parent.parent / "output" / "evaluation"

EXPERIMENTS = ["vdg-vs-vdg", "narrative-vs-vdg", "narrative-vs-reconstituted", "narrative-vs-own-vdg"]

# ---------------------------------------------------------------------------
# Prompt
# ---------------------------------------------------------------------------
TEXT_JURY_PROMPT = """\
You will be presented with two textual descriptions of scenes from a video \
game simulation (labelled Text A and Text B). Each describes a sequence of \
actions performed by characters. Your task is to read both and score each \
one between 0 and 100. Scores must have NO ties.

## Scoring criteria (in order of importance)

1. **Narrative coherence**: The description should have a logical arc — events \
follow one another meaningfully. Beginning, middle and end feel connected.

2. **Character motivation**: Characters' actions feel purposeful. We understand \
why they do what they do, even if not explicitly stated.

3. **Temporal logic**: Ordering and timing of events make sense. Cause-and-effect \
is plausible.

4. **Richness**: Enough detail about actions, interactions and atmosphere. \
Generic, vague or very short descriptions score lower.

## Descriptions

### Text A
{text_a}

### Text B
{text_b}

## Required output format

Respond with EXACTLY this JSON and nothing else:
{{"Text A": <score 0-100>, "Text B": <score 0-100>}}"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _shuffle_pair(text_source_a, text_source_b, label_a, label_b, seed):
    """Randomly assign the two texts to Text A/B. Returns (prompt, mapping)."""
    rng = random.Random(seed)
    if rng.random() < 0.5:
        mapping = {"Text A": label_a, "Text B": label_b}
        text_a, text_b = text_source_a, text_source_b
    else:
        mapping = {"Text A": label_b, "Text B": label_a}
        text_a, text_b = text_source_b, text_source_a
    prompt = TEXT_JURY_PROMPT.format(text_a=text_a, text_b=text_b)
    return prompt, mapping


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


def _parse_scores(raw):
    import re
    if not raw:
        return None
    raw = raw.strip()
    for pattern in [
        lambda s: json.loads(s),
        lambda s: json.loads(re.search(r'```(?:json)?\s*(\{.*?\})\s*```', s, re.DOTALL).group(1)),
        lambda s: json.loads(re.search(r'\{[^{}]*\}', s, re.DOTALL).group(0)),
    ]:
        try:
            result = pattern(raw)
            if isinstance(result, dict) and "Text A" in result and "Text B" in result:
                return {"Text A": int(result["Text A"]), "Text B": int(result["Text B"])}
        except Exception:
            pass
    return None


def _create_judge(name):
    from vlm_judge import create_judge
    return create_judge(name)


# ---------------------------------------------------------------------------
# Core evaluation
# ---------------------------------------------------------------------------
def evaluate_pair(judge, text_a, text_b, label_a, label_b, pair_id, seed=42):
    """Ask judge to score two texts head-to-head. Returns result dict."""
    prompt, mapping = _shuffle_pair(text_a, text_b, label_a, label_b, seed ^ hash(pair_id) % (2**31))

    t0 = time.time()
    try:
        raw = _api_retry(lambda: judge.judge(prompt, frames_b64=None))
    except Exception as e:
        print(f"    [ERROR] {judge.name}: {e}")
        if "401" in str(e) or "invalid_api_key" in str(e):
            raise SystemExit(f"Fatal: invalid API key for {judge.name}.")
        return None
    latency = time.time() - t0

    scores_ab = _parse_scores(raw)
    if scores_ab is None:
        print(f"    [PARSE ERROR] {judge.name}: {raw[:200]}")
        return {"scores": None, "raw_response": raw, "latency_s": round(latency, 2), "mapping": mapping}

    # De-anonymize: {"agentic": score, "procedural": score}
    deanon = {mapping[label]: score for label, score in scores_ab.items()}
    return {
        "scores": deanon,
        "anonymized_scores": scores_ab,
        "raw_response": raw,
        "latency_s": round(latency, 2),
        "mapping": mapping,
    }


# ---------------------------------------------------------------------------
# Text getters per experiment
# ---------------------------------------------------------------------------
def _get_texts(experiment, ag_story, pr_story):
    """Return (text_a, text_b) for the given experiment mode.

    For narrative-vs-own-vdg, pr_story is ignored — both texts come from ag_story.
    """
    if experiment == "vdg-vs-vdg":
        return ag_story.description, pr_story.description

    elif experiment == "narrative-vs-vdg":
        return ag_story.root_narrative, pr_story.description

    elif experiment == "narrative-vs-reconstituted":
        return ag_story.root_narrative, pr_story.reconstituted_narrative

    elif experiment == "narrative-vs-own-vdg":
        # Internal fidelity: how much narrative intent survives execution
        return ag_story.root_narrative, ag_story.description

    raise ValueError(f"Unknown experiment: {experiment}")


def _texts_available(experiment, ag_story, pr_story):
    ag_text, pr_text = _get_texts(experiment, ag_story, pr_story)
    return bool(ag_text) and bool(pr_text)


# ---------------------------------------------------------------------------
# Main jury runner
# ---------------------------------------------------------------------------
def run_text_jury(
    experiment="vdg-vs-vdg",
    judges=("gpt-5.2-pro", "gemini", "claude"),
    n_matches=5,
    output_path=None,
    seed=42,
    resume=False,
):
    if experiment == "all":
        for exp in EXPERIMENTS:
            run_text_jury(
                experiment=exp,
                judges=judges,
                n_matches=n_matches,
                output_path=None,
                seed=seed,
                resume=resume,
            )
        return

    if output_path is None:
        output_path = RESULTS_DIR / f"text_jury_{experiment}.json"
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    print(f"\nExperiment: {experiment}")
    print("Loading stories...")
    agentic = load_agentic_stories()
    procedural = load_procedural_stories()
    matches = select_procedural_matches(agentic, procedural, n_per_group=n_matches)

    pairs = []

    if experiment == "narrative-vs-own-vdg":
        # Internal fidelity: one pair per GEST group (5 total), no procedural needed
        group_rep = {}
        for s in sorted(agentic, key=lambda s: s.worker_id):
            if s.gest_group not in group_rep:
                group_rep[s.gest_group] = s
        for group, ag_story in sorted(group_rep.items()):
            if not _texts_available(experiment, ag_story, None):
                continue
            pair_id = f"g{ag_story.gest_group}_internal"
            pairs.append((pair_id, ag_story, None))
    else:
        # One agentic representative per group × matched procedurals
        group_rep = {}
        for s in agentic:
            if s.gest_group not in group_rep:
                group_rep[s.gest_group] = s

        from collections import defaultdict
        proc_by_actors = defaultdict(list)
        for s in matches:
            proc_by_actors[s.actor_count].append(s)

        for group, ag_story in sorted(group_rep.items()):
            candidates = proc_by_actors.get(ag_story.actor_count, [])
            if not candidates:
                for delta in [1, -1, 2, -2]:
                    candidates = proc_by_actors.get(ag_story.actor_count + delta, [])
                    if candidates:
                        break
            for pr_story in candidates[:n_matches]:
                if not _texts_available(experiment, ag_story, pr_story):
                    continue
                pair_id = f"g{ag_story.gest_group}_vs_{pr_story.story_dir.name[:24]}"
                pairs.append((pair_id, ag_story, pr_story))

    print(f"  Pairs: {len(pairs)}")

    # Resume
    existing = {}
    if resume and output_path.exists():
        prev = json.loads(output_path.read_text())
        for r in prev.get("pairs", []):
            existing[r["pair_id"]] = r
        print(f"  Resume: {len(existing)} existing pairs loaded")

    results = dict(existing)

    for judge_name in judges:
        print(f"\n{'='*60}\nJudge: {judge_name}\n{'='*60}")
        judge = _create_judge(judge_name)

        for i, (pair_id, ag_story, pr_story) in enumerate(pairs):
            if pair_id in results and judge_name in results[pair_id].get("judges", {}):
                print(f"  [{i+1}/{len(pairs)}] SKIP (resume): {pair_id}")
                continue

            print(f"  [{i+1}/{len(pairs)}] {pair_id}")
            ag_text, pr_text = _get_texts(experiment, ag_story, pr_story)
            # Labels describe what each text actually is
            if experiment == "narrative-vs-own-vdg":
                label_a, label_b = "narrative", "vdg"
            elif experiment in ("narrative-vs-vdg", "narrative-vs-reconstituted"):
                label_a, label_b = "agentic", "procedural"
            else:
                label_a, label_b = "agentic", "procedural"
            result = evaluate_pair(judge, ag_text, pr_text, label_a, label_b,
                                   pair_id=pair_id, seed=seed)

            if pair_id not in results:
                results[pair_id] = {
                    "pair_id": pair_id,
                    "experiment": experiment,
                    "agentic_group": ag_story.gest_group,
                    "agentic_worker": ag_story.worker_id,
                    "agentic_actor_count": ag_story.actor_count,
                    "procedural_story": pr_story.story_dir.name if pr_story else None,
                    "procedural_actor_count": pr_story.actor_count if pr_story else None,
                    "judges": {},
                }

            if result:
                results[pair_id]["judges"][judge_name] = result

            _save(output_path, results, judges, seed, experiment)

        judge.cleanup()

    _save(output_path, results, judges, seed, experiment, compute_summary=True)
    print(f"\nDone -> {output_path}")


def _save(output_path, results, judges, seed, experiment, compute_summary=False):
    pairs_list = sorted(results.values(), key=lambda r: r["pair_id"])
    out = {
        "metadata": {
            "experiment": experiment,
            "judges": list(judges),
            "seed": seed,
            "timestamp": datetime.now().isoformat(timespec="seconds"),
        },
        "pairs": pairs_list,
    }
    if compute_summary:
        out["summary"] = _summarize(pairs_list, list(judges))
    output_path.write_text(json.dumps(out, indent=2), encoding="utf-8")


def _summarize(pairs_list, judge_names):
    from collections import defaultdict
    wins = defaultdict(int)
    source_scores = defaultdict(list)

    for pair in pairs_list:
        for jname in judge_names:
            jdata = pair.get("judges", {}).get(jname)
            if not jdata or not jdata.get("scores"):
                continue
            scores = jdata["scores"]
            for source, score in scores.items():
                source_scores[source].append(score)
            winner = max(scores, key=scores.get)
            wins[winner] += 1

    avg_scores = {
        source: round(sum(sc) / len(sc), 2)
        for source, sc in sorted(source_scores.items()) if sc
    }
    return {"num_pairs": len(pairs_list), "wins": dict(wins), "avg_scores": avg_scores}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Text jury: agentic vs procedural")
    parser.add_argument("--experiment", default="vdg-vs-vdg",
                        choices=EXPERIMENTS + ["all"])
    parser.add_argument("--judges", nargs="+",
                        default=["gpt-5.2-pro", "gemini", "claude"])
    parser.add_argument("--n-matches", type=int, default=5)
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()

    run_text_jury(
        experiment=args.experiment,
        judges=args.judges,
        n_matches=args.n_matches,
        output_path=args.output,
        seed=args.seed,
        resume=args.resume,
    )
