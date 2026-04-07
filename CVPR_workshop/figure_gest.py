"""Generate a small illustrative GEST graph for the qualitative figure."""
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import numpy as np

# Subset of events from Worker 1, Group 1
# Two actor timelines (Marcus=a0, Sarah=a1) with key events

events_a0 = [
    ("a0_1", "TakeOut\nphone"),
    ("a0_3", "TalkPhone"),
    ("a0_11", "Talk\n(both)"),
    ("a0_13", "PickUp\ndrink"),
    ("a0_22", "SitDown"),
    ("a0_30", "Smoke"),
    ("a0_42", "Hug\n(both)"),
    ("a0_48", "Laugh\n(both)"),
]

events_a1 = [
    ("a1_6", "TakeOut\nphone"),
    ("a1_8", "TalkPhone"),
    ("a1_12", "Talk\n(both)"),
    ("a1_15", "PickUp\ndrink"),
    ("a1_23", "SitDown"),
    ("a1_34", "TalkPhone"),
    ("a1_43", "Hug\n(both)"),
    ("a1_49", "Laugh\n(both)"),
]

# Scene boundaries
scene1_end = 3  # after PickUp drink (index 3)

fig, ax = plt.subplots(1, 1, figsize=(10, 3.2))

y_a0 = 1.8
y_a1 = 0.6
x_positions = np.linspace(0.8, 9.2, 8)
node_w = 0.85
node_h = 0.55

colors_a0 = '#4A90D9'
colors_a1 = '#D94A4A'

# Scene background
ax.axvspan(x_positions[0] - 0.55, x_positions[scene1_end] + 0.55,
           alpha=0.08, color='#888888', zorder=0)
ax.axvspan(x_positions[scene1_end + 1] - 0.55, x_positions[-1] + 0.55,
           alpha=0.08, color='#888888', zorder=0)
ax.text((x_positions[0] + x_positions[scene1_end]) / 2, 2.65, 'Scene 1',
        ha='center', fontsize=9, fontstyle='italic', color='#555555')
ax.text((x_positions[scene1_end + 1] + x_positions[-1]) / 2, 2.65, 'Scene 2',
        ha='center', fontsize=9, fontstyle='italic', color='#555555')

# Draw events
for i, (eid, label) in enumerate(events_a0):
    x = x_positions[i]
    rect = mpatches.FancyBboxPatch((x - node_w/2, y_a0 - node_h/2), node_w, node_h,
                                    boxstyle="round,pad=0.08",
                                    facecolor=colors_a0, edgecolor='#2A5A8A',
                                    alpha=0.85, linewidth=1.2, zorder=2)
    ax.add_patch(rect)
    ax.text(x, y_a0, label, ha='center', va='center', fontsize=6.5,
            color='white', fontweight='bold', zorder=3)
    # Arrow to next
    if i < len(events_a0) - 1:
        ax.annotate('', xy=(x_positions[i+1] - node_w/2 - 0.02, y_a0),
                    xytext=(x + node_w/2 + 0.02, y_a0),
                    arrowprops=dict(arrowstyle='->', color=colors_a0, lw=1.2),
                    zorder=1)

for i, (eid, label) in enumerate(events_a1):
    x = x_positions[i]
    rect = mpatches.FancyBboxPatch((x - node_w/2, y_a1 - node_h/2), node_w, node_h,
                                    boxstyle="round,pad=0.08",
                                    facecolor=colors_a1, edgecolor='#8A2A2A',
                                    alpha=0.85, linewidth=1.2, zorder=2)
    ax.add_patch(rect)
    ax.text(x, y_a1, label, ha='center', va='center', fontsize=6.5,
            color='white', fontweight='bold', zorder=3)
    if i < len(events_a1) - 1:
        ax.annotate('', xy=(x_positions[i+1] - node_w/2 - 0.02, y_a1),
                    xytext=(x + node_w/2 + 0.02, y_a1),
                    arrowprops=dict(arrowstyle='->', color=colors_a1, lw=1.2),
                    zorder=1)

# Synchronization arrows (starts_with relations)
sync_pairs = [(2, 2), (6, 6), (7, 7)]  # Talk, Hug, Laugh happen together
for i_a0, i_a1 in sync_pairs:
    ax.annotate('', xy=(x_positions[i_a1], y_a1 + node_h/2 + 0.02),
                xytext=(x_positions[i_a0], y_a0 - node_h/2 - 0.02),
                arrowprops=dict(arrowstyle='<->', color='#666666', lw=1,
                               linestyle='dashed'),
                zorder=1)

# Location change arrow for a0 (livingroom -> kitchen -> livingroom)
ax.annotate('kitchen', xy=(x_positions[3], y_a0 + node_h/2 + 0.12),
            ha='center', fontsize=6, color='#777777', fontstyle='italic')
ax.annotate('livingroom', xy=(x_positions[0], y_a0 + node_h/2 + 0.12),
            ha='center', fontsize=6, color='#777777', fontstyle='italic')

# Actor labels
ax.text(0.25, y_a0, 'Marcus\n(a0)', ha='center', va='center', fontsize=8,
        fontweight='bold', color='black')
ax.text(0.25, y_a1, 'Sarah\n(a1)', ha='center', va='center', fontsize=8,
        fontweight='bold', color='black')

ax.set_xlim(-0.2, 10.0)
ax.set_ylim(-0.1, 2.9)
ax.set_aspect('equal')
ax.axis('off')

plt.tight_layout()
out = 'c:/nick/PhD/repos/multiagent_story_system/CVPR_workshop/figure_frames/gest_subgraph.pdf'
plt.savefig(out, bbox_inches='tight', dpi=300)
plt.savefig(out.replace('.pdf', '.png'), bbox_inches='tight', dpi=300)
print(f'Saved {out}')
