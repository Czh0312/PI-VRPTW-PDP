"""Only E1 volume capacity changes: rerun M3/M8 ALNS on existing input."""
from pathlib import Path
from copy import deepcopy
import json,hashlib,time,statistics,csv
from tools.configuration.loader import load_routing_case
from models.common.cycle import load_instance
from models.common.cycle.audit import audit_solution
from models.cost.comparison import evaluate_comparison_cost
from models.common.cycle.calendar import attach_calendar_decisions
from cli.multibatch import run_case
ROOT=Path('output/l05_capacity60_rerun_20261020');DATA=Path('data/mechanism_l05_3sat_balanced_v1')
PREV=Path('output/mechanism_l05_3sat_balanced_20261020')
def write(p,v):p.write_text(json.dumps(v,ensure_ascii=False,indent=2)+'\n')
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
assert not (ROOT/'protocol.json').exists(),'Do not overwrite an existing execution'
old=json.load((PREV/'execution.json').open());fleet=json.load((DATA/'fleet.json').open());oldfleet=deepcopy(fleet);oldfleet['E1_Ck']=200
oldbytes=(json.dumps(oldfleet,ensure_ascii=False,indent=2)+'\n').encode()
assert hashlib.sha256(oldbytes).hexdigest()==old['input_sha256'][str(DATA/'fleet.json')]
assert fleet['E1_Ck']==60 and fleet['E1_Qk']==3000
write(ROOT/'previous_fleet.json',oldfleet)
unchanged={p:sha(p)==h for p,h in old['input_sha256'].items() if p!=str(DATA/'fleet.json')}
assert all(unchanged.values()),[p for p,v in unchanged.items() if not v]
inputs={p:sha(p) for p in old['input_sha256']}
source={str(p):sha(p) for base in ['models','cli','tools'] for p in Path(base).rglob('*.py')}
write(ROOT/'protocol.json',dict(date='2026-10-20',source_dataset=str(DATA),
 change=dict(parameter='E1_Ck',old=50,new=60,reference_capacity=200,unit='standard box volume slots'),
 E1_weight_capacity=3000,all_other_original_inputs_unchanged=True,unchanged_checks=unchanged,
 input_sha256=inputs,source_sha256=source,seeds=[17,42,2026],seconds=60,iterations=1000000000,
 groups=['M3_cold','M8_cold','M8_feasible_start'],
 note='No new case design, no assignment/window/fleet-count/model changes. M8 feasible-start uses the same deterministic witness as previous experiment; cold results retained separately.',
 started_at=time.time(),command='PYTHONPATH=. .venv/bin/python output/l05_capacity60_rerun_20261020/run.py'))
rows=[]
for seed in [17,42,2026]:
 for group in ['M3_cold','M8_cold','M8_feasible_start']:
  label='M3' if group=='M3_cold' else 'M8'
  cfg,fc=load_routing_case(DATA/label/'case.json',use_pool_config=False)
  cfg.update(seed=seed,alns_time=60,alns_iter=1000000000)
  if group=='M8_feasible_start':
   parent=load_instance(cfg,fc);initial=json.load((PREV/'diagnostics/witness_M8_K3.json').open())
   attach_calendar_decisions(parent,initial,3,initial['batch_boundaries'])
   initial['audit']=audit_solution(parent,initial)
   assert initial['audit']['valid'],initial['audit']['errors']
   write(ROOT/'initial_M8.json',initial);cfg['initial_joint_result']=str(ROOT/'initial_M8.json')
  print(f'START {group} seed={seed} E1_Ck=60 E1_Qk=3000',flush=True)
  folder=ROOT/group/f'seed_{seed}'
  result=run_case(cfg,fc,case_path=DATA/label/'case.json',output=folder)
  problem=load_instance(cfg,fc)
  audit=audit_solution(problem,result) if result['info'].get('objective') is not None else {}
  write(folder/'independent_audit.json',audit)
  row=dict(group=group,seed=seed,status=result['info']['status'],valid=audit.get('valid',False),
           proven_infeasible=result['info'].get('proven_infeasible',False),
           precheck_errors=result.get('precheck',{}).get('errors',[]),wall_time=result['wall_time'],source=str(folder))
  if row['valid']:
   cost=evaluate_comparison_cost(problem,result,str(DATA/'economic.json'),audit=audit)
   write(folder/'comparison_cost.json',cost)
   pool=cost['container_pool_size'];assignment=result.get('order_batch_assignment',{o.id:'b1' for o in problem.orders})
   orders={o.id:o for o in problem.orders};dw=[];dv=[]
   for trip in result['e1_trips']:
    sites={e['site_id'] for e in trip['events'] if e['kind']=='d'}
    if not sites:continue
    served=[o for oid,o in orders.items() if o.satellite in sites and assignment[oid]==trip['batch_id']]
    dw.append(sum(o.goods_weight+o.count*fc['container_weight'] for o in served)/fc['E1_Qk'])
    dv.append(sum(o.count*fc['container_volume'] for o in served)/fc['E1_Ck'])
   row.update(N=pool,K=result.get('cycle_batch_count',1),saving_rate=(100-pool)/100,turnover=100/pool,
    D1=audit['distances']['e1'],D2=audit['distances']['e2'],trips1=audit['e1_trip_count'],trips2=audit['e2_trip_count'],
    total_cost=cost['total'],two_cycle_valid=audit['periodic_replay']['valid'],
    e1_departure_weight_util=statistics.mean(dw),e1_departure_volume_util=statistics.mean(dv))
  rows.append(row);write(ROOT/'summary.json',rows)
  print('RESULT '+json.dumps(row,ensure_ascii=False),flush=True)
aggregates={}
for group in ['M3_cold','M8_cold','M8_feasible_start']:
 local=[r for r in rows if r['group']==group];valid=[r for r in local if r['valid']]
 a=dict(runs=len(local),successes=len(valid),proven_infeasible=sum(r['proven_infeasible'] for r in local))
 if valid:
  for key in ['N','K','D1','D2','trips1','trips2','total_cost','saving_rate','turnover','e1_departure_weight_util','e1_departure_volume_util']:
   a[key+'_mean']=statistics.mean(r[key] for r in valid)
  a['cost_sd']=statistics.stdev(r['total_cost'] for r in valid) if len(valid)>1 else 0
 aggregates[group]=a
write(ROOT/'aggregates.json',aggregates)
columns=sorted({k for r in rows for k in r if k!='precheck_errors'})
with (ROOT/'summary.csv').open('w',encoding='utf-8-sig',newline='') as stream:
 w=csv.DictWriter(stream,fieldnames=columns,extrasaction='ignore');w.writeheader();w.writerows(rows)
changes=[p for p,h in {**inputs,**source}.items() if sha(p)!=h]
write(ROOT/'integrity.json',dict(passed=not changes,changes=changes,finished_at=time.time()))
assert not changes,changes
lines=['# 原算例一级容量改为60：M3/M8 ALNS重跑','',
 '沿用原100顾客、3仓、100箱次算例，仅将一级体积容量E1_Ck从50改为60；重量容量仍为3000。其余分仓、订单窗口、成本、车辆数、清洗时间和算法代码均不变。对照原200容量快照，唯一运行输入差异也是E1_Ck。',
 '种子17、42、2026；每次ALNS预算60秒、迭代上限10亿。M8冷启动与沿用同一历史确定性可行见证启动分开报告。M8允许K=1…100，未增加固定K对照。','',
 '| 运行方式 | 可行/次数 | 已证不可行次数 | 平均N | 所选K均值 | 平均成本 | 一级出仓体积装载率 |',
 '|---|---:|---:|---:|---:|---:|---:|']
for group,a in aggregates.items():
 if a['successes']:lines.append(f"| {group} | {a['successes']}/{a['runs']} | {a['proven_infeasible']} | {a['N_mean']:.2f} | {a['K_mean']:.2f} | {a['total_cost_mean']:.2f} | {a['e1_departure_volume_util_mean']:.2%} |")
 else:lines.append(f"| {group} | 0/{a['runs']} | {a['proven_infeasible']} | — | — | — | — |")
lines+=['','## 运输与箱池','',
 '| 运行方式 | 一级距离 | 二级距离 | 一级趟次 | 二级趟次 | 节箱率 | 周转次数 |',
 '|---|---:|---:|---:|---:|---:|---:|']
for group,a in aggregates.items():
 if a['successes']:lines.append(f"| {group} | {a['D1_mean']:.2f} | {a['D2_mean']:.2f} | {a['trips1_mean']:.2f} | {a['trips2_mean']:.2f} | {a['saving_rate_mean']:.2%} | {a['turnover_mean']:.3f} |")
if aggregates['M3_cold']['successes']:
 for group in ['M8_cold','M8_feasible_start']:
  if aggregates[group]['successes']:
   reduction=1-aggregates[group]['total_cost_mean']/aggregates['M3_cold']['total_cost_mean']
   lines+=['',f"{group}相对本次M3的平均成本变化：降低{reduction:.2%}。"]
lines+=['',
 '容量60已容纳S3单批54箱，消除了此前50容量触发的单仓不可拆分容量冲突。必要条件通过本身不证明整体可行。',
 'HEURISTIC_NO_SOLUTION仅表示ALNS预算内未找到解；INFEASIBLE仅在有明确证明时报告。全部可行解独立审计并进行连续两周期重放。',
 '费用统一为0.1D1+0.05D2+5N+0.02Q，使用合成成本、基准坐标距离。M3原生优化总距离，报告成本为同口径事后重算；M8直接优化成本。工时、车辆固定费和每趟固定费为0。',
 '装载率先逐种子按配送出仓趟次平均，再对三个种子平均。减小容量分母不等于运输效率同比提升。所选K及箱池未证明全局最优。',
 '本次没有重新设计空间、需求或分仓。历史50/200容量结果及输入快照保留；当前原数据目录fleet.json为60容量。历史design/input_checks为原200容量记录，当前运行检查见本输出。',
 '', '## Material Passport','', '- Origin Skill: academic-research-suite / experiment-agent','- Origin Mode: run','- Origin Date: 2026-10-20','- Verification Status: ANALYZED; no reproducibility rerun','- Version Label: l05_capacity60_rerun','']
(ROOT/'report.md').write_text('\n'.join(lines),encoding='utf-8')
print('COMPLETE '+json.dumps(aggregates,ensure_ascii=False),flush=True)
