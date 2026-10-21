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
ROOT=Path('output/l05_capacity50_rerun_20261020');DATA=Path('data/mechanism_l05_3sat_balanced_v1')
PREV=Path('output/mechanism_l05_3sat_balanced_20261020')
def write(p,v):p.write_text(json.dumps(v,ensure_ascii=False,indent=2)+'\n')
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
assert not (ROOT/'protocol.json').exists(),'Do not overwrite an existing execution'
old=json.load((PREV/'execution.json').open());fleet=json.load((DATA/'fleet.json').open());oldfleet=deepcopy(fleet);oldfleet['E1_Ck']=200
oldbytes=(json.dumps(oldfleet,ensure_ascii=False,indent=2)+'\n').encode()
assert hashlib.sha256(oldbytes).hexdigest()==old['input_sha256'][str(DATA/'fleet.json')]
assert fleet['E1_Ck']==50 and fleet['E1_Qk']==3000
write(ROOT/'previous_fleet.json',oldfleet)
unchanged={p:sha(p)==h for p,h in old['input_sha256'].items() if p!=str(DATA/'fleet.json')}
assert all(unchanged.values()),[p for p,v in unchanged.items() if not v]
inputs={p:sha(p) for p in old['input_sha256']}
source={str(p):sha(p) for base in ['models','cli','tools'] for p in Path(base).rglob('*.py')}
write(ROOT/'protocol.json',dict(date='2026-10-20',source_dataset=str(DATA),
 change=dict(parameter='E1_Ck',old=200,new=50,unit='standard box volume slots'),
 E1_weight_capacity=3000,all_other_original_inputs_unchanged=True,unchanged_checks=unchanged,
 input_sha256=inputs,source_sha256=source,seeds=[17,42,2026],seconds=60,iterations=1000000000,
 groups=['M3_cold','M8_cold','M8_feasible_start'],
 note='No new case design, no assignment/window/fleet-count/model changes. M8 feasible-start uses the same deterministic witness as previous experiment; cold results retained separately.',
 started_at=time.time(),command='PYTHONPATH=. .venv/bin/python output/l05_capacity50_rerun_20261020/run.py'))
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
  print(f'START {group} seed={seed} E1_Ck=50 E1_Qk=3000',flush=True)
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
lines=['# 原算例一级容量改为50：M3/M8 ALNS重跑','',
 '只将 data/mechanism_l05_3sat_balanced_v1/fleet.json 的 E1_Ck 从200改为50；重量容量仍为3000。顾客、分仓、时间窗、需求、费用、清洗、车辆数及算法代码均不变。此前单独创建的1000重量容量候选未用于本次运行。',
 '种子17、42、2026，每次ALNS预算60秒、迭代上限10亿。M8冷启动和同一历史可行见证启动分开报告；不新增固定K对照。','',
 '| 运行方式 | 可行/次数 | 已证不可行次数 | 平均N | 平均K | 平均成本 | 一级出仓体积装载率 |',
 '|---|---:|---:|---:|---:|---:|---:|']
for group,a in aggregates.items():
 if a['successes']:lines.append(f"| {group} | {a['successes']}/{a['runs']} | {a['proven_infeasible']} | {a['N_mean']:.2f} | {a['K_mean']:.2f} | {a['total_cost_mean']:.2f} | {a['e1_departure_volume_util_mean']:.2%} |")
 else:lines.append(f"| {group} | 0/{a['runs']} | {a['proven_infeasible']} | — | — | — | — |")
lines+=['',
 'M3若触发UNSPLIT_E1_DELIVERY_CAPACITY，是S3单批54箱超过50箱，当前模型不允许同仓同批由多车拆分配送。增加车数不能解除该约束；这是输入必要条件证明，不是ALNS搜索失败。',
 'M8的HEURISTIC_NO_SOLUTION表示预算内未找到可行解，不能判为不可行。全部可行解重算共同成本，并独立审计与连续两周期重放。',
 'M3若不可行，本次不能给出同口径的M3/M8成本节约率，也不能将模型可行性差异直接解释为经济优势。',
 '当前目录保存逐次结果、初解、输入/源码快照、比较费用、检查错误和哈希。原200容量历史结果仍保留于旧输出目录；原数据目录中的历史input_checks/design属于此前200容量记录，本次有效检查以当前输出为准。',
 '', '## Material Passport','', '- Origin Skill: academic-research-suite / experiment-agent','- Origin Mode: run','- Origin Date: 2026-10-20','- Verification Status: ANALYZED; no reproducibility rerun','- Version Label: l05_capacity50_rerun','']
(ROOT/'report.md').write_text('\n'.join(lines),encoding='utf-8')
print('COMPLETE '+json.dumps(aggregates,ensure_ascii=False),flush=True)
