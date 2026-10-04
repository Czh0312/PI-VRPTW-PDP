"""Summarize saved trial outputs and render an audited LaDe operating schedule."""
from __future__ import annotations
from pathlib import Path
import csv,json,sys
from collections import Counter
ROOT=Path(__file__).resolve().parents[4]
sys.path.insert(0,str(ROOT))
from configuration.loader import load_routing_case
from models.routing.variants.model_3.multibatch import load_instance
from models.routing.variants.model_3.multibatch.audit import audit_solution
from cli.multibatch import plot_result

OUT=Path(__file__).resolve().parent
families=json.loads((OUT/'trial_plan.json').read_text())['families']
labels={'zhou_Set1_7-2-1':'Zhou 小型 · 7 顾客','zhou_Set2_100-5-1b_subset50':'Zhou 中型 · 50 顾客',
        'zhou_Set2_200-10-1':'Zhou 大型 · 200 顾客','lade_0829_10aoi':'LaDe 小型 · 10 AOI',
        'lade_0829_20aoi':'LaDe 中型 · 20 AOI','lade_0829_30aoi':'LaDe 大型 · 30 AOI'}

def write_csv(path,rows):
    with path.open('w',encoding='utf-8-sig',newline='') as handle:
        writer=csv.DictWriter(handle,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)

def yes(value): return str(value).lower()=='true'

runs=[]
for directory in [OUT/'alns',OUT/'exact']+sorted(OUT.glob('refine_*')):
    manifest=json.loads((directory/'manifest.json').read_text())
    jobs={j['run']:j for j in manifest['jobs']}
    with (directory/'runs.csv').open(newline='') as handle:
        for row in csv.DictReader(handle):
            row['result_path']=str((directory/row['run']/'result.json').relative_to(OUT))
            row['case_path']=jobs[row['run']]['case_path']
            row['run_group']=directory.name
            if yes(row['valid']):
                cfg,fleet=load_routing_case(row['case_path'])
                result=json.loads((OUT/row['result_path']).read_text())
                audit=audit_solution(load_instance(cfg,fleet),result)
                if not audit['valid'] or not audit['periodic_replay']['valid']:
                    raise RuntimeError(f'Saved result failed independent re-audit: {row["result_path"]}')
            runs.append(row)
refs={(r['case_id'],int(r['pool'])):float(r['objective']) for r in runs if yes(r['valid']) and yes(r['proven_optimal'])}
for row in runs:
    ref=refs.get((row['case_id'],int(row['pool'])))
    row['optimal_gap_percent']=100*(float(row['objective'])-ref)/ref if ref and yes(row['valid']) else ''
    if row['optimal_gap_percent']!='' and abs(row['optimal_gap_percent'])<1e-7: row['optimal_gap_percent']=0.0
write_csv(OUT/'all_runs.csv',runs)
summary=[]
chosen={}
for family in families:
    group=[r for r in runs if r['family_id']==family and r['solver']=='alns']
    single=next(r for r in group if int(r['batch_count'])==1)
    multi=[r for r in group if int(r['batch_count'])==3]
    valid=[r for r in multi if yes(r['valid'])]
    best=min(valid,key=lambda r:(int(r['pool']),float(r['objective'])))
    chosen[family]=best
    total=int(best['total_container_uses']);pool=int(best['pool'])
    full=next(r for r in multi if int(r['pool'])==total)
    lower=max(int(r['pool_lower_bound']) for r in multi)
    summary.append(dict(family_id=family,label=labels[family],single_pool=int(single['pool']),
                        multi_tested_min_pool=pool,necessary_pool_lower_bound=lower,minimum_pool_proven=lower==pool,
                        pool_reduction_percent=100*(total-pool)/total,
                        single_full_pool_distance=float(single['objective']),multi_full_pool_distance=float(full['objective']),
                        multi_min_pool_distance=float(best['objective']),
                        distance_unit='km' if family.startswith('lade') else 'benchmark_coordinate_units',
                        valid_min_pool_result=best['result_path']))
write_csv(OUT/'summary.csv',summary)
(OUT/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n')

# All detailed figures and tables below derive from this one independently audited schedule.
selected=chosen['lade_0829_30aoi']
cfg,fleet=load_routing_case(selected['case_path'])
problem=load_instance(cfg,fleet)
result=json.loads((OUT/selected['result_path']).read_text())
audit=result['audit'];p=problem.operating_policy
figures=OUT/'figures';figures.mkdir(exist_ok=True)
plot_result(problem,result,figures/'lade30_timeline.png')

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.ticker import FuncFormatter
plt.rcParams.update({'font.size':10,'axes.spines.top':False,'axes.spines.right':False,'axes.titleweight':'bold'})
fig,axes=plt.subplots(2,1,figsize=(11,7),gridspec_kw={'height_ratios':[2,1.2]},constrained_layout=True)
times=[p.minutes(p.cycle_start)/60]+[p.minutes(e['time'])/60 for e in audit['container_ledger']]+[p.minutes(p.cycle_end)/60]
clean=[p.container_pool_size]+[e['states']['clean'] for e in audit['container_ledger']]+[audit['terminal_state']['clean']]
axes[0].step(times,clean,where='post',color='#147d92',linewidth=2)
axes[0].fill_between(times,clean,step='post',color='#147d92',alpha=.14)
for batch in p.batches:
    axes[0].axvline(p.minutes(batch.cutoff)/60,color='#d19138',linestyle='--',alpha=.8)
    axes[0].annotate(batch.id+' issue',(p.minutes(batch.start)/60,0),xytext=(4,8),textcoords='offset points',fontsize=9)
axes[0].set(xlim=(8,21),ylim=(0,p.container_pool_size*1.18),ylabel='Clean containers at depot',
            title=f'LaDe 08-29 | 30 AOIs | 3 batches | N = {p.container_pool_size} | {float(selected["objective"]):.2f} km')
axes[0].set_xticks(range(8,22));axes[0].xaxis.set_major_formatter(FuncFormatter(lambda x,pos:f'{int(x):02}:00'))
axes[0].grid(axis='y',alpha=.2)
axes[0].text(.99,.96,'Dashed lines: collection eligibility cutoffs\nAll containers clean at cycle end; 2-cycle replay passed',
             transform=axes[0].transAxes,ha='right',va='top',fontsize=9,color='#425466')
stocks=audit['clean_stock'];xs=list(range(len(stocks)))
axes[1].bar([x-.19 for x in xs],[r['available'] for r in stocks],.36,label='Clean stock before issue',color='#147d92')
axes[1].bar([x+.19 for x in xs],[r['demand'] for r in stocks],.36,label='Batch demand',color='#d19138')
for x,r in zip(xs,stocks):
    for dx,key in [(-.19,'available'),(.19,'demand')]:
        axes[1].text(x+dx,r[key]+2,str(r[key]),ha='center',fontsize=10)
axes[1].set_xticks(xs);axes[1].set_xticklabels([f'{r["batch_id"]}  /  {p.minutes(r["time"])/60:02.0f}:00' for r in stocks])
axes[1].set(ylabel='Containers',ylim=(0,max(r['available'] for r in stocks)*1.28),title='Each dispatch has enough clean containers')
axes[1].legend(loc='upper right',frameon=False);axes[1].grid(axis='y',alpha=.15)
fig.savefig(figures/'lade30_inventory.png',dpi=180);plt.close(fig)

schedule=[]
for layer in ('e1','e2'):
    for trip in result[layer+'_trips']:
        base=problem.depot if layer=='e1' else trip['satellite_id']
        nodes=[base]+[e['site_id'] if layer=='e1' else problem.order_map[e['order_id']].customer for e in trip['events']]+[base]
        schedule.append(dict(layer=layer,trip_id=trip['trip_id'],vehicle_id=trip['vehicle_id'],
                             batch_id=trip.get('batch_id',''),satellite_id=trip.get('satellite_id',''),
                             departure_minutes=p.minutes(trip['departure']),return_complete_minutes=p.minutes(trip['return_complete']),
                             route=' -> '.join(nodes),distance_km=sum(problem.distance(a,b) for a,b in zip(nodes,nodes[1:]))))
write_csv(OUT/'lade30_routes.csv',schedule)
write_csv(OUT/'lade30_clean_stock.csv',[dict(**r,time_minutes=p.minutes(r['time'])) for r in stocks])

statuses=Counter(r['status'] for r in runs);valid_count=sum(yes(r['valid']) for r in runs)
lines=['新数据集代表算例：求解试跑结果','',
       'Material Passport：Origin Skill = academic-research-suite / experiment-agent；Origin Mode = run；Verification Status = VERIFIED（本次已保存结果逐一重审通过；研究结论范围为短预算代表样本）。','',
       '2026-09-29。先校验 static_suite_v1 的 870 个文件哈希和 69 组配对，再选 6 组代表数据；不是对 207 个配置做全量评测。',
       '每组比较 B=1 与 B=3，ALNS 单次 5 秒、最多 5000 次迭代、种子 42；7 顾客小例另用 MILP 单次 30 秒参考。',
       '初轮使用数据集建议池网格，随后根据初轮必要池下界及有效时间表另声明 7 个 N 值；两份计划及每次源码/输入快照均保留。',
       f'共 {len(runs)} 次运行，{valid_count} 个有效解全部再次通过独立审计与连续两周期重放。状态：'+json.dumps(statuses,ensure_ascii=False)+'。','',
       '容器池结果（“已测最小”是本次试跑找到的最小可行测试 N）：','',
       '| 数据 | 单批池 N=Q | 三批已测最小 N | 必要下界 | 最小池已证明 | 池减少 | 三批该方案距离 |',
       '| --- | ---: | ---: | ---: | --- | ---: | ---: |']
for r in summary:
    unit='km' if r['distance_unit']=='km' else '坐标单位'
    lines.append(f'| {r["label"]} | {r["single_pool"]} | {r["multi_tested_min_pool"]} | {r["necessary_pool_lower_bound"]} | '
                 f'{"是" if r["minimum_pool_proven"] else "否"} | {r["pool_reduction_percent"]:.1f}% | {r["multi_min_pool_distance"]:.3f} {unit} |')
lines += ['', '这里的“池减少”比较资源数量，不等于费用下降。最小池证明仅在实际有效解与必要下界闭合时成立，范围包含本版固定日程、时间精度及趟次上限。', '',
          '相同 N=Q 下的单批/三批距离（ALNS，同一个种子和求解预算）：','',
          '| 数据 | B=1 距离 | B=3 距离 | 单位 |','| --- | ---: | ---: | --- |']
for r in summary:
    lines.append(f'| {r["label"]} | {r["single_full_pool_distance"]:.3f} | {r["multi_full_pool_distance"]:.3f} | {"km" if r["distance_unit"]=="km" else "坐标单位"} |')
lines += ['', '本轮部分 ALNS 方案在较大 N 下距离反而更长，这是短预算启发式搜索所得解的差异，不能解释为扩大容器池会提高数学最优成本。','',
          '7 顾客精确参考（N=7）：','', '| 批次 | MILP 状态 | 距离 | 已证距离最优 |','| --- | --- | ---: | --- |']
for r in runs:
    if r['solver']=='exact': lines.append(f'| {r["batch_count"]} | {r["status"]} | {float(r["objective"]):.6f} | {r["proven_optimal"]} |')
lines += ['', 'LaDe 30 AOI 三批方案详情：','',
          f'使用 N={p.container_pool_size}，全周期需求 Q={problem.total} 箱次，总距离 {float(selected["objective"]):.3f} km；'
          f'一级 {audit["e1_vehicle_count"]} 辆/{audit["e1_trip_count"]} 趟，二级 {audit["e2_vehicle_count"]} 辆/{audit["e2_trip_count"]} 趟。',
          f'[完整结果 JSON]({selected["result_path"]}) · [逐趟路线 CSV](lade30_routes.csv) · [逐批库存 CSV](lade30_clean_stock.csv)','',
          '| 批次 | 领箱时刻 | 清洁库存 | 本批需求 | 领用后剩余 |','| --- | --- | ---: | ---: | ---: |']
for r in stocks:
    minute=p.minutes(r['time']);clock=f'{int(minute//60):02}:{int(minute%60):02}'
    lines.append(f'| {r["batch_id"]} | {clock} | {r["available"]} | {r["demand"]} | {r["after"]} |')
lines += ['', '![LaDe 30 AOI 清洁库存](figures/lade30_inventory.png)','',
          '![按批次划分的两级路线](figures/routes_1.png)','',
          '路线图用地点间直线表达访问顺序；距离按保存的道路矩阵计算，图中折线不是实际道路轨迹。', '',
          '[全部运行与失败记录](all_runs.csv) · [六组汇总](summary.csv) · [初轮计划](trial_plan.json) · [补充池测试计划](refinement_plan.json)', '',
          '范围：Zhou 是重设窗口的派生场景，距离为坐标单位；LaDe 是真实配送基础上的情景，容器回收、清洗和车队不是现场实测。'
          '本轮只用一个随机种子和短预算，未开展正式算法评测或费用比较。']
(OUT/'report.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
print(json.dumps(dict(runs=len(runs),valid=valid_count,statuses=statuses,summary=summary,
                     selected_result=selected['result_path'],clean_stock=stocks),ensure_ascii=False,indent=2))
