# Historical M8 experiments: retain their original two-stage semantics.
"""Plot the verified designed mechanism example; no benchmark claims."""
import argparse
import json
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

from experiments.scenarios.model8_capacity import make_capacity_example
from models.routing.common.task_builder import build_tasks_for_model
from models.routing.variants.model_8.two_stage_v2.state import Evaluator


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', default='output/experiments/model8_capacity_20260922')
    args = parser.parse_args()
    folder = Path(args.input)
    base = json.loads((folder / 'mechanism_tight/base.json').read_text())
    solved = json.loads((folder / 'mechanism_tight/exact_relay_42.json').read_text())['info']
    p = make_capacity_example()
    a, b = build_tasks_for_model(p, 8)
    e = Evaluator(p, a, b, solved['baseline'])
    state = solved['state']
    fig, axes = plt.subplots(1, 3, figsize=(14, 4.4), gridspec_kw={'width_ratios': [1, 1, 1.4]})
    colors = ['#2563eb', '#ea580c']
    for ax, paths, title in zip(axes[:2], [base['e2_routes']['S0'], state['e2_routes']['S0']],
                               ['Without transfer', 'With capacity cooperation']):
        for k, r in enumerate(paths):
            xy = [(p.nodes[e.phys(n)].x, p.nodes[e.phys(n)].y) for n in r]
            ax.plot([x for x, y in xy], [y for x, y in xy], color=colors[k],
                    linewidth=2.0, alpha=.85, label=f'Vehicle {"AB"[k]}')
        for c in p.customers:
            ax.scatter(c.x, c.y, color='#334155', s=35, zorder=5)
            ax.annotate(c.id, (c.x, c.y), xytext=(4, 5), textcoords='offset points', fontsize=11)
        ax.scatter(0, 0, marker='s', color='#0f172a', s=70, zorder=5)
        ax.annotate('S', (0, 0), xytext=(4, 5), textcoords='offset points', fontsize=11)
        ax.set_title(title, fontsize=12, fontweight='bold')
        ax.set_xlim(-13, 25)
        ax.set_ylim(-14, 15)
        ax.set_aspect('equal')
        ax.grid(alpha=.15)
        ax.legend(loc='upper left', fontsize=9)
        ax.set_xlabel('Coordinate distance')
    axes[1].scatter(10, 0, marker='*', color='#16a34a', edgecolor='white', s=230, zorder=6)
    quantity = state['transfers'][0]['quantity']
    axes[1].annotate(f'A hands {quantity} box(es) to B', (10, 0), xytext=(1, -9), fontsize=9,
                     arrowprops={'arrowstyle': '->', 'color': '#16a34a'})
    sender = state['transfers'][0]['sender_vehicle']
    route = state['e2_routes']['S0'][sender]
    sent, received = e.transfer_maps(state)
    with_transfer, _, _ = e.profile(route, 'S0', sender, sent, received)
    without_transfer, _, _ = e.profile(route, 'S0', sender, {}, {})
    labels = ['Start'] + [n.replace('MEET@', '') for n in route[1:-1]]
    ax = axes[2]
    x = list(range(len(labels)))
    ax.step(x, [r['volume'] for r in without_transfer], where='post', linestyle='--',
            color='#dc2626', label='Same pickups, no transfer (infeasible)')
    ax.step(x, [r['volume'] for r in with_transfer], where='post', linewidth=2.5,
            color='#2563eb', label='With transfer')
    ax.axhline(3, color='#64748b', linestyle=':', label='Volume capacity = 3')
    ax.set_xticks(x, labels, rotation=45, ha='right')
    ax.set_ylim(0, 4.8)
    ax.set_ylabel('Goods + empty-container volume')
    ax.set_title("Vehicle A: capacity released at H", fontsize=12, fontweight='bold')
    ax.grid(alpha=.15)
    ax.legend(fontsize=8, loc='lower left')
    fig.suptitle('Designed mechanism example: 130.929 -> 116.787  |  Distance saving 10.80%',
                 fontsize=14, fontweight='bold', y=1.02)
    fig.tight_layout()
    fig.savefig(folder / 'capacity_mechanism.png', dpi=160, bbox_inches='tight')
    fig.savefig(folder / 'capacity_mechanism.svg', bbox_inches='tight')
    plt.close(fig)
    print(folder / 'capacity_mechanism.png')


if __name__ == '__main__':
    main()
