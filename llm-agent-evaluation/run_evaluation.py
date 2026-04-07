"""
Evaluation orchestrator — runs the full agentic vs procedural evaluation pipeline.

Steps:
  1. generate-texts      — generate texts.json for all 25 agentic stories via VDG
  2. reconstitute        — infer root narratives for matched procedural stories (one-shot GPT)
  3. text-jury           — LLM jury compares descriptions head-to-head (3 experiment modes)
  4. video-jury          — LLM jury rates story execution from event-aware video frames

Usage:
    # Full pipeline
    python run_evaluation.py --all

    # Individual steps
    python run_evaluation.py --generate-texts [--model gpt-4o] [--force]
    python run_evaluation.py --reconstitute [--model gpt-4o] [--force]
    python run_evaluation.py --text-jury [--experiment all] [--judges gpt-5.2-pro gemini claude]
    python run_evaluation.py --video-jury [--judges gpt-5.2-pro gemini claude]

    # Resume interrupted run
    python run_evaluation.py --text-jury --resume
    python run_evaluation.py --video-jury --resume
"""

import argparse
import sys
from pathlib import Path

RESULTS_DIR = Path(__file__).parent.parent / "output" / "evaluation"
JUDGES = ["gpt-5.2-pro", "gemini", "claude"]
ALL_JUDGE_CHOICES = ["gpt-5.2-pro", "gemini", "claude", "videollama3", "qwen3vl"]
EXPERIMENT_CHOICES = ["video-only", "vdg-vs-vdg", "narrative-vs-vdg", "narrative-vs-reconstituted", "narrative-vs-own-vdg", "all"]


def step_generate_texts(args):
    print("\n" + "=" * 60)
    print("STEP 1: Generate texts.json for agentic stories")
    print("=" * 60)
    sys.path.insert(0, str(Path(__file__).parent.parent))
    sys.path.insert(0, str(Path(__file__).parent))
    from generate_texts import generate_all
    generate_all(
        model=args.model,
        force=args.force,
        dry_run=args.dry_run,
        groups=args.groups if hasattr(args, "groups") else None,
    )


def step_reconstitute(args):
    print("\n" + "=" * 60)
    print("STEP 2: Reconstitute root narratives for procedural stories")
    print("=" * 60)
    sys.path.insert(0, str(Path(__file__).parent.parent))
    sys.path.insert(0, str(Path(__file__).parent))
    from reconstitute import reconstitute_all
    reconstitute_all(
        model=args.model,
        force=args.force,
        dry_run=args.dry_run,
        n_per_group=args.n_matches,
    )


def step_text_jury(args):
    print("\n" + "=" * 60)
    print("STEP 3: Text jury")
    print("=" * 60)
    sys.path.insert(0, str(Path(__file__).parent.parent))
    sys.path.insert(0, str(Path(__file__).parent))
    from text_jury import run_text_jury
    run_text_jury(
        experiment=args.experiment,
        judges=args.judges,
        n_matches=args.n_matches,
        output_path=args.output / f"text_jury_{args.experiment}.json" if args.output else None,
        seed=args.seed,
        resume=args.resume,
    )


def step_narrative_fidelity(args):
    print("\n" + "=" * 60)
    print("STEP 5: Narrative fidelity")
    print("=" * 60)
    sys.path.insert(0, str(Path(__file__).parent.parent))
    sys.path.insert(0, str(Path(__file__).parent))
    from narrative_fidelity import run_narrative_fidelity
    run_narrative_fidelity(
        judges=args.judges,
        force=args.force,
        output_path=args.output / "narrative_fidelity.json" if args.output != RESULTS_DIR else None,
    )


def step_video_jury(args):
    print("\n" + "=" * 60)
    print("STEP 4: Video jury")
    print("=" * 60)
    sys.path.insert(0, str(Path(__file__).parent.parent))
    sys.path.insert(0, str(Path(__file__).parent))
    from video_jury import run_video_jury
    run_video_jury(
        experiment=args.experiment,
        judges=args.judges,
        n_matches=1,  # Video jury always 1:1 (expensive image calls)
        output_path=args.output / f"video_jury_{args.experiment}.json" if args.output else None,
        max_frames=args.max_frames,
        seed=args.seed,
        resume=args.resume,
    )


def main():
    parser = argparse.ArgumentParser(
        description="Run agentic vs procedural story evaluation pipeline",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )

    # Which steps to run
    steps = parser.add_argument_group("Steps")
    steps.add_argument("--all", action="store_true", help="Run all steps")
    steps.add_argument("--generate-texts", action="store_true")
    steps.add_argument("--reconstitute", action="store_true")
    steps.add_argument("--text-jury", action="store_true")
    steps.add_argument("--video-jury", action="store_true")
    steps.add_argument("--narrative-fidelity", action="store_true")

    # Text generation / reconstitution options
    gen = parser.add_argument_group("Text generation & reconstitution (steps 1 & 2)")
    gen.add_argument("--model", default="gpt-4o",
                     help="Model for text generation and reconstitution (default: gpt-4o)")
    gen.add_argument("--force", action="store_true", help="Overwrite existing files")
    gen.add_argument("--dry-run", action="store_true", help="Build prompts only, no API calls")
    gen.add_argument("--groups", type=int, nargs="+", metavar="N",
                     help="Only process these GEST groups (1-5) for generate-texts")

    # Jury options
    jury = parser.add_argument_group("Jury (steps 3 & 4)")
    jury.add_argument("--experiment", default="all", choices=EXPERIMENT_CHOICES,
                      help="Text jury experiment mode (default: all)")
    jury.add_argument("--judges", nargs="+", default=JUDGES, choices=ALL_JUDGE_CHOICES)
    jury.add_argument("--n-matches", type=int, default=5,
                      help="Procedural matches per agentic GEST group for text jury (default: 5)")
    jury.add_argument("--n-procedural", type=int, default=5,
                      help="Procedural matches for video jury (default: 5)")
    jury.add_argument("--max-frames", type=int, default=20,
                      help="Max video frames per story for video jury (default: 20)")
    jury.add_argument("--seed", type=int, default=42)
    jury.add_argument("--resume", action="store_true",
                      help="Resume from existing output, skip completed entries")

    # Output
    parser.add_argument("--output", type=Path, default=RESULTS_DIR)

    args = parser.parse_args()

    if not any([args.all, args.generate_texts, args.reconstitute, args.text_jury, args.video_jury, args.narrative_fidelity]):
        parser.print_help()
        return

    args.output.mkdir(parents=True, exist_ok=True)

    if args.all or args.generate_texts:
        step_generate_texts(args)

    if args.all or args.reconstitute:
        step_reconstitute(args)

    if args.all or args.text_jury:
        step_text_jury(args)

    if args.all or args.video_jury:
        step_video_jury(args)

    if args.all or args.narrative_fidelity:
        step_narrative_fidelity(args)

    print(f"\nAll done. Results in: {args.output}")


if __name__ == "__main__":
    main()
