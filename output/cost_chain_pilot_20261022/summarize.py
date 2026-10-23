"""Summarize saved experiment results only; never rerun or select solver runs."""
from pathlib import Path
import csv
import json
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

out = Path(__file__).resolve().parent
exp = out / 'experiment'
rows = json.loads((exp / 'results.json').read_text())
old, new = [r for r in rows if r['kind'] == 'quality']
anchor = json.loads((exp / 'inputs/anchor_result.json').read_text())
summary = dict(
    cost_reduction=old['objective'] - new['objective'],
    cost_reduction_percent=(old['objective'] - new['objective']) / old['objective'] * 100,
    historical_reduction=anchor['info']['objective'] - new['objective'],
    historical_reduction_percent=(anchor['info']['objective'] - new['objective']) / anchor['info']['objective'] * 100,
    cost_component_delta_new_minus_old={k: new['costs'][k] - old['costs'][k] for k in new['costs']},
    cost_component_delta_new_minus_historical={k: new['costs'][k] - anchor['cost_components'][k] for k in new['costs']},
    stock_chain_budget_percent=new['stock_chain']['stock_chain_seconds'] / new['seconds'] * 100,
    limitation='One case and one seed; historical reference is not a new contemporaneous no-repair control.')
(out / 'analysis.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2) + '\n')
fields = ['kind', 'variant', 'case', 'seed', 'seconds', 'objective', 'pool', 'wall_seconds',
          'cpu_seconds', 'iterations', 'evaluations', 'distance_e1', 'distance_e2', 'valid', 'periodic_valid']
with (out / 'comparison.csv').open('w', newline='') as f:
    writer = csv.DictWriter(f, fieldnames=fields, extrasaction='ignore')
    writer.writeheader()
    for row in rows:
        if row['kind'] == 'mechanism':
            row = dict(row, objective=row['final_objective'], pool=row['final_pool'])
        writer.writerow(row)

plt.rcParams.update({'font.size': 10, 'axes.spines.top': False, 'axes.spines.right': False})
fig, axes = plt.subplots(1, 2, figsize=(11, 4.2), constrained_layout=True)
colors = {'baseline': '#b65b3d', 'candidate': '#146b91'}
labels = {'baseline': 'Stock-first repair', 'candidate': 'Cost-aware repair'}
for variant in ('baseline', 'candidate'):
    result = json.loads((exp / 'quality' / variant / 'result.json').read_text())
    history = result['info']['incumbent_history']
    for ax, key, end in zip(axes, ('time', 'iteration'), (30, 3000)):
        x = [v[key] for v in history]
        y = [v['objective'] for v in history]
        if x[-1] < end:
            x.append(end); y.append(y[-1])
        ax.step(x, y, where='post', color=colors[variant], lw=2, label=labels[variant])
for ax, xlabel, end in zip(axes, ('Elapsed seconds', 'Iteration (first 3,000)'), (30, 3000)):
    ax.axhline(anchor['info']['objective'], color='#555555', ls='--', lw=1,
               label='Historical reference: 138.6907')
    ax.set(xlim=(0, end), ylim=(135, 192), xlabel=xlabel, ylabel='Best feasible total cost')
    ax.grid(alpha=.18)
axes[0].legend(fontsize=8, loc='upper right')
fig.suptitle('M02 | seed 42 | 30 s per strategy | one run each', fontsize=12)
fig.savefig(out / 'convergence.png', dpi=180)
fig.savefig(out / 'convergence.svg')
print(json.dumps(summary, ensure_ascii=False, indent=2))
