"""
Story catalog — discovers agentic and procedural stories from Google Drive.

Agentic stories: G:/My Drive/.../llm_agentic_stories/worker{N}/batch_*/story_detail_gest/
Procedural stories: G:/My Drive/.../segmentations_balanced_{1,2,3}/*/

Each story is grouped by its unique GEST (agentic stories share GESTs across 5 workers).
"""

import json
import glob
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

GDRIVE_BASE = Path("G:/My Drive/Archive - PhD/sa_video_story_engine")
AGENTIC_BASE = GDRIVE_BASE / "llm_agentic_stories"
PROCEDURAL_BASES = [
    GDRIVE_BASE / "segmentations_balanced_1",
    GDRIVE_BASE / "segmentations_balanced_2",
    GDRIVE_BASE / "segmentations_balanced_3",
]


@dataclass
class Story:
    story_dir: Path
    story_type: str                    # "agentic" | "procedural"
    proto_graph_path: Path
    video_path: Path
    event_frame_mapping_path: Path
    texts_json_path: Path              # may not exist for agentic stories
    actor_count: int
    action_count: int
    gest_group: Optional[int] = None  # 1–5 for agentic (shared GEST group)
    worker_id: Optional[int] = None   # 1–25 for agentic

    @property
    def has_texts(self) -> bool:
        return self.texts_json_path.exists()

    @property
    def description(self) -> Optional[str]:
        """Return best available GPT description from texts.json."""
        if not self.has_texts:
            return None
        data = json.loads(self.texts_json_path.read_text(encoding="utf-8"))
        for key in ["gpt-5_withGEST_t-1.0", "gpt-4o_withGEST_t-1.0"]:
            if key in data:
                return data[key]
        for key, value in data.items():
            if not key.startswith("query"):
                return value
        return None

    @property
    def prompt(self) -> Optional[str]:
        """Return the withGEST prompt from texts.json."""
        if not self.has_texts:
            return None
        data = json.loads(self.texts_json_path.read_text(encoding="utf-8"))
        return data.get("query_withGEST")

    @property
    def root_narrative(self) -> Optional[str]:
        """Return the LLM-authored root narrative (agentic stories only)."""
        gest_file = self.story_dir / "detailed_graph" / "take1" / "detail_gest.json"
        if not gest_file.exists():
            return None
        data = json.loads(gest_file.read_text(encoding="utf-8"))
        return data.get("story_root", {}).get("Properties", {}).get("narrative")

    @property
    def reconstituted_narrative_path(self) -> Path:
        """Path where the reconstituted narrative is saved (procedural stories)."""
        return self.story_dir / "reconstituted_narrative.json"

    @property
    def reconstituted_narrative(self) -> Optional[str]:
        """Return the GPT-reconstituted root narrative (procedural stories only)."""
        p = self.reconstituted_narrative_path
        if not p.exists():
            return None
        data = json.loads(p.read_text(encoding="utf-8"))
        return data.get("narrative")


def _count_actors_actions(gest_path: Path):
    try:
        data = json.loads(gest_path.read_text(encoding="utf-8"))
        actors = sum(
            1 for v in data.values()
            if isinstance(v, dict)
            and v.get("Action") == "Exists"
            and "Name" in v.get("Properties", {})
        )
        actions = sum(
            1 for k, v in data.items()
            if isinstance(v, dict)
            and v.get("Action") not in ("Exists", None)
            and "_" in k
        )
        return actors, actions
    except Exception:
        return 0, 0


def load_agentic_stories() -> List[Story]:
    """Load all 25 agentic stories (5 unique GESTs × 5 workers each)."""
    stories = []

    # Collect all workers, sorted numerically
    worker_dirs = sorted(
        [d for d in AGENTIC_BASE.iterdir() if d.name.startswith("worker")],
        key=lambda d: int(d.name.replace("worker", ""))
    )

    for worker_dir in worker_dirs:
        worker_id = int(worker_dir.name.replace("worker", ""))

        # Find batch folder
        batch_dirs = [d for d in worker_dir.iterdir()
                      if d.is_dir() and d.name.startswith("batch_")]
        if not batch_dirs:
            continue
        batch_dir = batch_dirs[0]

        story_dir = batch_dir / "story_detail_gest"
        proto_graph = story_dir / "detailed_graph" / "take1" / "proto-graph.json"
        video = story_dir / "simulations" / "take1_sim1" / "camera1" / "raw.mp4"
        efm = story_dir / "simulations" / "take1_sim1" / "event_frame_mapping.json"
        texts = story_dir / "texts.json"
        gest_file = story_dir / "detailed_graph" / "take1" / "detail_gest.json"

        if not proto_graph.exists() or not video.exists():
            continue

        actors, actions = _count_actors_actions(gest_file) if gest_file.exists() else (0, 0)

        # Group assignment: workers 1-5 → group 1, 6-10 → group 2, etc.
        gest_group = (worker_id - 1) // 5 + 1

        stories.append(Story(
            story_dir=story_dir,
            story_type="agentic",
            proto_graph_path=proto_graph,
            video_path=video,
            event_frame_mapping_path=efm,
            texts_json_path=texts,
            actor_count=actors,
            action_count=actions,
            gest_group=gest_group,
            worker_id=worker_id,
        ))

    return stories


def load_procedural_stories() -> List[Story]:
    """Load all procedural stories from segmentations_balanced_1/2/3."""
    stories = []

    for base in PROCEDURAL_BASES:
        if not base.exists():
            continue
        for story_dir in base.iterdir():
            if not story_dir.is_dir() or story_dir.name in ("desktop.ini",):
                continue
            # Skip non-story entries
            if story_dir.suffix in (".json", ".md", ".zip"):
                continue

            proto_graph = story_dir / "detailed_graph" / "take1" / "proto-graph.json"
            video = story_dir / "simulations" / "take1_sim1" / "camera1" / "raw.mp4"
            efm = story_dir / "simulations" / "take1_sim1" / "event_frame_mapping.json"
            texts = story_dir / "texts.json"
            gest_file = story_dir / "detailed_graph" / "take1" / "detail_gest.json"

            if not proto_graph.exists() or not video.exists():
                continue

            actors, actions = _count_actors_actions(gest_file) if gest_file.exists() else (0, 0)

            stories.append(Story(
                story_dir=story_dir,
                story_type="procedural",
                proto_graph_path=proto_graph,
                video_path=video,
                event_frame_mapping_path=efm,
                texts_json_path=texts,
                actor_count=actors,
                action_count=actions,
            ))

    return stories


def select_procedural_matches(
    agentic_stories: List[Story],
    procedural_stories: List[Story],
    n_per_group: int = 5,
) -> List[Story]:
    """
    Select procedural stories matched to agentic GEST groups by actor count.

    For each unique agentic GEST group, picks n_per_group procedural stories
    with the same actor count (or closest available). Returns a deduplicated list.
    """
    import random
    random.seed(42)

    # Determine target actor count per group
    group_actor_count: dict = {}
    for s in agentic_stories:
        if s.gest_group not in group_actor_count:
            group_actor_count[s.gest_group] = s.actor_count

    selected_paths = set()
    selected = []

    for group, target_actors in sorted(group_actor_count.items()):
        # Find procedural stories with matching actor count
        candidates = [
            s for s in procedural_stories
            if s.actor_count == target_actors and str(s.story_dir) not in selected_paths
        ]
        # If not enough exact matches, relax to ±1
        if len(candidates) < n_per_group:
            candidates = [
                s for s in procedural_stories
                if abs(s.actor_count - target_actors) <= 1
                and str(s.story_dir) not in selected_paths
            ]

        picks = random.sample(candidates, min(n_per_group, len(candidates)))
        for p in picks:
            selected_paths.add(str(p.story_dir))
            selected.append(p)

    return selected


if __name__ == "__main__":
    agentic = load_agentic_stories()
    procedural = load_procedural_stories()
    matches = select_procedural_matches(agentic, procedural, n_per_group=5)

    print(f"Agentic stories: {len(agentic)}")
    for g in sorted(set(s.gest_group for s in agentic)):
        group = [s for s in agentic if s.gest_group == g]
        sample = group[0]
        print(f"  Group {g}: {len(group)} sims, {sample.actor_count} actors, "
              f"{sample.action_count} actions, has_texts={sample.has_texts}")

    print(f"\nProcedural stories available: {len(procedural)}")
    print(f"Procedural matches selected: {len(matches)}")
    for s in matches:
        print(f"  {s.story_dir.name}: {s.actor_count} actors, has_texts={s.has_texts}")
