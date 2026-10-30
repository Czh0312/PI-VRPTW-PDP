"""Independently replay both saved runs and write the matched cost report."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys

PROJECT=Path.cwd()
sys.path.insert(0,str(PROJECT))
from models.common.cycle import load_instance
from models.common.cycle.audit import audit_solution
from models.common.cycle.joint import lift_result
from models.common.cycle.calendar import attach_calendar_decisions
from models.cost.comparison import evaluate_comparison_cost

ROOT=Path(__file__).resolve().parent
def read(path):
    return json.loads(path.read_text(encoding='utf-8'))
def write(path,value):
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False)+'\n',encoding='utf-8')
summary=read(ROOT/'summary.json')
experiment=read(ROOT/'experiment.json')
results={}
problems={}
audits={}
costs={}
manifests={}
for model in (3,8):
    manifest=read(ROOT/f'm{model}/manifest.json')
    manifests[model]=manifest
    for filename,expected in manifest['input_sha256'].items():
        assert hashlib.sha256(Path(filename).read_bytes()).hexdigest()==expected,filename
    for filename,expected in manifest['source_sha256'].items():
        assert hashlib.sha256((PROJECT/filename).read_bytes()).hexdigest()==expected,filename
    assert hashlib.sha256((ROOT/f'initial_m{model}.json').read_bytes()).hexdigest()==experiment['initial_sha256'][f'M{model}']
    problem=load_instance(manifest['config'],manifest['fleet'])
    result=read(ROOT/f'm{model}/result.json')
    checked=audit_solution(problem,result)
    assert checked['valid'] and checked['periodic_replay']['valid'],checked['errors']
    price=evaluate_comparison_cost(problem,result,manifest['config']['economic_config'],audit=checked)
    measured=next(row for row in summary['results'] if row['model']==model)
    assert abs(price['total']-measured['total_cost'])<1e-6
    results[model],problems[model],audits[model],costs[model]=result,problem,checked,price
assert manifests[3]['source_sha256']==manifests[8]['source_sha256']

# Check the actual M3 incumbent in M8's legal K=1 domain, without another solve.
child=problems[8].fixed_calendar(1)
projected=lift_result(child,results[3],{o.id:'b1' for o in problems[8].orders},100,
    'comparison_replay',{'b1':problems[3].operating_policy.batches[0].cutoff})
assert projected.get('audit',{}).get('valid'),projected.get('audit',{}).get('errors')
attach_calendar_decisions(problems[8],projected,1,problems[8].balanced_boundaries(1))
projected['audit']=audit_solution(problems[8],projected)
assert projected['audit']['valid'] and projected['audit']['periodic_replay']['valid']
assert abs(projected['info']['objective']-costs[3]['total'])<1e-6
write(ROOT/'m3_replayed_in_m8_K1.json',projected)

metrics={row['model']:row for row in summary['results']}
delta=metrics[8]['total_cost']-metrics[3]['total_cost']
recurring_delta=metrics[8]['recurring_cost']-metrics[3]['recurring_cost']
periods=[dict(H=h,M3=metrics[3]['acquisition']+h*metrics[3]['recurring_cost'],
    M8=metrics[8]['acquisition']+h*metrics[8]['recurring_cost']) for h in (1,2,10)]
verified=dict(configurations=2,solver_results_checked=2,passed=2,failed=0,skipped=0,
    both_two_cycle_replays=True,matched_source_snapshots=True,matched_inputs=True,
    identical_initial_physical_routes=True,m3_replayed_in_m8_K1_valid=True,
    m3_replay_cost=projected['info']['objective'],gurobi_checked=False)
write(ROOT/'independent_validation.json',verified)
summary.update(repeated_identical_cycles=periods,recurring_cost_difference_m8_minus_m3=recurring_delta,
    m3_replay_in_m8=dict(valid=True,K=1,N=100,cost=projected['info']['objective'],new_optimization=False))
write(ROOT/'comparison.json',summary)

lines=['# 均匀预约100顾客：模型3与模型8 ALNS费用对照','',
    '算例：data/scenarios/continuous_flow_100_v1。货物全部在08:00就绪，只有顾客四个取送窗口端点相对原L05调整。'
    '两模型使用相同100个顾客、位置、固定配送站、重量、一客一箱、车辆参数和费用系数。','',
    '两者从完全相同的单批可行路线和显式任务时刻出发，各运行120秒、seed=42。'
    f'共同初解按统一费用核算为 {experiment["initial_comparison_cost"]:.4f}。'
    '模型3通过已有搜索函数的初解参数运行；仅使用当前进程包装，不修改模型或算法源码。'
    '模型8使用原生初解入口，K=1～100、箱池1～100，没有固定多批或有限箱池。',
    '模型3优化未加权的两级距离之和；模型8优化购箱与单周期运营费用。下面统一重新核算费用，'
    '不能把两个求解器的原始目标值直接比较。','',
    '| 指标 | 模型3 ALNS | 模型8 ALNS |','| --- | ---: | ---: |']
for label,key,digits in (
    ('批次数','K',0),('箱池数量','N',0),('一级距离','D1',4),('二级距离','D2',4),
    ('总距离','distance',4),('首次购箱费用','acquisition',4),('每周期运输费用','transport',4),
    ('每周期其他运营费用','other_operating',4),('每周期运营费用合计','recurring_cost',4),
    ('首次购箱＋1周期运营总费用','total_cost',4),('一级趟数','e1_trips',0),
    ('二级趟数','e2_trips',0),('一级车辆数','e1_vehicles',0),('二级车辆数','e2_vehicles',0),('耗时/秒','runtime',3)):
    lines.append(f'| {label} | {metrics[3][key]:.{digits}f} | {metrics[8][key]:.{digits}f} |')
lines+=['',f'模型8减模型3的总费用差：{delta:+.4f}；每周期运营费用差：{recurring_delta:+.4f}。正号表示模型8更高。',
    '距离为算例坐标单位，费用为合成单位。购箱只计一次，清洗和磨损随100次顾客使用计费。',
    '两份求解结果独立检查通过，包括取送时间窗、最低使用时间、车辆衔接、载重体积、箱量守恒、清洗和连续两个周期闭环。'
    '连续两个周期重放是物理检查；上述表格费用仅含首次购箱和一个运营周期。',
    '只运行ALNS，未检查Gurobi、未运行精确求解。单个种子和有限预算没有最优性或一般性能排名证明。','',
    '## 模型8的批次数搜索记录','',
    '下表仅列搜索实际返回并通过审计的候选；保留初解不等于从空初解找到。各K分得时间不同。','',
    '| K | N | 候选费用 |','| ---: | ---: | ---: |']
for row in sorted(summary['m8_candidates'],key=lambda row:row['K']):
    lines.append(f'| {row["K"]} | {row["N"]} | {row["cost"]:.4f} |')
lines+=['',f'另外，将本次模型3的最终路线重放为模型8 K=1方案，费用 {projected["info"]["objective"]:.4f}，'
    '约束及连续两周期检查通过。这个投影没有新增ALNS求解；它用于区分模型允许范围与算法搜索效果。','',
    '## 重复运营周期的费用投影','',
    '保持各自已保存路线不变，购箱只计一次；总费用=首次购箱+H×每周期运营费。未重新优化多周期目标。','',
    '| 周期数H | 模型3总费用 | 模型8总费用 |','| ---: | ---: | ---: |']
for row in periods:
    lines.append(f'| {row["H"]} | {row["M3"]:.4f} | {row["M8"]:.4f} |')
lines+=['']
(ROOT/'comparison.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')

route_lines=['# 本次ALNS实际两级路线','',
    '使用新均匀预约100顾客算例；所有时刻为显式服务开始时间。二级车辆编号在每个站内单独计数。','']
for model in (3,8):
    p=problems[model]
    result=results[model]
    orders={o.id:o for o in p.orders}
    factor=result['time_tick_seconds']/result.get('time_tick_subdivision',1)
    def clock(tick):
        sec=round(tick*factor)
        return f'{sec//3600:02d}:{sec//60%60:02d}:{sec%60:02d}'
    route_lines += [f'## 模型{model}','',f'K={metrics[model]["K"]}，N={metrics[model]["N"]}，费用={metrics[model]["total_cost"]:.4f}。','',
        '### 一级路线','']
    for trip in result['e1_trips']:
        events=' → '.join(f'{e["site_id"]}{"送" if e["kind"]=="d" else "取"}{e["quantity"]}箱({clock(e["start"])})' for e in trip['events'])
        route_lines.append(f'- {trip["batch_id"]}/车{trip["vehicle_id"]}：总仓({clock(trip["departure"])}) → {events} → 总仓卸完({clock(trip["return_complete"])})。')
    route_lines += ['', '### 二级路线','',
        '| 站点 | 车/趟次 | 出发 | 返回卸完 | 送取顺序 |','| --- | --- | --- | --- | --- |']
    for trip in sorted(result['e2_trips'],key=lambda t:(t['satellite_id'],t['vehicle_id'],t['trip_index'])):
        events=' → '.join(f'{orders[e["order_id"]].customer}{"送" if e["kind"]=="d" else "取"}({clock(e["start"])})' for e in trip['events'])
        route_lines.append(f'| {trip["satellite_id"]} | {trip["vehicle_id"]}/{trip["trip_index"]} | '
            f'{clock(trip["departure"])} | {clock(trip["return_complete"])} | {events} |')
    route_lines += ['']
(ROOT/'routes.md').write_text('\n'.join(route_lines)+'\n',encoding='utf-8')
print(json.dumps(dict(results=summary['results'],cost_difference=delta,m8_candidates=summary['m8_candidates'],
    validation=verified),ensure_ascii=False,indent=2))
