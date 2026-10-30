"""Independent final audit and report for the frozen M3 acceleration study."""
from collections import Counter
import hashlib
import json
from pathlib import Path
import statistics
import sys
import xml.etree.ElementTree as ET

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[2]
sys.path.insert(0, str(ROOT))
from models.common.cycle import load_instance
from models.common.cycle.audit import audit_solution
from tools.configuration.loader import load_routing_case


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def counts(path):
    suites = ET.parse(path).getroot().findall('testsuite')
    values = {key: sum(int(s.attrib.get(key, 0)) for s in suites)
              for key in ('tests', 'failures', 'errors', 'skipped')}
    values['passed'] = values['tests']-values['failures']-values['errors']-values['skipped']
    return values


plan, rows, stats = read(OUT / 'plan.json'), read(OUT / 'rows.json'), read(OUT / 'statistics.json')
for relative, expected in plan['hashes'].items():
    assert sha(ROOT / relative) == expected, relative
assert len(rows) == 27 and stats['optimized_optimal'] == stats['optimized_calls'] == 18
audits = []
for row in rows:
    path = OUT / f"{row['case']}_{row['variant']}_s{row['seed']}"
    cfg, fleet = load_routing_case(ROOT / f"data/formal_benchmark27_uniform_7_20_50_v1/model_3/small/{row['case']}/case.json", use_pool_config=False)
    p = load_instance(cfg, fleet)
    result = read(path / 'result.json')
    audit = audit_solution(p, result)
    assert audit['valid'] and audit['periodic_replay']['valid']
    info = result['info']
    assert info['proof_scope'] == 'full_model_3_integer_time_MILP'
    assert row['optimize_calls'][-1]['phase'] == 'full_model'
    if row['optimal']:
        assert info['status'] == 'OPTIMAL' and row['optimize_calls'][-1]['native_status'] == 2
        assert abs(info['best_bound']-audit['objective']) <= 1e-6
    if row['variant'] == 'optimized':
        assert info['route_strengthening']['complete_library']
        assert not info['route_strengthening']['historical_information_used']
        assert info['mip_start_supplied'] == info['solver_config']['mip_start']
    audits.append(dict(case=row['case'], variant=row['variant'], seed=row['seed'],
                       valid=True, periodic_replay_valid=True, objective=audit['objective']))
test_counts = counts(OUT / 'validation.xml')
assert test_counts == dict(tests=112, failures=0, errors=0, skipped=0, passed=112)
prior = ROOT / 'output/experiments/m3_route_bound_trial_20261029_v1/validation.xml'
validation = dict(final_selected_pytest=test_counts,
    selected_scope=['tests/routing/test_model3_route_partition.py',
                    'tests/routing/test_model3_cost_objective.py', 'tests/routing/test_milp_baseline.py'],
    original_plan_reaudits=audits, original_inputs_and_frozen_sources_verified=True,
    optimized_full_native_optimal=18, optimized_runs=18, full_suite_run=False,
    earlier_extended_check=dict(path=str(prior.relative_to(ROOT)), sha256=sha(prior), counts=counts(prior),
        reason='Cross-model isolation test reached M4: current seven-customer demo timed out after 10s without an assignment; M3 isolated exact execution passed. No M4 solver changes.'))
(OUT / 'validation.json').write_text(json.dumps(validation, ensure_ascii=False, indent=2), encoding='utf-8')

new42 = [r for r in rows if r['variant'] == 'optimized' and r['seed'] == 42]
baseline = [r for r in rows if r['variant'] == 'baseline']
lines = ['# 模型3小规模加速：正式入口与冷启动对照', '',
    '2026-10-29。已接入模型3默认精确求解入口。九个七单算例、种子42和101，共18次优化入口运行，'
    '全部由完整原模型的 Gurobi OPTIMAL 与独立业务/两周期审计确认最优，最优成本与历史结果一致。', '',
    f"18次总耗时 {stats['optimized_min_seconds']:.3f}–{stats['optimized_max_seconds']:.3f} 秒，"
    f"中位数 {stats['optimized_median_seconds']:.3f} 秒；合计 {stats['optimized_total_seconds']:.3f} 秒。", '',
    '**求解改动。** 最多9个订单时，完整枚举放松的二级车次任务集合，求整数集合划分下界，'
    '将该下界作为有效成本下界加入完整主模型。成本模式再用新选出的任务分组限制一个完整模型副本，'
    '保留全部原时钟、路径顺序、一级供货和车辆日程，生成新的内部初解。最后回到未限制的完整主模型求解。'
    '副本的任务分组与槽位限制不会写回主模型，副本的最优状态不能单独发布原问题最优证明。', '',
    '所有下界和初解均从当前输入重新生成；没有读取历史方案、历史最优值或历史下界。'
    '正式运行结束后才读取旧成本作核对。两个准备阶段各不超过当时剩余预算的25%，且各最多5秒。'
    '超过9单或枚举未完成时继续使用原模型；不完整的车次库被整体丢弃。', '',
    '**同条件对照。** 原始输入与成本、expanded表达、单线程相同；每次总预算30秒，串行运行。'
    '种子42下前后顺序按算例交替，种子101另行确认优化入口。两种方法都从原始输入开始；'
    '优化入口允许算法在本次预算内新构造内部初解。时间覆盖输入加载、建模、车次枚举、下界、'
    '内部初解、完整主模型求解及独立末端审计；不含进程/运行器启动、环境预检和冻结归档。', '',
    '| 算例 | 原入口42/秒 | 优化入口42/秒 | 优化入口101/秒 | 最优成本 |',
    '|---|---:|---:|---:|---:|']
for case in (f'S{i:02}' for i in range(1, 10)):
    old = next(r for r in baseline if r['case'] == case)
    fresh = next(r for r in new42 if r['case'] == case)
    repeat = next(r for r in rows if r['case'] == case and r['variant'] == 'optimized' and r['seed'] == 101)
    old_time = f"{old['total_wall_seconds']:.3f}" + ('，限时未证优' if not old['optimal'] else '')
    lines.append(f"| {case} | {old_time} | {fresh['total_wall_seconds']:.3f} | {repeat['total_wall_seconds']:.3f} | {fresh['objective']:.9f} |")
lines += ['', '原入口种子42：8/9证最优，S01在30秒内有可行方案但未证优；'
          '优化入口两个种子均9/9证最优。S01的30秒是截断预算，不能当作旧入口真实证明时间计算确定加速倍数。'
          'S06本次29.250→0.914秒，S08本次20.502→2.570秒。', '',
          f"种子42九次合计：原入口 {sum(r['total_wall_seconds'] for r in baseline):.3f} 秒（含1次未证优），"
          f"优化入口 {sum(r['total_wall_seconds'] for r in new42):.3f} 秒（全部证最优）。"
          '部分原本很快的算例增加了准备开销，例如S07本次1.330→4.003秒；主要收益是消除已观察到的长尾，'
          '不宣称每例、每个种子都更快。', '',
          '**数值与正确性。** 保留 MIPGap=0、MIPGapAbs=0；全局证明取自完整主模型的原生状态，'
          '成本与下界按1e-6绝对容差复核。有效下界向负无穷方向取一个浮点步，避免人为留出固定的正差额。'
          '主模型原生 optimize 证据、放松下界和内部初解的状态、工时与工作量分开保存。'
          '27份原业务方案全部再次独立审计通过。', '',
          '**专项测试。** 最终选定三个测试模块112 passed、0 failed、0 errors、0 skipped；未运行全仓库测试。'
          '覆盖独立小规模枚举最优值、完整车次集合的独立排列 oracle、有向非度量网络、零行驶时间、'
          '多箱订单、细化整数时钟、枚举中断回退、内部初解失败、无主模型解不发布证明、审计失败撤销证明，'
          '以及模型3在新进程中的算法/编号隔离。项目运行器预检 READY，实际 Gurobi 11.0.3 整数求解通过。', '',
          '早先额外运行覆盖全部模型的隔离测试时，模型4当前演示在10秒内没有可行分仓而失败；'
          '该次记录为111通过、1失败，保留于诊断目录的 validation.xml。本次逐模型定位确认模型3通过，'
          '没有修改模型4；最终专项范围及原记录哈希均在 validation.json 中列出。', '',
          '**适用范围与使用。** 当前加速仅对最多9单的小实例启用；20/50单的运行速度未验证。'
          '公开服务和CLI的模型3精确调用默认生效。Python入口 solve_exact(..., route_strengthening=False) '
          '可恢复原基线；build_model仍只构造原完整模型。', '',
          'plan.json、source_inputs.zip：运行前冻结的顺序、输入与源码；runs.csv、rows.json、statistics.json：逐次结果；'
          '每次目录的 result.json、independent_audit.json、summary.json 及分阶段日志：原生证据；'
          'validation.xml、validation.json：实际测试与最终独立复核；evidence_manifest.json：归档校验。', '']
(OUT / 'report.md').write_text('\n'.join(lines), encoding='utf-8')
hashes = {p.relative_to(OUT).as_posix(): sha(p) for p in OUT.rglob('*')
          if p.is_file() and '__pycache__' not in p.parts and p.name != 'evidence_manifest.json'}
(OUT / 'evidence_manifest.json').write_text(json.dumps(dict(files=hashes), ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps(dict(optimized_optimal=18, audits=len(audits), pytest=test_counts,
                      min_seconds=stats['optimized_min_seconds'], max_seconds=stats['optimized_max_seconds']), ensure_ascii=False))
