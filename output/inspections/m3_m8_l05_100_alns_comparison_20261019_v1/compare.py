"""Replay M3/M8 routes and price both under the same acquisition/operating contract."""
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import sys

PROJECT = Path.cwd()
sys.path.insert(0, str(PROJECT))
from models.common.cycle import load_instance
from models.common.cycle.audit import audit_solution
from models.common.cycle.joint import lift_result
from models.common.cycle.calendar import attach_calendar_decisions
from models.cost.comparison import evaluate_comparison_cost

ROOT = Path(__file__).resolve().parent
M8_ROOT = PROJECT / 'output/inspections/m8_l05_100_newfleet_20261019_v1'
def read(path):
    return json.loads(path.read_text(encoding='utf-8'))
def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')

runs = {3: ROOT / 'm3_alns', 8: M8_ROOT / 'warm/alns'}
metrics = {}
plans = {}
manifests = {}
problems = {}
for model, run in runs.items():
    manifest = read(run / 'manifest.json')
    for filename, expected in manifest['source_sha256'].items():
        if hashlib.sha256((PROJECT / filename).read_bytes()).hexdigest() != expected:
            raise ValueError(f'Source changed: {filename}')
    for filename, expected in manifest['input_sha256'].items():
        if hashlib.sha256(Path(filename).read_bytes()).hexdigest() != expected:
            raise ValueError(f'Input changed: {filename}')
    problem = load_instance(manifest['config'], manifest['fleet'])
    result = read(run / 'result.json')
    checked = audit_solution(problem, result)
    assert checked['valid'] and checked['periodic_replay']['valid']
    cost = evaluate_comparison_cost(problem, result, manifest['config']['economic_config'], audit=checked)
    parts = cost['components']
    acquisition = parts['container_pool']
    metrics[model] = dict(model=model, customers=len(problem.orders),
        K=result['cycle_batch_count'] if model == 8 else len(problem.operating_policy.batches),
        N=cost['container_pool_size'], objective_mode='cost' if model == 8 else 'distance',
        solver_objective=result['info']['objective'],
        D1=checked['distances']['e1'], D2=checked['distances']['e2'],
        distance=sum(checked['distances'].values()),
        acquisition=acquisition, transport=parts['e1_distance'] + parts['e2_distance'],
        other_operating=sum(value for key, value in parts.items()
                            if key not in ('container_pool', 'e1_distance', 'e2_distance')),
        recurring_cost=cost['total'] - acquisition, first_cycle_cost=cost['total'],
        e1_vehicles=checked['e1_vehicle_count'], e2_vehicles=checked['e2_vehicle_count'],
        e1_trips=checked['e1_trip_count'], e2_trips=checked['e2_trip_count'],
        runtime=result['info']['runtime'], budget_seconds=120, seed=42, warm_start=True,
        valid=True, periodic_replay=True, gurobi_checked=False, proven_optimal=False,
        result_file=str(run / 'result.json'))
    problems[model], plans[model], manifests[model] = problem, result, manifest
    write(ROOT / f'm{model}_comparison_cost.json', cost)

def order_contract(order):
    return {key: value for key, value in asdict(order).items() if key not in ('batch', 'allowed_batch_ids')}
assert [order_contract(o) for o in problems[3].orders] == [order_contract(o) for o in problems[8].orders]
assert vars(problems[3].fleet) == vars(problems[8].fleet)
assert manifests[3]['source_sha256'] == manifests[8]['source_sha256']
assert (Path(manifests[3]['config']['economic_config']).read_bytes()
        == Path(manifests[8]['config']['economic_config']).read_bytes())

delta = {key: metrics[8][key] - metrics[3][key] for key in
         ('N', 'D1', 'D2', 'distance', 'acquisition', 'transport', 'recurring_cost', 'first_cycle_cost')}
m8_problem = problems[8]
one_batch = m8_problem.fixed_calendar(1)
assignment = {order.id: 'b1' for order in one_batch.orders}
cutoffs = {'b1': problems[3].operating_policy.batches[0].cutoff}
projected = lift_result(one_batch, plans[3], assignment, 100, 'comparison_replay', cutoffs)
attach_calendar_decisions(m8_problem, projected, 1, m8_problem.balanced_boundaries(1))
projected['audit'] = audit_solution(m8_problem, projected)
assert projected['audit']['valid'] and projected['audit']['periodic_replay']['valid']
assert abs(projected['info']['objective'] - metrics[3]['first_cycle_cost']) < 1e-6
write(ROOT / 'm3_replayed_as_m8_K1.json', projected)
replay = dict(K=1, N=100, cost=projected['info']['objective'], valid=True,
              periodic_replay=True, source='M3 ALNS route re-priced and replayed within M8; no new ALNS run.')
repeated = [dict(H=h, M3=metrics[3]['acquisition'] + h * metrics[3]['recurring_cost'],
                 M8=metrics[8]['acquisition'] + h * metrics[8]['recurring_cost'])
            for h in (1, 2, 10, 100)]
comparison = dict(case='L05', customers=100, same_physical_orders=True, same_fleet=True,
    same_source_revision=True, same_cost_coefficients=True, metrics=list(metrics.values()),
    M8_minus_M3=delta, repeated_identical_cycles=repeated, m3_replayed_within_m8=replay,
    limitations=['One seed, two existing feasible witness initializations, 120 seconds per model.',
                 'M3 minimizes unweighted distance; M8 minimizes acquisition plus single-cycle operating cost.',
                 'M8 divides its total budget across K; M3 has a fixed K=1.',
                 'No optimality proof or general performance ranking; Gurobi not checked.',
                 'Repeated-cycle projection retains each saved route, with acquisition charged once.'])
write(ROOT / 'comparison.json', comparison)

lines = ['# L05：100 顾客，模型 3 与模型 8 ALNS 比较', '',
    '同一交错取送数据集、一客一箱、固定分仓、更新后的大规模车辆参数。'
    '已逐项核对顾客物理字段、车辆、经济系数与源代码快照一致。',
    '两次均为有效可行初解上的 ALNS，预算120秒、seed=42。初解来自同一个数据集的构造见证：'
    '模型3使用单批见证；模型8使用3批见证并先将箱池减为84。'
    '模型3公共入口未暴露初解参数，本实验仅在当前进程中向其已有搜索函数的 initial_result 参数传入初解，未修改源码。',
    '模型8此前另有300秒冷启动，未找到可行解；该轮不合并到这里的120秒比较。',
    '模型3优化两级距离之和；模型8优化购箱与单周期运营成本。下表成本全部用同一经济配置重新核算，'
    '不能直接比较原始求解目标值。模型8还需要在不同K之间分配时间。', '',
    '| 指标 | 模型3 ALNS | 模型8 ALNS |', '| --- | ---: | ---: |']
rows = [('批次数', 'K', 0), ('箱池数量', 'N', 0), ('一级距离', 'D1', 4),
        ('二级距离', 'D2', 4), ('总距离', 'distance', 4),
        ('首次购箱费用', 'acquisition', 4), ('每周期两级运输费用', 'transport', 4),
        ('每周期其他运营费用', 'other_operating', 4),
        ('每周期运营费用合计', 'recurring_cost', 4),
        ('首次购箱＋1周期运营费用', 'first_cycle_cost', 4),
        ('一级车辆数', 'e1_vehicles', 0), ('一级趟数', 'e1_trips', 0),
        ('二级车辆数', 'e2_vehicles', 0), ('二级趟数', 'e2_trips', 0),
        ('求解耗时/秒', 'runtime', 3)]
for label, key, digits in rows:
    lines.append(f'| {label} | {metrics[3][key]:.{digits}f} | {metrics[8][key]:.{digits}f} |')
lines += ['', '距离为算例坐标单位，费用为合成单位；未做真实货币或距离标定。'
    '购箱费用与 initial_pool_investment 是同一笔费用，未重复相加。',
    f'相对本次模型3结果，模型8少用 {-delta["N"]} 个箱子；'
    f'单周期含购箱总成本差为 {delta["first_cycle_cost"]:+.4f}，每周期运营费用差为 {delta["recurring_cost"]:+.4f}。'
    '正号表示模型8更高。', '',
    '## 模型3路线在模型8中的合法性', '',
    f'已将模型3的实际路线原样重放为模型8的K=1、N=100方案，费用 {replay["cost"]:.4f}，'
    '全部检查及连续两个周期重放通过。这不是新增一次模型8 ALNS求解。',
    '因此，模型8允许本次更便宜的单批方案；此前672.0933是其有限预算ALNS返回的较差可行解，'
    '不能解释为模型8的约束必然导致成本更高。该算例提示应检查模型8的单批初解构造与批次数搜索，'
    '并用有效单批方案作为比较基线；本实验未修改任何算法。', '',
    '## 重复相同运营周期的成本投影', '',
    '保持两条已保存路线不变，暂不考虑额外购箱、残值或折现；'
    '已配置的清洗与磨损费用随每周期100次使用计算。'
    '总费用=首次购箱费用+H×每周期运营费用。这是事后核算，没有重新优化多周期目标。', '',
    '| 周期数 H | 模型3总费用 | 模型8总费用 |', '| ---: | ---: | ---: |']
for row in repeated:
    lines.append(f'| {row["H"]} | {row["M3"]:.4f} | {row["M8"]:.4f} |')
lines += ['', '两份结果均通过独立重放，包括时间窗、先送后取及最低使用时间、车辆衔接、载重体积、'
    '箱量守恒、清洗与连续两个周期闭环。只运行ALNS，未检查Gurobi、未做精确求解，未证明最优。',
    '单次种子和有限预算的结果不足以证明模型的一般优劣。', '', '## 模型3实际路线', '']

result = plans[3]
problem = problems[3]
orders = {order.id: order for order in problem.orders}
factor = result['time_tick_seconds'] / result.get('time_tick_subdivision', 1)
def clock(value):
    seconds = round(value * factor)
    return f'{seconds // 3600:02d}:{seconds // 60 % 60:02d}:{seconds % 60:02d}'
for trip in result['e1_trips']:
    events = ' → '.join(f'{event["site_id"]}{"送" if event["kind"] == "d" else "取"}'
        f'{event["quantity"]}箱({clock(event["start"])})' for event in trip['events'])
    lines.append(f'- 一级车{trip["vehicle_id"]}：总仓({clock(trip["departure"])}) → '
        f'{events} → 总仓卸完({clock(trip["return_complete"])})。')
lines += ['', '二级车辆编号在各站内独立计数，各路线从所属站出发并返回；括号为服务开始时间。', '',
          '| 站点 | 车辆/趟次 | 出发 | 返回卸完 | 顾客送取顺序 |', '| --- | --- | --- | --- | --- |']
for trip in sorted(result['e2_trips'], key=lambda t: (t['satellite_id'], t['vehicle_id'], t['trip_index'])):
    events = ' → '.join(f'{orders[event["order_id"]].customer}'
        f'{"送" if event["kind"] == "d" else "取"}({clock(event["start"])})'
        for event in trip['events'])
    lines.append(f'| {trip["satellite_id"]} | {trip["vehicle_id"]}/{trip["trip_index"]} | '
                 f'{clock(trip["departure"])} | {clock(trip["return_complete"])} | {events} |')
lines += ['', f'[模型8完整路线与分批安排]({M8_ROOT.as_posix()}/routes_and_batches.md)', '']
(ROOT / 'comparison.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')
print(json.dumps(comparison, ensure_ascii=False, indent=2))
