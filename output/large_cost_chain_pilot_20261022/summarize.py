"""Derive tables/plots from the four saved large-case runs; no solver calls."""
from pathlib import Path
import csv
import json
import os
import tempfile

out = Path(__file__).resolve().parent
exp = out / 'experiment'
rows = json.loads((exp / 'results.json').read_text())
expected = {(c, s) for c in ('L06', 'L07') for s in ('stock_first', 'cost_aware')}
assert {(r['case'], r['strategy']) for r in rows} == expected
assert all(r['valid'] and r['periodic_valid'] and r['worker_returncode'] == 0 for r in rows)
by_key = {(r['case'], r['strategy']): r for r in rows}
comparisons = []
for case in ('L06', 'L07'):
    old, new = [by_key[case, s] for s in ('stock_first', 'cost_aware')]
    comparisons.append(dict(case=case, stock_first_cost=old['objective'], cost_aware_cost=new['objective'],
        cost_saving=old['objective']-new['objective'],
        percent_saving=100*(old['objective']-new['objective'])/old['objective'],
        cost_delta_new_minus_old={k: new['costs'][k]-old['costs'][k] for k in new['costs']},
        stock_first_pool=old['pool'], cost_aware_pool=new['pool'], lower_bound=new['pool_lower_bound'],
        stock_first_repair_budget_percent=100*old['stock_chain']['stock_chain_seconds']/old['seconds'],
        cost_aware_repair_budget_percent=100*new['stock_chain']['stock_chain_seconds']/new['seconds'],
        stock_first_accepted_chains=len(old['stock_chain']['stock_chain_improvements']),
        cost_aware_accepted_chains=len(new['stock_chain']['stock_chain_improvements'])))
(out / 'analysis.json').write_text(json.dumps(comparisons, ensure_ascii=False, indent=2)+'\n')
fields = ['case','strategy','customers','satellites','batches','seed','seconds','objective','pool',
          'pool_lower_bound','distance_e1','distance_e2','wall_seconds','cpu_seconds',
          'first_feasible_seconds','iterations','evaluations','valid','periodic_valid']
with (out / 'comparison.csv').open('w', newline='') as f:
    writer = csv.DictWriter(f, fieldnames=fields, extrasaction='ignore')
    writer.writeheader()
    for case in ('L06','L07'):
        for strategy in ('stock_first','cost_aware'):
            writer.writerow(by_key[case,strategy])

plot_cache = tempfile.mkdtemp(prefix='m5_large_plot_')
os.environ['MPLCONFIGDIR'] = plot_cache
os.environ.setdefault('XDG_CACHE_HOME', plot_cache)
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
plt.rcParams.update({'font.size':10,'axes.spines.top':False,'axes.spines.right':False})
fig,axes=plt.subplots(1,2,figsize=(11,4.2),constrained_layout=True)
colors={'stock_first':'#b65b3d','cost_aware':'#146b91'}
labels={'stock_first':'Stock-first repair','cost_aware':'Cost-aware repair'}
for ax,case in zip(axes,('L06','L07')):
    for strategy in ('stock_first','cost_aware'):
        result=json.loads((exp/'quality'/case/strategy/'result.json').read_text())
        history=result['info']['incumbent_history']
        x=[r['time'] for r in history]; y=[r['objective'] for r in history]
        if x[-1] < 60:
            x.append(60);y.append(y[-1])
        ax.step(x,y,where='post',color=colors[strategy],lw=2,label=labels[strategy])
    ax.set(title=case+' | 100 customers',xlabel='Elapsed seconds',ylabel='Best feasible total cost',xlim=(0,60))
    ax.grid(alpha=.18)
    ax.legend(fontsize=9)
fig.suptitle('M5 large-case pilot | seed 42 | 60 s per strategy | one run each',fontsize=12)
fig.savefig(out/'convergence.png',dpi=180)
fig.savefig(out/'convergence.svg')
print(json.dumps(comparisons,ensure_ascii=False,indent=2))
