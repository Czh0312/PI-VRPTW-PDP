"""Independently audit and compare two saved, matched M8/M9 exact runs."""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path

from models.common.cycle.assignment import input_assignment
from models.common.cycle.audit import audit_solution
from models.common.cycle.calendar import attach_calendar_decisions
from models.common.cycle.instance import load_instance
from tools.configuration.loader import PROJECT_ROOT


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)+'\n', encoding='utf-8')


def number(value, digits=6):
    return '—' if value is None else f'{value:.{digits}f}'


def load_fixed_baseline(root, native_manifest):
    """Audit an optional M8 K=2 run without transferring its optimality proof."""
    folder = root/'M8_fixedK2_seed42_300s'
    if not (folder/'result.json').is_file():
        return None, None, None
    manifest, saved = read(folder/'manifest.json'), read(folder/'result.json')
    for path, expected_hash in manifest['input_sha256'].items():
        if hashlib.sha256(Path(path).read_bytes()).hexdigest() != expected_hash:
            raise ValueError(f'Baseline diagnostic input changed: {path}')
    expected = deepcopy(native_manifest['config'])
    expected['joint_batch_options']['batch_count_candidates'] = [2]
    expected.pop('case_name', None)
    observed = {key: value for key, value in manifest['config'].items()
                if key not in {'case_name', 'diagnostic_scope'}}
    if (observed != expected or manifest['fleet'] != native_manifest['fleet']
            or manifest['source_sha256'] != native_manifest['source_sha256']):
        raise ValueError('M8 diagnostic changed more than the declared K=2 domain')
    restricted = load_instance(manifest['config'], manifest['fleet'])
    info = saved['info']
    audit = audit_solution(restricted, saved) if info.get('objective') is not None else None
    full_audit = None
    if audit is not None:
        if not audit['valid'] or not audit['periodic_replay']['valid']:
            raise ValueError('M8 fixed-K replay failed')
        full_problem = load_instance(native_manifest['config'], native_manifest['fleet'])
        copied = deepcopy(saved)
        copied['info'].update(proven_optimal=False, proven_infeasible=False,
            best_bound=None, mip_gap=None, proof_scope='feasibility_projection_only')
        attach_calendar_decisions(full_problem, copied, saved['cycle_batch_count'], saved['batch_boundaries'])
        full_audit = audit_solution(full_problem, copied)
        if not full_audit['valid'] or not full_audit['periodic_replay']['valid']:
            raise ValueError('M8 diagnostic is not feasible in the native M8 domain')
    record = dict(scope='K=2 only', objective=info.get('objective'), status=info['status'],
        proven_optimal=info['proven_optimal'], best_bound=info.get('best_bound'), mip_gap=info.get('mip_gap'),
        audit_valid=bool(audit and audit['valid']),
        native_domain_feasibility_verified=bool(full_audit and full_audit['valid']),
        batch_count=saved.get('cycle_batch_count'), pool=saved.get('container_pool_size'),
        cost_components=saved.get('cost_components'), assignment=input_assignment(restricted),
        reference_assignment=input_assignment(restricted), runtime_seconds=info.get('runtime'),
        variables=info.get('variables'), constraints=info.get('constraints'))
    return record, audit, full_audit


def analyze(root):
    root = Path(root)
    if not root.is_absolute():
        root = PROJECT_ROOT/root
    records, manifests, audits = {}, {}, {}
    for model in (8, 9):
        folder = root/f'M{model}_seed42_300s'
        manifest = read(folder/'manifest.json')
        result = read(folder/'result.json')
        config, fleet = manifest['config'], manifest['fleet']
        if config['model'] != model or config['solver'] != 'exact' or config['seed'] != 42:
            raise ValueError('Unexpected model, solver or seed in saved manifest')
        for path, expected_hash in manifest['input_sha256'].items():
            if hashlib.sha256(Path(path).read_bytes()).hexdigest() != expected_hash:
                raise ValueError(f'Input changed since solve: {path}')
        problem = load_instance(config, fleet)
        info = result['info']
        audit = audit_solution(problem, result) if info.get('objective') is not None else None
        if audit is not None and not audit['valid']:
            raise ValueError(f'M{model} independent replay failed: {audit["errors"]}')
        if audit is not None and not math.isclose(audit['objective'], info['objective'], abs_tol=1e-6):
            raise ValueError('Saved objective differs from independent replay')
        if audit is not None and not audit['periodic_replay']['valid']:
            raise ValueError('Two-cycle replay failed')
        calendar = None if audit is None else problem.selected_problem(result)
        records[model] = dict(
            model=model, source_case=config['base_case_id'], customers=len({o.customer for o in problem.orders}),
            orders=len(problem.orders), satellites=len(problem.satellites), seed=config['seed'],
            budget_seconds=config['exact_time'], status=info['status'], objective=info.get('objective'),
            best_bound=info.get('best_bound'), mip_gap=info.get('mip_gap'),
            proven_optimal=info['proven_optimal'], proven_infeasible=info['proven_infeasible'],
            complete_count_domain=info.get('complete_count_domain'),
            batch_count_candidates=info.get('batch_count_candidates'),
            batch_count=result.get('cycle_batch_count'), pool=result.get('container_pool_size'),
            assignment=result.get('assignment', input_assignment(problem)) if audit else None,
            reference_assignment=input_assignment(problem),
            order_batch_assignment=result.get('order_batch_assignment'),
            batches_minutes=None if calendar is None else [dict(id=b['id'],
                start=calendar.operating_policy.minutes(b['start']),
                cutoff=calendar.operating_policy.minutes(b['cutoff']),
                end=calendar.operating_policy.minutes(b['end'])) for b in result['batches']],
            distances=None if audit is None else audit['distances'],
            cost_components=result.get('cost_components'), audit_valid=bool(audit and audit['valid']),
            periodic_replay_valid=bool(audit and audit['periodic_replay']['valid']),
            clean_stock=None if audit is None else audit['clean_stock'],
            terminal_state=None if audit is None else audit['terminal_state'],
            runtime_seconds=info.get('runtime'), build_seconds=info.get('build_time'),
            mip_seconds=info.get('mip_runtime'), variables=info.get('variables'),
            constraints=info.get('constraints'), solver_config=info.get('solver_config'),
            result_sha256=hashlib.sha256((folder/'result.json').read_bytes()).hexdigest())
        manifests[model], audits[model] = manifest, audit

    left, right = manifests[8], manifests[9]
    identity_fields = {'model', 'case_name'}
    left_config = {k: v for k, v in left['config'].items() if k not in identity_fields}
    right_config = {k: v for k, v in right['config'].items() if k not in identity_fields}
    if left_config != right_config or left['fleet'] != right['fleet']:
        raise ValueError('The paired runs have different business/solver configurations')
    if left['source_sha256'] != right['source_sha256']:
        raise ValueError('Source files changed between the two runs')
    for key in ('data', 'orders', 'routing_config', 'operating_policy', 'economic_config'):
        path = str((PROJECT_ROOT/left['config'][key]).resolve())
        if left['input_sha256'][path] != right['input_sha256'][path]:
            raise ValueError(f'Mismatched physical input: {key}')

    m8, m9 = records[8], records[9]
    comparable = m8['audit_valid'] and m9['audit_valid']
    saving = m8['objective']-m9['objective'] if comparable else None
    percent = 100*saving/m8['objective'] if comparable and m8['objective'] else None
    changes = [] if not comparable else [dict(customer=c, before=s, after=m9['assignment'][c])
        for c, s in sorted(m8['assignment'].items()) if s != m9['assignment'][c]]
    summary = dict(case=m8['source_case'], seed=42, paired_input_hashes_equal=True,
        paired_source_hashes_equal=True, effective_config_differences=['model', 'case_name'],
        results=records, audited_incumbent_cost_reduction=saving,
        audited_incumbent_reduction_percent=percent,
        both_proven_optimal=m8['proven_optimal'] and m9['proven_optimal'],
        improvement_certified_against_m8_bound=bool(comparable and m8['best_bound'] is not None
            and m9['objective'] < m8['best_bound']-1e-6), assignment_changes=changes,
        scope='One existing small benchmark instance, one seed, two direct-Gurobi solves.')
    diagnostic = None
    baseline_diagnostic, baseline_audit, baseline_full_audit = load_fixed_baseline(root, left)
    reference = baseline_diagnostic if baseline_diagnostic and baseline_diagnostic['audit_valid'] else m8
    reference_label = 'M8固定K=2诊断' if reference is baseline_diagnostic else 'M8全域结果'
    if baseline_diagnostic is not None:
        summary['fixed_K2_baseline'] = baseline_diagnostic
        audits.update(M8_fixed_K2=baseline_audit, M8_fixed_K2_replayed_in_native_domain=baseline_full_audit)
    diagnostic_folder = root/'M9_fixedK2_seed42_300s'
    if (diagnostic_folder/'result.json').is_file():
        saved = read(diagnostic_folder/'result.json')
        manifest = read(diagnostic_folder/'manifest.json')
        for path, expected_hash in manifest['input_sha256'].items():
            if hashlib.sha256(Path(path).read_bytes()).hexdigest() != expected_hash:
                raise ValueError(f'Diagnostic input changed since solve: {path}')
        if manifest['source_sha256'] != right['source_sha256']:
            raise ValueError('Diagnostic used a different solver source')
        expected_config = deepcopy(right['config'])
        expected_config['joint_batch_options']['batch_count_candidates'] = [2]
        observed_config = {k: v for k, v in manifest['config'].items()
                           if k not in {'case_name', 'diagnostic_scope'}}
        expected_config.pop('case_name', None)
        if expected_config != observed_config or manifest['fleet'] != right['fleet']:
            raise ValueError('Diagnostic changed more than the declared K=2 restriction')
        restricted = load_instance(manifest['config'], manifest['fleet'])
        restricted_audit = audit_solution(restricted, saved) if saved['info'].get('objective') is not None else None
        full_audit = None
        if restricted_audit is not None:
            if not restricted_audit['valid'] or not restricted_audit['periodic_replay']['valid']:
                raise ValueError('Fixed-K diagnostic replay failed')
            full_problem = load_instance(right['config'], right['fleet'])
            full_copy = deepcopy(saved)
            # Only transfer feasibility. A fixed-K proof cannot become a
            # proof over all native counts when the calendar contract changes.
            full_copy['info'].update(proven_optimal=False, proven_infeasible=False,
                                     best_bound=None, mip_gap=None,
                                     proof_scope='feasibility_projection_only')
            attach_calendar_decisions(full_problem, full_copy, saved['cycle_batch_count'], saved['batch_boundaries'])
            full_audit = audit_solution(full_problem, full_copy)
            if not full_audit['valid'] or not full_audit['periodic_replay']['valid']:
                raise ValueError('Diagnostic is not feasible in the original full M9 domain')
        info = saved['info']
        cost = info.get('objective')
        native_bound = m9['best_bound']
        diagnostic_comparable = cost is not None and reference['audit_valid']
        diagnostic_reduction = reference['objective']-cost if diagnostic_comparable else None
        diagnostic_percent = (100*diagnostic_reduction/reference['objective']
                              if diagnostic_comparable and reference['objective'] else None)
        if cost is not None and native_bound is not None and cost < native_bound-1e-6:
            raise ValueError('Fixed-K incumbent contradicts the full-domain bound')
        diagnostic = dict(scope='K=2 only; not a native all-K optimality proof',
            status=info['status'], objective=cost, fixed_K_best_bound=info.get('best_bound'),
            fixed_K_mip_gap=info.get('mip_gap'), fixed_K_proven_optimal=info['proven_optimal'],
            native_domain_feasibility_verified=bool(full_audit and full_audit['valid']),
            native_domain_lower_bound=native_bound, native_domain_feasible_upper_bound=cost,
            native_domain_relative_bound_gap=None if cost is None or native_bound is None else (cost-native_bound)/cost,
            reduction_vs_M8_incumbent=diagnostic_reduction,
            reduction_percent_vs_M8_incumbent=diagnostic_percent,
            comparison_reference_scope=reference_label,
            reduction_vs_M8_optimum=diagnostic_reduction if reference is m8 and m8['proven_optimal'] else None,
            reduction_percent_vs_M8_optimum=diagnostic_percent if reference is m8 and m8['proven_optimal'] else None,
            improvement_certified_against_M8_bound=bool(cost is not None and m8['best_bound'] is not None
                and cost < m8['best_bound']-1e-6),
            assignment=saved.get('assignment'), pool=saved.get('container_pool_size'),
            batch_count=saved.get('cycle_batch_count'), cost_components=saved.get('cost_components'),
            distances=None if full_audit is None else full_audit['distances'],
            runtime_seconds=info.get('runtime'), build_seconds=info.get('build_time'), mip_seconds=info.get('mip_runtime'),
            variables=info.get('variables'), constraints=info.get('constraints'), seed=42, budget_seconds=300)
        summary.update(fixed_K2_diagnostic=diagnostic,
                       scope='One existing small case, seed 42; native all-K solves and separately recorded fixed-K=2 diagnostics.')
        audits.update(fixed_K2=restricted_audit, fixed_K2_replayed_in_native_domain=full_audit)
    write(root/'comparison.json', summary)
    write(root/'independent_audits.json', audits)

    lines = [f'# 现有小规模 {m8["source_case"]}：M8 与 M9 精确求解对照', '',
        f'使用当前统一7/20/50数据集的{m8["source_case"]}：{m8["customers"]}位客户、{m8["orders"]}笔订单、{m8["satellites"]}个前置仓。',
        '两个模型使用相同输入、费用、车辆、箱池范围、K=1～7与原生时间域。',
        '种子42，单线程，等价compact表达，每模型总预算300秒；未使用外部初解。',
        '输入与求解代码哈希配对检查通过，仅模型编号和案例名称不同。', '',
        '| 指标 | M8 固定分仓 | M9 自由选仓 |', '| --- | ---: | ---: |']
    metrics = [('状态', 'status'), ('有效方案成本', 'objective'), ('全局下界', 'best_bound'),
        ('最优性间隙', 'mip_gap'), ('已证明最优', 'proven_optimal'), ('配送批次数', 'batch_count'),
        ('实体箱池', 'pool'), ('建模时间/秒', 'build_seconds'), ('MIP时间/秒', 'mip_seconds'),
        ('总算法时间/秒', 'runtime_seconds'), ('变量数', 'variables'), ('约束数', 'constraints'),
        ('独立审计通过', 'audit_valid'), ('连续两周期通过', 'periodic_replay_valid')]
    for label, key in metrics:
        vals = []
        for row in (m8, m9):
            value = row.get(key)
            vals.append('尚无可复核方案' if key in ('audit_valid', 'periodic_replay_valid') and not row['audit_valid']
                        else '—' if value is None else f'{100*value:.4f}%' if key == 'mip_gap'
                        else number(value) if isinstance(value, float) else str(value))
        lines.append(f'| {label} | {vals[0]} | {vals[1]} |')
    lines += ['', '成本单位沿用原数据的synthetic_units，不改动费率，不作为人民币经营费用。']
    if comparable:
        lines += ['', f'本次已审计方案成本变化：M8−M9={saving:.6f}，相对M8为{percent:.4f}%。',
            '两者均已证明最优，可比较最优成本。' if summary['both_proven_optimal'] else
            '存在限时未证明最优的结果，以上仅比较本次找到的可行方案成本，不能据此推断最优值差异。',
            '', '| 成本分项 | M8 | M9 |', '| --- | ---: | ---: |']
        for key in m8['cost_components']:
            lines.append(f'| {key} | {number(m8["cost_components"][key])} | {number(m9["cost_components"][key])} |')
        lines += ['', '| 客户 | M8原仓 | M9所选仓 |', '| --- | --- | --- |']
        for customer, site in sorted(m8['assignment'].items()):
            lines.append(f'| {customer} | {site} | {m9["assignment"][customer]} |')
        for model, row in records.items():
            lines += ['', f'## M{model}批次日历（分钟）', '',
                '| 批次 | 开始 | 截点 | 结束 |', '| --- | ---: | ---: | ---: |']
            for batch in row['batches_minutes']:
                lines.append(f'| {batch["id"]} | {batch["start"]:.4f} | {batch["cutoff"]:.4f} | {batch["end"]:.4f} |')
    else:
        lines += ['', '首轮全域对照中，至少一个模型在预算内没有经审计的可行方案，不能直接计算这轮求解器方案成本改善比例。',
            '限时无解与已证明不可行分开记录，保留两者状态及下界。']
    if diagnostic is not None:
        lines += ['', '## 补充：固定K=2精确诊断', '',
            '首轮全域对照之外，额外用同一种子42、相同300秒预算固定M9的K=2，',
            '订单、费用、车辆、箱池范围和秒级日历域保持原样，自由选仓仍由Gurobi决策。',
            '该轮与首轮全域冷启动对照分开记录，不能把固定K=2当作M9全域最优性证明。', '',
            f'| 指标 | {reference_label} | M9固定K=2诊断 |', '| --- | ---: | ---: |',
            f'| 成本 | {number(reference["objective"])} | {number(diagnostic["objective"])} |',
            f'| 状态 | {reference["status"]} | {diagnostic["status"]} |',
            f'| 箱池 | {reference["pool"]} | {diagnostic["pool"]} |',
            f'| 配送批次 | {reference["batch_count"]} | {diagnostic["batch_count"]} |',
            f'| 各自声明K域内gap | {number(None if reference.get("mip_gap") is None else 100*reference["mip_gap"], 4)}% | {number(None if diagnostic["fixed_K_mip_gap"] is None else 100*diagnostic["fixed_K_mip_gap"], 4)}% |',
            f'| 独立复核可行 | {reference["audit_valid"]} | {diagnostic["native_domain_feasibility_verified"]} |']
        if diagnostic['objective'] is not None:
            if diagnostic['reduction_vs_M8_incumbent'] is not None:
                label = '已证明最优值' if reference['proven_optimal'] else '本轮可行方案成本'
                lines += ['', f'该M9方案相对{reference_label}{label}的成本差M8−M9为{diagnostic["reduction_vs_M8_incumbent"]:.6f}，',
                    f'相对变化{diagnostic["reduction_percent_vs_M8_incumbent"]:.4f}%。',
                    'M8参照方案尚未证明最优，此处方案差异不等于最优值差异。' if not reference['proven_optimal'] else
                    'M8参照方案的最优性仅覆盖其声明K域。']
            else:
                lines += ['', 'M8本轮没有可行方案，不能计算两份方案的成本改善百分比。']
            lines += ['该M9方案已在原生K=1～7域中独立复核通过，但尚不能据固定K诊断证明M9全域最优。', '',
                f'原生M9的全域下界{number(native_bound)}，已审计可行上界{number(diagnostic["objective"])}。']
            if diagnostic['native_domain_relative_bound_gap'] is not None:
                lines += [f'二者相对差距{100*diagnostic["native_domain_relative_bound_gap"]:.4f}%。']
            lines += ['这里的全域下界来自首轮全K模型，不能用固定K=2的较高下界代替。']
            if diagnostic['improvement_certified_against_M8_bound']:
                lines += ['该M9方案还低于M8原生全域下界，可据此确认原生域中也存在自由选仓改善。']
            if reference['cost_components'] is not None:
                lines += ['', f'| 成本分项 | {reference_label} | M9固定K=2方案 |', '| --- | ---: | ---: |']
                for key in reference['cost_components']:
                    lines.append(f'| {key} | {number(reference["cost_components"][key])} | {number(diagnostic["cost_components"][key])} |')
            lines += ['', '| 客户 | M8原仓 | M9所选仓 |', '| --- | --- | --- |']
            for customer, site in sorted(m8['reference_assignment'].items()):
                lines.append(f'| {customer} | {site} | {diagnostic["assignment"][customer]} |')
            lines += ['', f'M8参照方案箱池/批次：{reference["pool"]}/{reference["batch_count"]}；M9诊断方案箱池/批次：{diagnostic["pool"]}/{diagnostic["batch_count"]}。',
                f'M9首轮全域模型{m9["variables"]}个变量；固定K=2诊断{diagnostic["variables"]}个变量。', '',
                '- [固定K=2结果](M9_fixedK2_seed42_300s/result.json)、[该轮报告](M9_fixedK2_seed42_300s/report.md)。']
            if baseline_diagnostic is not None:
                lines += ['- [M8固定K=2结果](M8_fixedK2_seed42_300s/result.json)、[该轮报告](M8_fixedK2_seed42_300s/report.md)。']
            if (root/'M8_schedule_in_M9_check.json').is_file():
                lines += ['- [M8日程在M9中可行的复核](M8_schedule_in_M9_check.json)。']
    lines += ['', '## 范围与文件', '',
        f'本报告只覆盖{m8["source_case"]}、种子42，未对多种子稳定性作结论。',
        'K=1～7是原生声明域；7笔不可拆订单和每批非空规则排除K>7。未改成整分钟日历。',
        '保存结果均从原始输入独立重放；可行方案通过原业务、成本和连续两周期审计。', '',
        '- [结构化对照](comparison.json)、[独立审计](independent_audits.json)、[实验设置](plan.json)。',
        '- [M8结果](M8_seed42_300s/result.json)、[M8报告](M8_seed42_300s/report.md)。',
        '- [M9结果](M9_seed42_300s/result.json)、[M9报告](M9_seed42_300s/report.md)。',
        '- 每个运行目录包含输入、源码快照、哈希清单和求解日志。']
    (root/'README.md').write_text('\n'.join(lines)+'\n', encoding='utf-8')
    print(json.dumps({key: value for key, value in summary.items() if key != 'results'},
                     ensure_ascii=False, indent=2))
    for row in records.values():
        print(f'M{row["model"]}: status={row["status"]}, cost={row["objective"]}, '
              f'bound={row["best_bound"]}, gap={row["mip_gap"]}, K={row["batch_count"]}, '
              f'N={row["pool"]}, audited={row["audit_valid"]}')
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', help='Pair directory containing M8_seed42_300s and M9_seed42_300s')
    args = parser.parse_args()
    analyze(args.directory)


if __name__ == '__main__':
    main()
