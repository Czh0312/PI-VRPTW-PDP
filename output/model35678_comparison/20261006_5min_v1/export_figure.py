"""Verify the saved long-budget comparison and plot all six cases."""
from pathlib import Path
import json
import math

OUT=Path(__file__).resolve().parent


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def main():
    plan=read(OUT/'experiment.json')
    summary=read(OUT/'summary.json')
    rows=read(OUT/'rows.json')
    assert len(rows)==len(plan['jobs'])==30
    metrics=[]
    for row in rows:
        item={key:row.get(key) for key in ('id','family','model','seed','status','valid','cost','k','pool','iterations_completed','solve_wall_seconds')}
        result_path=OUT/'results'/row['id']/'result.json'
        if result_path.exists():
            result=read(result_path)
            item.update(count_search=result.get('count_search'),
                        cutoff_ratios=result.get('cutoff_ratios'),
                        batch_durations_ticks=result.get('batch_durations_ticks'))
        if row['valid']:
            audit=read(result_path.parent/'independent_audit.json')
            cost=read(result_path.parent/'comparison_cost.json')
            assert audit['valid'] and audit['periodic_replay']['valid']
            assert cost['economic_profile_hash']==plan['economic_profile_hash']
            assert math.isclose(sum(cost['components'].values()),row['cost'],abs_tol=1e-6)
        metrics.append(item)
    (OUT/'search_metrics.json').write_text(json.dumps(metrics,ensure_ascii=False,indent=2,allow_nan=False)+'\n',encoding='utf-8')
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    import numpy as np
    plt.rcParams.update({'font.family':'DejaVu Sans','font.size':10,'axes.spines.top':False,'axes.spines.right':False})
    groups=[('Container pool','#3b6ea8',['container_pool']),('Distance','#e99d3c',['e1_distance','e2_distance']),
            ('Occupied time','#43967c',['e1_work','e2_work']),('Vehicles','#8d76b9',['e1_vehicles','e2_vehicles']),
            ('Trips','#b98268',['e1_trips','e2_trips']),('Cleaning / tracking / wear','#a0a7b0',['cleaning','tracking','container_wear'])]
    fig,axes=plt.subplots(2,3,figsize=(16,10))
    for ax,index in zip(axes.flat,[0,2,4,1,3,5]):
        case=summary['cases'][index]
        selected=case['selected']
        x=np.arange(5); bottoms=np.zeros(5)
        for label,color,keys in groups:
            values=[sum(selected[str(model)]['cost_components'][key] for key in keys) if selected[str(model)] else 0 for model in (3,5,6,7,8)]
            ax.bar(x,values,bottom=bottoms,color=color,label=label,width=.65)
            bottoms+=np.array(values)
        for position,total in enumerate(bottoms):
            ax.annotate(f'{total:,.1f}' if total else 'No solution',(position,total),
                xytext=(0,5),textcoords='offset points',ha='center',fontsize=9)
        title=case['family'].replace('zhou_','').replace('_subset50','')
        ax.set_title(f'{title}\n{case["customers"]} customers; Q={case["demand"]}')
        ax.set_xticks(x,['M3','M5','M6','M7','M8'])
        ax.set_ylabel('Synthetic cost units per cycle')
        ax.set_ylim(0,max(bottoms)*1.18 if max(bottoms)>0 else 1)
        ax.grid(axis='y',alpha=.18)
        ax.set_axisbelow(True)
    handles,labels=axes[0,0].get_legend_handles_labels()
    fig.legend(handles,labels,loc='lower center',bbox_to_anchor=(.5,.04),ncol=3,frameon=False)
    fig.suptitle('M3 / M5 / M6 / M7 / M8: six large cases, 300 seconds, seed 42',fontsize=16)
    fig.text(.5,.012,'M3/M5 optimize distance; M6-M8 optimize cost. All use one economic profile. Feasible results, no optimality proof.',ha='center',fontsize=10,color='#555555')
    fig.tight_layout(rect=(0,.14,1,.95))
    fig.savefig(OUT/'cost_components.png',dpi=180)
    fig.savefig(OUT/'cost_components.svg')
    plt.close(fig)
    report=OUT/'report.md'
    text=report.read_text(encoding='utf-8')
    if '![六个大规模算例的费用组成]' not in text:
        report.write_text(text+'\n## 六例费用组成\n\n![六个大规模算例的费用组成](cost_components.png)\n',encoding='utf-8')
    print('VERIFIED',summary['valid'],'/',summary['completed'],'valid; figure exported')


if __name__=='__main__':
    main()
