"""Generate stage2 seed text files and seed_file.json."""
import sys, random, json
from pathlib import Path
from collections import defaultdict

sys.path.insert(0, str(Path(__file__).parent.parent / "llm-agent-evaluation"))
from catalog import load_procedural_stories

random.seed(42)
stories = [s for s in load_procedural_stories() if s.description]

def get_location(name):
    return name.split('_max')[0]

combos = defaultdict(list)
for s in stories:
    loc = get_location(s.story_dir.name)
    combos[(loc, s.actor_count)].append(s)

locations = ['classroom', 'gym', 'garden_house', 'house_garden', 'house']
selection = []
for loc in locations:
    available_counts = sorted(set(n for (l, n) in combos if l == loc))
    picks_by_count = defaultdict(int)
    for i in range(5):
        c = available_counts[i % len(available_counts)]
        picks_by_count[c] += 1
    loc_picks = []
    for count, n in picks_by_count.items():
        candidates = list(combos[(loc, count)])
        random.shuffle(candidates)
        loc_picks.extend(candidates[:n])
    selection.extend(loc_picks)

out_dir = Path(__file__).parent
seed_paths = []
for i, s in enumerate(selection, 1):
    loc = get_location(s.story_dir.name)
    fname = f'{i:02d}_{loc}_a{s.actor_count}_{s.story_dir.name[-8:]}.txt'
    fpath = out_dir / fname
    fpath.write_text(s.description, encoding='utf-8')
    win_path = str(fpath.resolve()).replace('/', chr(92))
    seed_paths.append(win_path)
    print(f'{i:2d}. {fname}  ({len(s.description)} chars)')

seed_json = out_dir / 'seed_file.json'
seed_json.write_text(json.dumps(seed_paths, indent=2, ensure_ascii=False), encoding='utf-8')
print(f'\nWrote {len(seed_paths)} seeds -> {seed_json}')
