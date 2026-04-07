"""
Video jury — LLM jury compares agentic vs procedural story videos head-to-head.

Four experiment modes:
  video-only              no text — pure video judgment
  vdg-vs-vdg              agentic video + VDG desc    vs  procedural video + VDG desc
  narrative-vs-vdg        agentic video + root narr   vs  procedural video + VDG desc
  narrative-vs-reconstituted  agentic video + root narr vs procedural video + reconstituted narr

Frames from both stories are shown to the judge labelled as Story A / Story B,
in randomised order to reduce position bias.

Usage:
    python video_jury.py --experiment narrative-vs-vdg [--judges gpt-5.2-pro gemini claude]
    python video_jury.py --experiment all
"""

import argparse
import json
import os
import random
import sys
import time
from datetime import datetime
from pathlib import Path

import numpy as np

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

from catalog import load_agentic_stories, load_procedural_stories, select_procedural_matches, Story

RESULTS_DIR = Path(__file__).parent.parent / "output" / "evaluation"
MAX_FRAMES_PER_STORY = 10   # per story — 2 stories = 20 frames total per pair
EXPERIMENTS = ["video-only", "vdg-vs-vdg", "narrative-vs-vdg", "narrative-vs-reconstituted"]

# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------
VIDEO_JURY_PROMPT_WITH_TEXT = """\
You will be shown video frames from two different stories (Story A and Story B), \
each accompanied by a text description. The frames are labelled to indicate which \
story they belong to.

Your task is to read both descriptions and watch both sets of frames, then score \
each story between 0 and 100 based on the criteria below. Scores must have NO ties.

## Scoring criteria (in order of importance)

1. **Narrative coherence**: Does the sequence of events make sense as a story? \
Is there a logical arc from beginning to end? Do events feel connected rather \
than random?

2. **Character motivation**: Do characters' actions appear purposeful? \
They should seem to act with reason rather than arbitrarily.

3. **Temporal logic**: Does the order of events make sense? \
Are cause-and-effect relationships plausible?

4. **Description accuracy**: Does the text description accurately reflect \
what is visible in the video frames?

## Story A description
{text_a}

## Story B description
{text_b}

## Required output format

Respond with EXACTLY this JSON and nothing else:
{{"Story A": <score 0-100>, "Story B": <score 0-100>}}"""

VIDEO_JURY_PROMPT_NO_TEXT = """\
You will be shown video frames from two different stories (Story A and Story B). \
The frames are labelled to indicate which story they belong to.

Your task is to watch both sets of frames and score each story between 0 and 100 \
based on the criteria below. Scores must have NO ties.

## Scoring criteria (in order of importance)

1. **Narrative coherence**: Does the sequence of events make sense as a story? \
Is there a logical arc from beginning to end? Do events feel connected rather \
than random?

2. **Character motivation**: Do characters' actions appear purposeful? \
They should seem to act with reason rather than arbitrarily.

3. **Temporal logic**: Does the order of events make sense? \
Are cause-and-effect relationships plausible?

## Required output format

Respond with EXACTLY this JSON and nothing else:
{{"Story A": <score 0-100>, "Story B": <score 0-100>}}"""


# ---------------------------------------------------------------------------
# Frame sampling
# ---------------------------------------------------------------------------
def compute_hybrid_frames(story: Story, max_frames: int = MAX_FRAMES_PER_STORY):
    efm_path = story.event_frame_mapping_path
    if not efm_path or not efm_path.exists():
        return None
    try:
        data = json.loads(efm_path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not data or not isinstance(data, list):
        return None

    raw_events = data[0].get("events", [])
    video_fps = data[0].get("fps", 60)

    midpoints = set()
    max_frame = 0
    for e in raw_events:
        sf = e.get("startFrame")
        if sf is None:
            continue
        ef = e.get("endFrame", sf)
        sf, ef = int(sf), int(ef)
        midpoints.add((sf + ef) // 2)
        max_frame = max(max_frame, sf, ef)

    if not midpoints:
        return None

    total_frames = _probe_total_frames(story.video_path) or (max_frame + 1)
    segment_len = max(1, int(video_fps / 1.0))
    uniform_frames = list(range(segment_len // 2, total_frames, segment_len))

    remaining = max_frames - len(midpoints)
    if remaining > 0:
        uniform_only = [f for f in uniform_frames if f not in midpoints]
        if len(uniform_only) > remaining:
            keep_idx = np.linspace(0, len(uniform_only) - 1, remaining, dtype=int)
            uniform_only = [uniform_only[i] for i in keep_idx]
        hybrid = sorted(midpoints | set(uniform_only))
    else:
        hybrid = sorted(midpoints)[:max_frames]

    return hybrid


def _probe_total_frames(video_path: Path):
    try:
        from decord import VideoReader, cpu
        vr = VideoReader(str(video_path), ctx=cpu(0))
        return len(vr)
    except Exception:
        return None


def load_frames_at_indices(video_path: Path, indices: list):
    from decord import VideoReader, cpu
    from PIL import Image
    vr = VideoReader(str(video_path), ctx=cpu(0))
    total = len(vr)
    valid = [min(i, total - 1) for i in indices]
    frames_np = vr.get_batch(valid).asnumpy()
    return [Image.fromarray(f) for f in frames_np]


def load_frames_uniform(video_path: Path, max_frames: int = MAX_FRAMES_PER_STORY):
    from vlm_judge import load_video_frames, subsample_frames
    frames = load_video_frames(str(video_path))
    return subsample_frames(frames, max_n=max_frames)


def get_frames_for_story(story: Story, max_frames: int = MAX_FRAMES_PER_STORY):
    try:
        indices = compute_hybrid_frames(story, max_frames=max_frames)
        if indices:
            return load_frames_at_indices(story.video_path, indices)
        return load_frames_uniform(story.video_path, max_frames=max_frames)
    except Exception as e:
        print(f"    [WARN] frame loading failed for {story.story_dir.name}: {e}")
        return []


def frames_to_b64(frames, label: str):
    """Encode frames with a label overlay. Returns list of b64 strings."""
    from vlm_judge import frames_to_base64
    from PIL import ImageDraw, ImageFont
    labelled = []
    for frame in frames:
        img = frame.copy()
        draw = ImageDraw.Draw(img)
        draw.rectangle([0, 0, 160, 28], fill=(0, 0, 0, 180))
        draw.text((8, 6), label, fill=(255, 255, 255))
        labelled.append(img)
    return frames_to_base64(labelled, max_size=(512, 512))


# ---------------------------------------------------------------------------
# Text getters per experiment
# ---------------------------------------------------------------------------
def _get_texts(experiment, ag_story, pr_story):
    """Return (agentic_text, procedural_text) or (None, None) for video-only."""
    if experiment == "video-only":
        return None, None
    elif experiment == "vdg-vs-vdg":
        return ag_story.description, pr_story.description
    elif experiment == "narrative-vs-vdg":
        return ag_story.root_narrative, pr_story.description
    elif experiment == "narrative-vs-reconstituted":
        return ag_story.root_narrative, pr_story.reconstituted_narrative
    raise ValueError(f"Unknown experiment: {experiment}")


def _texts_available(experiment, ag_story, pr_story):
    if experiment == "video-only":
        return True
    ag_text, pr_text = _get_texts(experiment, ag_story, pr_story)
    return bool(ag_text) and bool(pr_text)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
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
            if isinstance(result, dict) and "Story A" in result and "Story B" in result:
                return {"Story A": int(result["Story A"]), "Story B": int(result["Story B"])}
        except Exception:
            pass
    return None


def _create_judge(name):
    from vlm_judge import create_judge
    return create_judge(name)


# ---------------------------------------------------------------------------
# Core evaluation
# ---------------------------------------------------------------------------
def evaluate_pair(judge, ag_story, pr_story, experiment, pair_id, seed=42,
                  max_frames=MAX_FRAMES_PER_STORY):
    """Head-to-head comparison of one agentic vs one procedural story."""
    ag_text, pr_text = _get_texts(experiment, ag_story, pr_story)

    # Load frames for both stories
    ag_frames = get_frames_for_story(ag_story, max_frames)
    pr_frames = get_frames_for_story(pr_story, max_frames)

    # Randomise which is Story A / Story B
    rng = random.Random(seed ^ hash(pair_id) % (2**31))
    if rng.random() < 0.5:
        mapping = {"Story A": "agentic", "Story B": "procedural"}
        frames_a, frames_b = ag_frames, pr_frames
        text_a, text_b = ag_text, pr_text
    else:
        mapping = {"Story A": "procedural", "Story B": "agentic"}
        frames_a, frames_b = pr_frames, ag_frames
        text_a, text_b = pr_text, ag_text

    # Encode frames with labels
    all_frames_b64 = []
    if frames_a:
        all_frames_b64 += frames_to_b64(frames_a, "Story A")
    if frames_b:
        all_frames_b64 += frames_to_b64(frames_b, "Story B")

    # Build prompt
    if experiment == "video-only":
        prompt = VIDEO_JURY_PROMPT_NO_TEXT
    else:
        prompt = VIDEO_JURY_PROMPT_WITH_TEXT.format(
            text_a=text_a or "(no description)",
            text_b=text_b or "(no description)",
        )

    t0 = time.time()
    try:
        raw = _api_retry(lambda: judge.judge(prompt, all_frames_b64 or None))
    except Exception as e:
        print(f"    [ERROR] {judge.name}: {e}")
        if "401" in str(e) or "invalid_api_key" in str(e):
            raise SystemExit(f"Fatal: invalid API key for {judge.name}.")
        return None
    latency = time.time() - t0

    scores_ab = _parse_scores(raw)
    if scores_ab is None:
        print(f"    [PARSE ERROR] {judge.name}: {raw[:200]}")
        return {"scores": None, "raw_response": raw, "latency_s": round(latency, 2),
                "mapping": mapping, "num_frames_a": len(frames_a), "num_frames_b": len(frames_b)}

    deanon = {mapping[label]: score for label, score in scores_ab.items()}
    return {
        "scores": deanon,
        "anonymized_scores": scores_ab,
        "raw_response": raw,
        "latency_s": round(latency, 2),
        "mapping": mapping,
        "num_frames_a": len(frames_a),
        "num_frames_b": len(frames_b),
    }


# ---------------------------------------------------------------------------
# Main jury runner
# ---------------------------------------------------------------------------
def run_video_jury(
    experiment="narrative-vs-vdg",
    judges=("gpt-5.2-pro", "gemini", "claude"),
    n_matches=1,
    output_path=None,
    max_frames=MAX_FRAMES_PER_STORY,
    seed=42,
    resume=False,
):
    if experiment == "all":
        for exp in EXPERIMENTS:
            run_video_jury(
                experiment=exp,
                judges=judges,
                n_matches=n_matches,
                output_path=None,
                max_frames=max_frames,
                seed=seed,
                resume=resume,
            )
        return

    if output_path is None:
        output_path = RESULTS_DIR / f"video_jury_{experiment}.json"
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    print(f"\nExperiment: {experiment}")
    print("Loading stories...")
    agentic = load_agentic_stories()
    procedural = load_procedural_stories()
    matches = select_procedural_matches(agentic, procedural, n_per_group=n_matches)

    # One agentic rep per group (first worker in group)
    group_rep = {}
    for s in agentic:
        if s.gest_group not in group_rep:
            group_rep[s.gest_group] = s

    from collections import defaultdict
    proc_by_actors = defaultdict(list)
    for s in matches:
        proc_by_actors[s.actor_count].append(s)

    pairs = []
    for group, ag_story in sorted(group_rep.items()):
        candidates = proc_by_actors.get(ag_story.actor_count, [])
        if not candidates:
            for delta in [1, -1, 2, -2]:
                candidates = proc_by_actors.get(ag_story.actor_count + delta, [])
                if candidates:
                    break
        for pr_story in candidates[:n_matches]:
            if not ag_story.video_path.exists() or not pr_story.video_path.exists():
                continue
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
            result = evaluate_pair(judge, ag_story, pr_story, experiment,
                                   pair_id=pair_id, seed=seed, max_frames=max_frames)

            if pair_id not in results:
                results[pair_id] = {
                    "pair_id": pair_id,
                    "experiment": experiment,
                    "agentic_group": ag_story.gest_group,
                    "agentic_worker": ag_story.worker_id,
                    "agentic_actor_count": ag_story.actor_count,
                    "procedural_story": pr_story.story_dir.name,
                    "procedural_actor_count": pr_story.actor_count,
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
    parser = argparse.ArgumentParser(description="Video jury: head-to-head agentic vs procedural")
    parser.add_argument("--experiment", default="narrative-vs-vdg",
                        choices=EXPERIMENTS + ["all"])
    parser.add_argument("--judges", nargs="+",
                        default=["gpt-5.2-pro", "gemini", "claude"])
    parser.add_argument("--n-matches", type=int, default=5)
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--max-frames", type=int, default=MAX_FRAMES_PER_STORY)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()

    run_video_jury(
        experiment=args.experiment,
        judges=args.judges,
        n_matches=args.n_matches,
        output_path=args.output,
        max_frames=args.max_frames,
        seed=args.seed,
        resume=args.resume,
    )
