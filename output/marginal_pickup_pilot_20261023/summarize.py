"""Summarize the saved paired experiment; never starts a solver."""
import csv
import json
import os
from pathlib import Path
import tempfile

out = Path(__file__).resolve().parent
exp = out / 'experiment'
rows = json.loads((exp/'results.json').read_text())
assert len(rows)==2 and all(r['case']=='L07' and r['seed']==42 for r in rows)
assert all(r['valid'] and r['periodic_valid'] and r['worker_returncode']==0 for r in rows)
by = {r['strategy']:r for r in rows}
old,new = [by[s] for s in ('cost_aware','cost_aware_marginal')]
chains = new['stock_chain']['stock_chain_improvements']
new_kind = lambda step: step.get('kind')=='e1_marginal_pickup'
summary = dict(case='L07',seed=42,seconds=60,
    baseline_cost=old['objective'],candidate_cost=new['objective'],
    cost_saving=old['objective']-new['objective'],
    percent_saving=100*(old['objective']-new['objective'])/old['objective'],
    cost_delta_candidate_minus_baseline={k:new['costs'][k]-old['costs'][k] for k in old['costs']},
    candidate_chains_using_new_operator=sum(any(new_kind(s) for s in c['best_path']) for c in chains),
    candidate_steps_using_new_operator=sum(sum(new_kind(s) for s in c['best_path']) for c in chains),
    accepted_chains=chains,
    repairs={s:dict(calls=r['stock_chain']['stock_chain_calls'],
                   evaluations=r['stock_chain']['stock_chain_evaluations'],
                   feasible=r['stock_chain']['stock_chain_feasible_evaluations'],
                   seconds=r['stock_chain']['stock_chain_seconds'],
                   budget_percent=100*r['stock_chain']['stock_chain_seconds']/r['seconds'],
                   accepted=len(r['stock_chain']['stock_chain_improvements'])) for s,r in by.items()})
(out/'analysis.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n')
fields=['case','strategy','seed','seconds','objective','pool','pool_lower_bound','distance_e1',
        'distance_e2','wall_seconds','cpu_seconds','iterations','evaluations','valid','periodic_valid']
with (out/'comparison.csv').open('w',newline='') as f:
    writer=csv.DictWriter(f,fieldnames=fields,extrasaction='ignore');writer.writeheader();writer.writerows(rows)
cache=tempfile.mkdtemp(prefix='m5_marginal_plot_')
os.environ['MPLCONFIGDIR']=cache
os.environ.setdefault('XDG_CACHE_HOME',cache)
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
plt.rcParams.update({'font.size':10,'axes.spines.top':False,'axes.spines.right':False})
fig,ax=plt.subplots(figsize=(8,4.2),constrained_layout=True)
for strategy,label,color in [('cost_aware','Current cost-aware','#b65b3d'),
                              ('cost_aware_marginal','With marginal pickup moves','#146b91')]:
    result=json.loads((exp/'quality'/'L07'/strategy/'result.json').read_text())
    history=result['info']['incumbent_history']
    x=[r['time'] for r in history];y=[r['objective'] for r in history]
    if x[-1]<60:x.append(60);y.append(y[-1])
    ax.step(x,y,where='post',lw=2,label=label,color=color)
ax.set(title='M5 / L07 | 100 customers | seed 42 | one 60 s run per strategy',
       xlabel='Elapsed seconds',ylabel='Best feasible total cost',xlim=(0,60))
ax.grid(alpha=.18);ax.legend()
fig.savefig(out/'convergence.png',dpi=180);fig.savefig(out/'convergence.svg')
print(json.dumps({k:v for k,v in summary.items() if k!='accepted_chains'},indent=2))
