"""Report all cold failures, supplemental starts and audited physical metrics."""
from pathlib import Path
import csv,json,statistics,collections,hashlib
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
ROOT=Path('output/mechanism_l05_3sat_balanced_20261020');DATA=Path('data/mechanism_l05_3sat_balanced_v1')
rows=json.loads((ROOT/'summary.json').read_text())+json.loads((ROOT/'diagnostics/feasible_start_summary.json').read_text())
labels=['M3','M8','M8_K3','M8_feasible_start','M8_K3_feasible_start']
aggregates={}
for label in labels:
 local=[r for r in rows if r['group']==label]; valid=[r for r in local if r['valid']]
 a=dict(runs=len(local),successes=len(valid))
 if valid:
  for key in ['N','K','total_cost','D1','D2','trips1','trips2','saving_rate','turnover']:
   a[key+'_mean']=statistics.mean(r[key] for r in valid)
  a['cost_sd']=statistics.stdev(r['total_cost'] for r in valid) if len(valid)>1 else None
  a['best_cost']=min(r['total_cost'] for r in valid)
  a['worst_cost']=max(r['total_cost'] for r in valid)
  a['best_source']=min(valid,key=lambda r:r['total_cost'])['source']
 aggregates[label]=a
base=aggregates['M3']['total_cost_mean']
for label in labels:
 if 'total_cost_mean' in aggregates[label]:aggregates[label]['cost_reduction_vs_M3_mean']=1-aggregates[label]['total_cost_mean']/base
(ROOT/'comparison_aggregates.json').write_text(json.dumps(aggregates,indent=2))
columns=[k for k in rows[0] if k!='cost_components']+['initial_cost']
with (ROOT/'all_runs.csv').open('w',encoding='utf-8-sig',newline='') as stream:
 writer=csv.DictWriter(stream,fieldnames=columns,extrasaction='ignore');writer.writeheader();writer.writerows(rows)
physical=[]
source_orders={o['order_id']:o for o in json.loads((DATA/'reference_orders.json').read_text())}
for row in rows:
 if not row['valid']:continue
 folder=Path(row['source']);result=json.loads((folder/'result.json').read_text());audit=json.loads((folder/'independent_audit.json').read_text())
 assignment=result.get('order_batch_assignment',{oid:'b1' for oid in source_orders})
 demand=collections.Counter((source_orders[oid]['satellite_id'],bid) for oid,bid in assignment.items())
 values=[demand[s,f'b{b}'] for s in ['S2','S3','S5'] for b in range(1,row['K']+1)]
 coverage={k:sum(bool(x.get('visits')) for x in result.get('count_search',[])) for k in ['visited_K']}
 batches=[dict(id=b['id'],start=b['start']/60,cutoff=b['cutoff']/60,end=b['end']/60) for b in result['batches']]
 physical.append(dict(group=row['group'],seed=row['seed'],N=row['N'],K=row['K'],qsb={s:[demand[s,f'b{b}'] for b in range(1,row['K']+1)] for s in ['S2','S3','S5']},
  qsb_mean=statistics.mean(values),qsb_min=min(values),qsb_zero_share=sum(v==0 for v in values)/len(values),qsb_1_2_share=sum(1<=v<=2 for v in values)/len(values),
  clean_stock=audit['clean_stock'],terminal=audit['terminal_state'],two_cycle_valid=audit['periodic_replay']['valid'],batches_minutes=batches,
  **coverage))
(ROOT/'physical_diagnostics.json').write_text(json.dumps(physical,indent=2))
lines=['# L05三仓均衡需求：M3与M8的ALNS比较','',
 '本次完成一个独立机制验证算例。结果同时暴露了M8冷启动构造可行解的困难；不能把后续可行初解启动的结果冒充冷启动成功。','',
 '## 算例设计','',
 '保留L05的100位顾客和中央仓坐标，从原5个仓中穷举选择3仓，使顾客到最近仓的距离总和最小。选中S2、S3、S5；未使用求解结果选择仓或调整成本。',
 '每客一单一箱，Q=100。按固定随机种子20261020生成外生预约窗口，三个服务时段需求为35/35/30；窗口宽度仍为配送90分钟、回收105分钟。全部订单08:00释放。M8不受这些参考批次标签绑定。','',
 '| 仓 | 第一时段 | 第二时段 | 第三时段 | 顾客总数 |','|---|---:|---:|---:|---:|',
 '| S2 | 6 | 7 | 6 | 19 |','| S3 | 19 | 19 | 16 | 54 |','| S5 | 10 | 9 | 8 | 27 |',
 '| 合计 | 35 | 35 | 30 | 100 |','',
 '每仓每时段平均11.11箱，最少6箱，1–2箱组合占比0。此处为外生需求时段统计；实际M8分批另见physical_diagnostics.json。',
 '同一输入下M3与M8均配5辆一级车、每仓8辆二级车，共24辆。相较原L05的25辆减少4%；本实验不能识别减少前置仓的单独效应。',
 '保留原货重、服务时间、最短使用时间、运载容量、速度、箱体规格、08:00–21:00周期、15分钟清洗及装卸时间。','',
 '## 执行协议与失败记录','',
 '种子17、42、2026；每次ALNS预算60秒，迭代上限10亿，以时间预算为主。M3固定K=1、N=100；M8允许K=1…100、N=1…100；M8_K3固定K=3。原生模型、算子和参数未改。',
 '先运行无外部初解的9次实验；全部结果保留。随后才声明补充诊断：以同一个确定性构造、独立审计通过的K=3、N=35可行见证，启动M8和固定K=3的M8，各3个种子、每次60秒。初解构造耗时另计，补充实验不替代冷启动实验。',
 '初解使用每条二级路线至多3单的地理邻近分组、各批配送后回收、每批第200分钟作为初始回收分界，成本417.3431。清洗与回收物理时间均未缩短。见证不是最优解，也没有采用精确求解器。','',
 '| 运行方式 | 可行/总数 | 平均N | 所选K | 平均总成本 | 标准差 | 最好成本 |',
 '|---|---:|---:|---:|---:|---:|---:|']
cn={'M3':'M3，冷启动','M8':'M8，冷启动','M8_K3':'M8固定K=3，冷启动','M8_feasible_start':'M8，可行初解启动','M8_K3_feasible_start':'M8固定K=3，可行初解启动'}
for label in labels:
 a=aggregates[label];local=[r for r in rows if r['group']==label and r['valid']]
 if a['successes']:
  ks=','.join(map(str,sorted({r['K'] for r in local})))
  lines.append(f"| {cn[label]} | {a['successes']}/{a['runs']} | {a['N_mean']:.2f} | {ks} | {a['total_cost_mean']:.2f} | {a['cost_sd']:.2f} | {a['best_cost']:.2f} |")
 else:lines.append(f"| {cn[label]} | 0/{a['runs']} | — | — | — | — | — |")
lines+=['','未找到解的状态是HEURISTIC_NO_SOLUTION，未宣称不可行。K=3固定参考日历初始化的独立诊断也出现时间窗、末态未回收或空回收批次问题；确定性见证通过审计证明该算例本身有可行三批方案。',
 '冷启动结果反映当前ALNS的构造/搜索困难；可行初解诊断提供条件性的优化表现。两者不可混为一种成功率。',
 '固定K=3对照的成本更低，不代表限制可行域改善了模型最优值：在相同60秒总预算内，它把更多时间用于K=3，而完整域还要探索其他K。这是有限预算下的启发式结果。','',
 '## 同口径物理与成本比较','',
 '| 组别 | 平均一级距离 | 平均二级距离 | 平均一级趟次 | 平均二级趟次 | 节箱率 | 周转次数Q/N |',
 '|---|---:|---:|---:|---:|---:|---:|']
for label in ['M3','M8_feasible_start','M8_K3_feasible_start']:
 a=aggregates[label]
 lines.append(f"| {cn[label]} | {a['D1_mean']:.2f} | {a['D2_mean']:.2f} | {a['trips1_mean']:.2f} | {a['trips2_mean']:.2f} | {a['saving_rate_mean']:.1%} | {a['turnover_mean']:.3f} |")
lines+=['',f"在本算例及给定合成费用下，可行初解启动M8的平均成本比M3低{aggregates['M8_feasible_start']['cost_reduction_vs_M3_mean']:.2%}。应结合运输距离与趟次增量解释，不能仅报告节箱率。",'',
 '统一成本为0.1D1+0.05D2+5N+0.02Q，单位为合成成本，距离为原Zhou坐标单位。M3原生优化总距离，其成本是审计后用共同费用系数重算；M8直接优化成本。',
 '工时、车辆固定费、每趟固定费在沿用费用配置中均为0，因此本次不能代表含全部运输组织成本的真实利润。',
 '所有报告为可行解，未证明全局最优；所选K是预算内找到的最好K，不能称为最优批次数。3种子仅作描述，不作显著性或跨算例稳健性推断。','',
 '## 审计与复现','',
 '每个可行解已重新独立检查时间窗、两级载重/体积、车辆连续占用、逐单送取、箱池守恒、回库清洗、周期末全清洁和连续两周期重放。见各运行目录independent_audit.json。',
 '原始冷启动结果见summary.csv，全部运行见all_runs.csv，汇总见comparison_aggregates.json，库存和实际分批见physical_diagnostics.json。',
 '代码/输入SHA及未提交工作区补丁均已保存；reproduction_snapshot.zip包含执行时的Python源文件和新算例输入。原27例正式数据未改。',
 '执行命令：`.venv/bin/python -m experiments.runs.run_l05_dense_pilot build`、`.venv/bin/python -m experiments.runs.run_l05_dense_pilot run`。',
 '补充诊断：`PYTHONPATH=. .venv/bin/python output/mechanism_l05_3sat_balanced_20261020/diagnostics/run_feasible_start.py`。复跑时应使用新输出目录。','',
 '## Material Passport','',
 '- Origin Skill: academic-research-suite / experiment-agent','- Origin Mode: run','- Origin Date: 2026-10-20',
 '- Verification Status: ANALYZED; independent physical replay passed, no reproducibility rerun',
 '- Version Label: mechanism_l05_3sat_balanced_v1','']
(ROOT/'comparison.md').write_text('\n'.join(lines),encoding='utf-8')
fig,axes=plt.subplots(1,3,figsize=(13,4.6),layout='constrained')
plotgroups=['M3','M8_feasible_start','M8_K3_feasible_start'];x=range(3);xt=['M3\ncold start','M8\nfeasible start','M8, K=3\nfeasible start']
for i,label in enumerate(plotgroups):
 valid=[r for r in rows if r['group']==label and r['valid']]
 axes[0].bar(i,statistics.mean(r['N'] for r in valid),color=['#65758b','#338da6','#64a67c'][i],width=.65)
 bottom=0
 for key,color,desc in [('container_pool','#65758b','Box pool'),('e1_distance','#e6a252','E1 transport'),('e2_distance','#338da6','E2 transport'),('cleaning','#c2c2c2','Cleaning + wear')]:
  value=statistics.mean(r['cost_components'][key]+(r['cost_components']['container_wear'] if key=='cleaning' else 0) for r in valid)
  axes[1].bar(i,value,bottom=bottom,color=color,width=.65,label=desc if i==0 else None);bottom+=value
 axes[1].text(i,bottom+8,f'{bottom:.1f}',ha='center',fontsize=9)
 axes[2].scatter([i-.08,i,i+.08],[r['total_cost'] for r in valid],s=45,color=['#65758b','#338da6','#64a67c'][i])
for ax in axes:
 ax.set_xticks(list(x));ax.set_xticklabels(xt,fontsize=9);ax.spines[['right','top']].set_visible(False);ax.grid(axis='y',alpha=.15);ax.set_axisbelow(True)
axes[0].set(title='Physical box pool',ylabel='Containers',ylim=(0,112))
axes[1].set(title='Mean cost decomposition',ylabel='Synthetic cost',ylim=(0,650));axes[1].legend(fontsize=8,loc='upper right')
axes[2].set(title='Three-seed cost observations',ylabel='Synthetic cost')
fig.suptitle('Balanced L05 pilot | 100 orders, 3 satellites | Cold-start M8: 0/3 feasible',fontsize=12)
fig.savefig(ROOT/'comparison.png',dpi=180);plt.close(fig)
print(json.dumps(aggregates,indent=2))
