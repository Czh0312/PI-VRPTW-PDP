"""Summarize audited native and fixed-K comparisons without mixing proof scopes."""
import hashlib
import json
import math
from pathlib import Path


root = Path(__file__).resolve().parent


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def fmt(value, digits=4):
    return '—' if value is None else f'{value:.{digits}f}'


def certified_percent(value):
    """Round a guaranteed improvement down instead of overstating the bound."""
    return fmt(None if value is None else math.floor(100*value)/100, 2)


def status(result):
    if result['info']['objective'] is None:
        return '未取得可行解' if result['info']['status'] == 'TIME_LIMIT' else result['info']['status']
    return 'K=2内已证明最优' if result['info']['proven_optimal'] else '限时可行'


rows, jobs = [], []
for case in ('S03', 'S04'):
    folder = root/case
    comparison = read(folder/'comparison.json')
    runs = {}
    for model in (8, 9):
        for scope, suffix in (('native', ''), ('K2', '_fixedK2')):
            path = folder/f'M{model}{suffix}_seed42_300s/result.json'
            result = read(path)
            runs[model, scope] = result
            jobs.append(dict(case=case, model=model, scope=scope, seed=42,
                status=result['info']['status'], objective=result['info']['objective'],
                solver_audit_valid=result.get('audit', {}).get('valid'),
                result_sha256=hashlib.sha256(path.read_bytes()).hexdigest()))
            if scope == 'native':
                if (not result['info'].get('complete_count_domain')
                        or result['info'].get('batch_count_candidates') != list(range(1, 8))):
                    raise ValueError('A native-domain bound must cover all seven declared counts')
                expected = comparison['results'][str(model)]['result_sha256']
                if expected != jobs[-1]['result_sha256']:
                    raise ValueError('Comparison is stale; run the pair analyzer again')
    m8, m9 = runs[8, 'K2'], runs[9, 'K2']
    diag8, diag9 = comparison['fixed_K2_baseline'], comparison['fixed_K2_diagnostic']
    for diag, result in ((diag8, m8), (diag9, m9)):
        value = result['info']['objective']
        if value is not None:
            if not diag['native_domain_feasibility_verified']:
                raise ValueError('Fixed-K schedule has not passed native-domain audit')
            if not math.isclose(diag['objective'], value, abs_tol=1e-7):
                raise ValueError('Diagnostic comparison is stale')
    c8, c9 = m8['info']['objective'], m9['info']['objective']
    lower8 = runs[8, 'native']['info'].get('best_bound')
    lower9 = runs[9, 'native']['info'].get('best_bound')
    certified = c9 is not None and lower8 is not None and c9 < lower8-1e-6
    fixed_lower8 = m8['info'].get('best_bound')
    fixed_certified = c9 is not None and fixed_lower8 is not None and c9 < fixed_lower8-1e-6
    if c9 is not None and lower9 is not None and lower9 > c9+1e-6:
        raise ValueError('Native M9 bound exceeds the audited diagnostic upper bound')
    rows.append(dict(case=case, customers=7, satellites=2, seed=42,
        native_m8_status=runs[8, 'native']['info']['status'],
        native_m9_status=runs[9, 'native']['info']['status'],
        native_m8_cost=runs[8, 'native']['info']['objective'],
        native_m9_cost=runs[9, 'native']['info']['objective'],
        native_m8_lower_bound=lower8, native_m9_lower_bound=lower9,
        fixed_K2_m8_cost=c8, fixed_K2_m9_cost=c9,
        fixed_K2_m8_status=status(m8), fixed_K2_m9_status=status(m9),
        fixed_K2_m8_gap=m8['info'].get('mip_gap'), fixed_K2_m9_gap=m9['info'].get('mip_gap'),
        fixed_K2_observed_cost_reduction=None if c8 is None or c9 is None else c8-c9,
        fixed_K2_observed_reduction_percent=None if c8 is None or c9 is None else 100*(c8-c9)/c8,
        native_improvement_certified=certified,
        native_minimum_improvement_percent=100*(lower8-c9)/lower8 if certified and lower8 > 0 else None,
        fixed_K2_improvement_certified=fixed_certified,
        fixed_K2_minimum_improvement_percent=(100*(fixed_lower8-c9)/fixed_lower8
            if fixed_certified and fixed_lower8 > 0 else None),
        native_m9_relative_bound_gap=None if c9 is None or lower9 is None else (c9-lower9)/c9,
        m8_pool=m8.get('container_pool_size'), m9_pool=m9.get('container_pool_size'),
        m8_assignment=diag8['assignment'], m9_assignment=diag9.get('assignment'),
        m8_cost_components=m8.get('cost_components'), m9_cost_components=m9.get('cost_components'),
        m8_native_domain_audit=diag8['native_domain_feasibility_verified'],
        m9_native_domain_audit=diag9['native_domain_feasibility_verified']))

summary = dict(date='2026-10-30', dataset='formal_benchmark27_uniform_7_20_50_v1',
    cases=['S03', 'S04'], seeds=[42], primary_domain=list(range(1, 8)), diagnostic_domain=[2],
    budget_seconds_per_solve=300, threads_per_solve=1, formulation='compact',
    maximum_concurrent_jobs=2, actual_solve_count=len(jobs),
    scope='Two cases, one seed; native all-K and paired fixed-K=2 runs reported separately.',
    results=rows, runs=jobs)
(root/'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2, allow_nan=False)+'\n', encoding='utf-8')

lines = ['# M8/M9新增小规模S03、S04精确对照', '',
    '本轮使用现有统一7/20/50数据集的S03、S04，各7客户、2前置仓，均使用单一种子42。',
    '原始网络、订单、车辆、费率、箱池范围和秒级时间域保持原样；输入与源码哈希配对检查通过。',
    '每次求解300秒、单线程、等价compact表达，无外部初解。两算例并行，至多同时两次单线程求解。',
    '本轮没有改动求解器。所记录时间受到并行负载影响，不据此作严格速度比结论。', '',
    '## 原生全K域结果', '',
    '| 算例 | M8状态/成本 | M8全域下界 | M9状态/成本 | M9全域下界 |',
    '| --- | --- | ---: | --- | ---: |']
for row in rows:
    lines.append(f'| {row["case"]} | {row["native_m8_status"]} / {fmt(row["native_m8_cost"])} | '
        f'{fmt(row["native_m8_lower_bound"])} | {row["native_m9_status"]} / {fmt(row["native_m9_cost"])} | {fmt(row["native_m9_lower_bound"])} |')
lines += ['', 'K=1～7的原生模型每次运行300秒。限时没有可行解不等于不可行；没有将固定K结果冒充全域求解结果。', '',
    '## 双方固定K=2的配对诊断', '',
    '| 算例 | M8成本 | M9成本 | 本轮方案成本下降 | M8/M9箱池 | M8 gap | M9 gap |',
    '| --- | ---: | ---: | ---: | --- | ---: | ---: |']
for row in rows:
    lines.append(f'| {row["case"]} | {fmt(row["fixed_K2_m8_cost"])} | {fmt(row["fixed_K2_m9_cost"])} | '
        f'{fmt(row["fixed_K2_observed_reduction_percent"], 2)}% | {row["m8_pool"]} / {row["m9_pool"]} | '
        f'{fmt(None if row["fixed_K2_m8_gap"] is None else 100*row["fixed_K2_m8_gap"], 2)}% | '
        f'{fmt(None if row["fixed_K2_m9_gap"] is None else 100*row["fixed_K2_m9_gap"], 2)}% |')
lines += ['', '以上成本与gap只属于固定K=2的这次诊断。M8、M9均各获额外300秒预算；有时限结果只代表已找到的可行方案。',
    '两批次的开始、结束、截点、箱池、路线和服务时刻仍由模型优化，K=2是唯一额外的业务限制。',
    '有成本的诊断结果均已分别在对应原生K=1～7域中重新独立审计，并执行连续两周期重放。']
for row in rows:
    lines += ['', f'## {row["case"]}的选仓与证明范围', '',
        f'M8固定K=2状态：{row["fixed_K2_m8_status"]}；M9固定K=2状态：{row["fixed_K2_m9_status"]}。',
        f'在原生域中独立复核：M8={row["m8_native_domain_audit"]}，M9={row["m9_native_domain_audit"]}。']
    if row['native_improvement_certified']:
        lines += [f'M9可行成本{fmt(row["fixed_K2_m9_cost"])}低于M8原生全域下界{fmt(row["native_m8_lower_bound"])}。',
            f'因此可确认原生域中存在自由选仓改善，至少{certified_percent(row["native_minimum_improvement_percent"])}%；该数字来自界限，不是多种子统计。']
    else:
        lines += ['本轮尚未通过“M9可行成本低于M8全域下界”认证原生域的严格改善；固定K方案差异仍须按其声明范围解释。']
    if row['fixed_K2_improvement_certified']:
        lines += [f'在固定K=2域中，M9方案低于M8同域下界，可认证至少{certified_percent(row["fixed_K2_minimum_improvement_percent"])}%的改善。']
    if row['m9_assignment']:
        lines += ['', '| 客户 | M8固定仓 | M9所选仓 |', '| --- | --- | --- |']
        for customer, site in sorted(row['m8_assignment'].items()):
            lines.append(f'| {customer} | {site} | {row["m9_assignment"][customer]} |')
    if row['m8_cost_components'] and row['m9_cost_components']:
        lines += ['', '| 分项 | M8 K=2 | M9 K=2 |', '| --- | ---: | ---: |']
        for name in ('container_pool', 'e1_distance', 'e2_distance', 'cleaning', 'tracking', 'container_wear'):
            lines.append(f'| {name} | {fmt(row["m8_cost_components"][name])} | {fmt(row["m9_cost_components"][name])} |')
lines += ['', '## 范围与复现', '',
    '本轮只新增S03、S04、种子42，不是全部9个小例，也不构成多种子稳定性验证。费用单位沿用原数据synthetic_units。',
    f'实际求解{len(jobs)}次；每轮预检检查了Gurobi接口、许可证初始化和实际整数求解。没有运行额外的pytest套件。', '',
    '- [S03详细对照](S03/README.md)、[S04详细对照](S04/README.md)。',
    '- [结构化汇总](summary.json)、[实验设置](plan.json)。',
    '- 各运行目录保存结果、日志、输入、源码快照与哈希；各算例independent_audits.json保存重新独立审计。',
    '- 前轮[S02参考](../model89_S02_exact_20261030_v1/README.md)另行保留，未计入本轮求解次数。']
(root/'README.md').write_text('\n'.join(lines)+'\n', encoding='utf-8')
print(json.dumps(rows, ensure_ascii=False, indent=2, allow_nan=False))
