"""Check saved large-scale runs and export a cost-component figure."""
from pathlib import Path
import csv
import json
import math
import statistics

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[2]


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def main():
    plan = read(OUT/'experiment.json')
    summary = read(OUT/'summary.json')
    rows = read(OUT/'rows.json')
    prior = read(ROOT/'output/model5678_comparison/20261006_same_cost_v1/experiment.json')
    assert plan['economic_profile_hash'] == prior['economic_profile_hash']
    assert len(rows) == len(plan['jobs'])
    metrics = []
    for row in rows:
        item = {key: row.get(key) for key in ('id', 'family', 'model', 'seed', 'budget', 'status', 'valid', 'cost',
                'distance', 'pool', 'minimum_pool', 'k', 'delivery_batches', 'solve_wall_seconds')}
        result_path = OUT/'results'/row['id']/'result.json'
        if result_path.exists():
            result = read(result_path)
            info = result['info']
            item.update(iterations_completed=info.get('iterations'), evaluations=info.get('evaluations'),
                        k_search=result.get('count_search'), cutoff_ratios=result.get('cutoff_ratios'),
                        batch_durations_ticks=result.get('batch_durations_ticks'))
        if row['valid']:
            target = result_path.parent
            audit = read(target/'independent_audit.json')
            cost = read(target/'comparison_cost.json')
            assert audit['valid'] and audit['periodic_replay']['valid']
            assert cost['economic_profile_hash'] == plan['economic_profile_hash']
            assert math.isclose(sum(cost['components'].values()), row['cost'], abs_tol=1e-6)
        metrics.append(item)
    (OUT/'search_metrics.json').write_text(json.dumps(metrics, ensure_ascii=False, indent=2, allow_nan=False)+'\n', encoding='utf-8')
    text = (OUT/'report.md').read_text(encoding='utf-8')
    empty_exact = '## 最优证明\n\n| 算例 | 模型 | 状态 | 成本 | 求解器目标下界 | 已证最优 |\n|---|---|---|---:|---:|---|'
    text = text.replace(empty_exact, '## 最优证明\n\n本轮均为 ALNS 可行解，没有全局最优证明。')
    extra = ['\n## 与上一轮价格核对\n',
             '本轮与 7/10/20/30 点实验的经济参数哈希完全相同。',
             '\n| 参数 | 数值 |\n|---|---:|']
    profile=plan['economic_profile']
    for label,key in [('箱池每箱每周期','container_pool_cost_per_period'),('一级每距离单位','e1_distance_cost_per_km'),
            ('二级每距离单位','e2_distance_cost_per_km'),('一级每占用车辆分钟','e1_work_cost_per_minute'),
            ('二级每占用车辆分钟','e2_work_cost_per_minute'),('一级每启用车辆每周期','e1_vehicle_cost_per_cycle'),
            ('二级每启用车辆每周期','e2_vehicle_cost_per_cycle'),('一级每趟','e1_trip_cost'),('二级每趟','e2_trip_cost'),
            ('每次回箱清洗','cleaning_cost_per_return'),('每次用箱追踪','tracking_cost_per_use'),
            ('箱体价格（初始投入单列）','container_price'),('箱体寿命（次数）','container_lifetime_uses')]:
        extra.append(f'| {label} | {profile[key]} |')
    extra += ['\n## 实际求解用时\n', '| 算例 | 模型 | 有效/尝试 | 最短秒数 | 平均秒数 | 最长秒数 |', '|---|---|---:|---:|---:|---:|']
    for case in plan['cases']:
        for model in (5,6,7,8):
            group=[r for r in rows if r['family']==case['family'] and r['model']==model]
            times=[r['solve_wall_seconds'] for r in group if r.get('solve_wall_seconds') is not None]
            values=[min(times),statistics.mean(times),max(times)] if times else [None]*3
            strings=['—' if v is None else f'{v:.2f}' for v in values]
            extra.append(f'| {case["family"]} | M{model} | {sum(r["valid"] for r in group)}/{len(group)} | '+' | '.join(strings)+' |')
    extra += ['\n60 秒为算法的软截止，初始化、一次解码及审计可能超出；两个进程共享机器，实际耗时不用于声称算法速度优势。',
              '\n![代表方案费用组成](cost_components.png)\n']
    extra += ['\n## 本轮实际使用的决策范围\n']
    for case in summary['cases']:
        joint=[case['selected'][str(m)] for m in (6,7,8)]
        if all(row and row['k']==1 for row in joint):
            extra.append(f'- {case["customers"]} 点：M6/M7/M8 的代表方案均选择 K=1。单批次下共同分界、独立分界及可变长度的允许日历域相同，本轮三模型成本差异反映有限预算搜索得到的路线与时刻差异，不能归因于日历扩展本身。')
        else:
            counts=', '.join(f'M{m}: K={case["selected"][str(m)]["k"]}' if case['selected'][str(m)] else f'M{m}: 无可行解' for m in (6,7,8))
            extra.append(f'- {case["customers"]} 点代表方案：{counts}。')
        baseline=case['selected']['5']
        if baseline and all(row and row['pool']==baseline['pool'] for row in joint):
            extra.append(f'- {case["customers"]} 点：四模型代表方案箱池均为 {baseline["pool"]}，箱池收费相同；相对 M5 原配置成本的差异来自距离、占用工时、车辆和趟次费用。M5 不变时间表最低需箱量参考另列。')
    if '## 与上一轮价格核对' not in text:
        (OUT/'report.md').write_text(text+'\n'.join(extra)+'\n', encoding='utf-8')
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    import numpy as np
    plt.rcParams.update({'font.family':'DejaVu Sans','font.size':10,'axes.spines.top':False,'axes.spines.right':False})
    categories=[('Container pool','#3b6ea8',['container_pool']),('Distance','#e99d3c',['e1_distance','e2_distance']),
                ('Occupied time','#43967c',['e1_work','e2_work']),('Vehicles','#8d76b9',['e1_vehicles','e2_vehicles']),
                ('Trips','#b98268',['e1_trips','e2_trips']),('Cleaning / tracking / wear','#a0a7b0',['cleaning','tracking','container_wear'])]
    fig,axes=plt.subplots(1,len(summary['cases']),figsize=(12,5.7),squeeze=False)
    for ax,case in zip(axes[0],summary['cases']):
        x=np.arange(4)
        bottoms=np.zeros(4)
        for label,color,keys in categories:
            values=[sum(case['selected'][str(m)]['cost_components'][k] for k in keys) if case['selected'][str(m)] else 0 for m in (5,6,7,8)]
            ax.bar(x,values,bottom=bottoms,width=.64,color=color,label=label)
            bottoms+=np.array(values)
        for index,total in enumerate(bottoms):
            label=f'{total:,.1f}' if case['selected'][str(index+5)] else 'No solution'
            ax.annotate(label,(index,total),xytext=(0,6),textcoords='offset points',ha='center',fontsize=10)
        ax.set_xticks(x,['M5','M6','M7','M8'])
        ax.set_title(f'{case["customers"]} customers; {case["demand"]} container uses')
        ax.set_ylabel('Synthetic cost units per cycle')
        ax.set_ylim(0,max(bottoms)*1.16 if max(bottoms)>0 else 1)
        ax.grid(axis='y',alpha=.18)
        ax.set_axisbelow(True)
    handles,labels=axes[0,0].get_legend_handles_labels()
    fig.legend(handles,labels,loc='lower center',bbox_to_anchor=(.5,.075),ncol=3,frameon=False)
    fig.suptitle('Large-scale M5-M8 cost comparison: 60 seconds, 3 seeds',fontsize=14)
    fig.text(.5,.02,'Selected by distance for M5 and by cost for M6-M8. Feasible results; no optimality proof.',ha='center',fontsize=9,color='#555555')
    fig.tight_layout(rect=(0,.20,1,.93))
    fig.savefig(OUT/'cost_components.png',dpi=180)
    fig.savefig(OUT/'cost_components.svg')
    plt.close(fig)
    print('CHECKED',summary['valid'],'/',summary['completed'],'valid; same profile as previous experiment; chart exported')


if __name__=='__main__':
    main()
