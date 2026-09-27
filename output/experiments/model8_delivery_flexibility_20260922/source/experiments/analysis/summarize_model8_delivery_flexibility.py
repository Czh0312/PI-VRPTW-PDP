"""Reload every saved solution in the M8 delivery-freedom 2x2 experiment."""
import argparse
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
import shutil
import statistics

from experiments.runs.run_model8_capacity_probe import ROOT, load_problem
from experiments.runs.run_model8_delivery_flexibility import ARMS, DEFAULT_OUTPUT
from experiments.runs.run_model8_small_comparison import write
from models.routing.variants.model_8.baseline import prepare_baseline
from models.routing.variants.model_8.state import Evaluator, baseline_state


def read(path):
    return json.loads(path.read_text())


def difference(a, b):
    value = a - b
    return 0.0 if abs(value) < 1e-6 else value


def delivery_changes(e, state):
    old = {n: (s, k) for s, paths in e.base['e2_routes'].items()
           for k, route in enumerate(paths) for n in route[1:-1] if e.tasks[n].kind == 'd'}
    new = {n: (s, k) for s, paths in state['e2_routes'].items()
           for k, route in enumerate(paths) for n in route[1:-1]
           if n in e.tasks and e.tasks[n].kind == 'd'}
    changed = {n: {'before': old[n], 'after': new[n]} for n in old if old[n] != new[n]}
    reordered = sum([n for n in route[1:-1] if n in old] != e.delivery_backbone[s, k]
                    for s, paths in state['e2_routes'].items() for k, route in enumerate(paths))
    return changed, reordered


def remove_relay(e, state):
    stripped = deepcopy(state)
    stripped['transfers'] = []
    for paths in stripped['e2_routes'].values():
        for route in paths:
            route[:] = [n for n in route if not n.startswith('MEET@')]
    peak_v, peak_w = 0.0, 0.0
    for sat, paths in stripped['e2_routes'].items():
        for k, route in enumerate(paths):
            rows, _, _ = e.profile(route, sat, k, {}, {})
            peak_v = max(peak_v, max(r['volume'] for r in rows))
            peak_w = max(peak_w, max(r['weight'] for r in rows))
    try:
        e.audit(stripped)
        valid, reason = True, None
    except ValueError as exc:
        valid, reason = False, str(exc)
    return {'valid': valid, 'rejection': reason, 'maximum_volume': peak_v,
            'maximum_weight': peak_w, 'volume_capacity': e.problem.e2_volume_cap,
            'weight_capacity': e.problem.e2_weight_cap,
            'scope': 'same task allocation and service order, no alternative-route optimization'}


def diagnostic_report(folder, instances):
    target = folder / 'diagnostic'
    if not (target / 'rejections.json').exists():
        return []
    manifest = read(target / 'manifest.json')
    snapshot = target / 'diagnose_model8_delivery_flexibility.py'
    if hashlib.sha256(snapshot.read_bytes()).hexdigest() != manifest['source_sha256']:
        raise RuntimeError('Diagnostic source mismatch')
    rows = read(target / 'rejections.json')
    if sorted(r['instance'] for r in rows) != sorted(instances):
        raise RuntimeError('Diagnostic batch incomplete')
    checks = []
    lines = ['', '## 另行诊断：新配送操作遇到了什么约束', '',
             '主批次结束后，对全部六例另跑种子 19、每例 5 秒的放开/可传版本，'
             '给配送操作添加计数。诊断结果不纳入上面的同预算配对比较。', '',
             '| 实例 | 配送操作尝试 | 返回可行且配送有变化 | 其中比当时方案更短 | 完整候选时间/衔接失败 | 发车载荷失败检查数 | 后续载荷失败检查数 |',
             '|---|---:|---:|---:|---:|---:|---:|']
    for row in rows:
        instance = row['instance']
        result = read(target / f'{instance}.json')
        p, a, b, _ = load_problem(instance)
        saved = read(folder / instance / 'base.json')
        base = prepare_baseline(p, a, b, p.fleet.e2_per_sat_count,
                                (saved['e1_routes'], saved['e2_routes']))
        audit = Evaluator(p, a, b, base, delivery_policy='flexible').audit(result['info']['state'])
        if (result['diagnostic'] != row or result['e1_routes'] != base['e1_routes'] or
                result['e2_routes'] != result['info']['state']['e2_routes'] or
                not math.isclose(audit['objective'], row['objective'], abs_tol=1e-6)):
            raise RuntimeError('Diagnostic result audit failed')
        checks.append({'file': f'{instance}.json', 'valid': True})
        outcomes = row['outcomes']
        totals = [sum(v for k, v in outcomes.items() if k.endswith(':' + label))
                  for label in ('attempts', 'feasible_delivery_changed', 'improving_delivery_changed')]
        timing = sum(row['full_candidate_audit_failures'].values())
        if any('时间' not in k and '衔接' not in k and '时序' not in k
               for k in row['full_candidate_audit_failures']):
            # Preserve full errors rather than mislabel a non-temporal rejection.
            timing = sum(v for k, v in row['full_candidate_audit_failures'].items()
                         if '时间' in k or '衔接' in k or '时序' in k)
        loads = row['route_profile_failures_including_repeated_repair_checks']
        lines.append(f"| {instance} | {totals[0]} | {totals[1]} | {totals[2]} | {timing} | "
                     f"{loads.get('initial_delivery_overload', 0)} | {loads.get('later_load_overload', 0)} |")
    write(target / 'verification.json', {'status': 'PASS', 'audited_solutions': len(checks), 'checks': checks})
    lines += ['', '一次操作可检查多个路线与修复位置，载荷检查也可能重复，因此各列不能相加作为互斥候选数，'
              '也不能据此估计整体可行率。配送有变化指相对当次操作之前的配送安排有变化。'
              '此诊断仅描述这些局部操作，不证明所有配送调整均不可行。', '',
              f"另有 {len(checks)} 个诊断最终解重新加载审计通过。"
              '见 [诊断计数](diagnostic/rejections.json) 与 [诊断审计](diagnostic/verification.json)。']
    return lines


def stronger_start_report(folder):
    target = folder / 'stronger_start'
    if not (target / 'summary.json').exists():
        return []
    result, manifest = read(target / 'summary.json'), read(target / 'manifest.json')
    if (result['status'] != 'PASS' or
            hashlib.sha256((target / 'refine_model8_flexible_start.py').read_bytes()).hexdigest() != manifest['script_sha256'] or
            hashlib.sha256((ROOT / manifest['source_result']).read_bytes()).hexdigest() != manifest['source_result_sha256']):
        raise RuntimeError('Stronger-start verification/source mismatch')
    best = result['best']
    gain = difference(best['flex_no_relay'], best['flex_relay'])
    lines = ['', '## 补充实验：从更好的无传递路线出发', '',
             '主实验后选择 200-10-2，因为该例实际改变配送归属并改善了无传递路线。'
             '用主实验最好无传递方案作为共同起点，放开配送、禁止/允许传递各再跑三个种子，每次 20 秒。'
             '这是事后选择的补充试验，不并入主实验的均值或胜负统计。', '',
             f"- 共同起点：{result['baseline']:.6f}；移除的空车辆标签数：{result['empty_vehicle_labels_removed']}。",
             f"- 继续禁止传递最好值：{best['flex_no_relay']:.6f}；允许传递最好值：{best['flex_relay']:.6f}。",
             f"- 两组最好值之差（禁传−可传）：{gain:.6f}，相对禁传为 {100*gain/best['flex_no_relay']:.4f}%。", '',
             '| 组 | 种子 | 距离 | 交接对数 | 去掉交接后可行 |', '|---|---:|---:|---:|---|']
    for row in sorted(result['runs'], key=lambda r: (r['arm'], r['seed'])):
        lines.append(f"| {row['arm']} | {row['seed']} | {row['objective']:.6f} | {row['handoffs']} | "
                     f"{'是' if row['without_relay_same_routes']['valid'] else '否'} |")
    lines += ['', f"{result['audited_solutions']} 个保存的解（新起点及六次结果）审计通过。"
              '见 [补充实验结果](stronger_start/summary.json)、[选择与参数记录](stronger_start/manifest.json)。']
    return lines


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    folder = ROOT / args.input
    manifest, jobs = read(folder / 'manifest.json'), read(folder / 'jobs.json')
    settings = manifest['settings']
    expected = {(i, seed, arm) for i in settings['instances'] for seed in settings['seeds'] for arm in ARMS}
    actual = [(j['instance'], j['seed'], j['arm']) for j in jobs['jobs']]
    if len(actual) != len(expected) or set(actual) != expected or any(j['status'] != 'completed' for j in jobs['jobs']):
        raise RuntimeError('Missing, duplicated or failed runs; do not summarize as zero improvement')
    for source, sha in manifest['source_sha256'].items():
        for path in (ROOT / source, folder / 'source' / source):
            if hashlib.sha256(path.read_bytes()).hexdigest() != sha:
                raise RuntimeError(f'Source/config snapshot mismatch: {path}')
    checks, records, cases, examples = [], [], [], []
    for instance in settings['instances']:
        case = folder / instance
        meta, saved = read(case / 'input.json'), read(case / 'base.json')
        for source, sha in ((meta['dataset'], meta['dataset_sha256']),
                            (meta['base_source'], meta['base_source_sha256'])):
            if hashlib.sha256((ROOT / source).read_bytes()).hexdigest() != sha:
                raise RuntimeError('Input provenance changed')
        p, a, b, _ = load_problem(instance)
        base = prepare_baseline(p, a, b, p.fleet.e2_per_sat_count,
                                (saved['e1_routes'], saved['e2_routes']))
        if not math.isclose(base['objective'], saved['info']['objective'], abs_tol=1e-6):
            raise RuntimeError('Baseline objective mismatch')
        base_evaluator = Evaluator(p, a, b, base)
        base_audit = base_evaluator.audit(baseline_state(base))
        profiles = list(base_audit['load_profiles'].values())
        pressure = {'vehicles': len(profiles),
                    'initial_volume_full': sum(abs(r[0]['volume'] - p.e2_volume_cap) < 1e-6 for r in profiles),
                    'ever_volume_full': sum(any(abs(x['volume'] - p.e2_volume_cap) < 1e-6 for x in r) for r in profiles),
                    'initial_volume_utilization': sum(r[0]['volume'] for r in profiles) / (len(profiles) * p.e2_volume_cap),
                    'fixed_e1_distance_share': sum(base_evaluator.route_cost(r) for r in base['e1_routes']) / base['objective']}
        checks.append({'file': f'{instance}/base.json', 'valid': True})
        groups = {}
        for arm, (policy, relay) in ARMS.items():
            e = Evaluator(p, a, b, base, delivery_policy=policy)
            arm_records = []
            for seed in settings['seeds']:
                path = case / f'{arm}_{seed}.json'
                result = read(path)
                info, state = result['info'], result['info']['state']
                audit = e.audit(state)
                if (info['baseline']['route_fingerprint'] != meta['baseline_fingerprint'] or
                        result['e1_routes'] != base['e1_routes'] or result['e2_routes'] != state['e2_routes'] or
                        info['delivery_policy'] != policy or info['allow_transfers'] != relay or
                        not math.isclose(audit['objective'], info['objective'], abs_tol=1e-6) or
                        audit['handoff_batches'] != info['handoff_batches'] or
                        info['objective'] > base['objective'] + 1e-6 or
                        (not relay and state['transfers'])):
                    raise RuntimeError(f'Arm or solution mismatch: {path}')
                if not state['transfers']:
                    # A separate M3 route validator also checks non-relay solutions.
                    plain = {s: [r for r in paths if len(r) > 2] for s, paths in state['e2_routes'].items()}
                    plain_audit = prepare_baseline(p, a, b, p.fleet.e2_per_sat_count, (base['e1_routes'], plain))
                    if not math.isclose(plain_audit['objective'], info['objective'], abs_tol=1e-6):
                        raise RuntimeError('Independent M3 validator mismatch')
                if any(batch['collections'][-1]['inventory_after_pickup'] != 0 for batch in audit['handoff_batches']):
                    raise RuntimeError('Deposit inventory left uncollected')
                changes, reordered = delivery_changes(e, state)
                checks.append({'file': str(path.relative_to(folder)), 'valid': True})
                row = {'instance': instance, 'arm': arm, 'seed': seed, 'baseline': base['objective'],
                       'objective': info['objective'], 'saving': difference(base['objective'], info['objective']),
                       'iterations': info['iterations'], 'runtime': info['runtime'],
                       'handoffs': info['transshipment_count'], 'split_deposits': info['split_handoff_deposit_count'],
                       'delivery_owners_changed': len(changes), 'delivery_sequences_changed': reordered,
                       'delivery_move_attempts': info['delivery_move_attempts'],
                       'delivery_relay_repairs': info['delivery_relay_repairs'],
                       'used_vehicles': sum(len(r) > 2 for paths in state['e2_routes'].values() for r in paths),
                       'without_relay_same_routes': remove_relay(e, state)}
                arm_records.append(row)
                records.append(row)
                if state['transfers']:
                    examples.append({'instance': instance, 'arm': arm, 'seed': seed,
                                     'objective': info['objective'], 'delivery_changes': changes,
                                     'handoff_batches': audit['handoff_batches'],
                                     'without_relay_same_routes': row['without_relay_same_routes']})
            best = min(arm_records, key=lambda r: r['objective'])
            groups[arm] = {'best': best, 'mean': statistics.mean(r['objective'] for r in arm_records),
                           'runs_with_relay': sum(r['handoffs'] > 0 for r in arm_records),
                           'runs_with_delivery_changes': sum(r['delivery_sequences_changed'] > 0 for r in arm_records)}
        fixed_gain = difference(groups['fixed_no_relay']['best']['objective'], groups['fixed_relay']['best']['objective'])
        flexible_gain = difference(groups['flex_no_relay']['best']['objective'], groups['flex_relay']['best']['objective'])
        cases.append({'instance': instance, 'baseline': base['objective'],
                      'baseline_capacity_profile': pressure,
                      'baseline_proven': bool(saved['info'].get('proven_optimal')), 'arms': groups,
                      'fixed_relay_gain_best': fixed_gain, 'flex_relay_gain_best': flexible_gain,
                      'flex_relay_gain_percent': 100 * flexible_gain / groups['flex_no_relay']['best']['objective'],
                      'gain_interaction_best': flexible_gain - fixed_gain})
    by_key = {(r['instance'], r['seed'], r['arm']): r for r in records}
    paired = {}
    contrasts = [('fixed_no_relay', 'fixed_relay'), ('flex_no_relay', 'flex_relay'),
                 ('fixed_no_relay', 'flex_no_relay'), ('fixed_relay', 'flex_relay')]
    for left, right in contrasts:
        pairs = []
        for instance in settings['instances']:
            for seed in settings['seeds']:
                l, r = by_key[instance, seed, left], by_key[instance, seed, right]
                gain = difference(l['objective'], r['objective'])
                pairs.append({'instance': instance, 'seed': seed, 'gain': gain,
                              'gain_percent': 100 * gain / l['objective']})
        paired[left + '_versus_' + right] = {
            'right_wins': sum(r['gain'] > 0 for r in pairs), 'ties': sum(r['gain'] == 0 for r in pairs),
            'right_losses': sum(r['gain'] < 0 for r in pairs),
            'mean_gain': statistics.mean(r['gain'] for r in pairs),
            'mean_gain_percent': statistics.mean(r['gain_percent'] for r in pairs), 'pairs': pairs}
    stats = {'runs': len(records), 'audited_solutions': len(checks), 'elapsed': jobs['elapsed'],
             'flexible_best_beats_no_relay_cases': sum(r['flex_relay_gain_best'] > 0 for r in cases),
             'best_relay_gain_percent_range': [min(r['flex_relay_gain_percent'] for r in cases),
                                               max(r['flex_relay_gain_percent'] for r in cases)],
             'paired_contrasts': paired}
    write(folder / 'summary.json', {'settings': settings, 'statistics': stats, 'instances': cases, 'runs': records})
    write(folder / 'verification.json', {'status': 'PASS', 'audited_solutions': len(checks), 'checks': checks})
    write(folder / 'relay_examples.json', examples)
    wins = sum(r['flex_relay_gain_best'] > 0 for r in cases)
    ties = sum(r['flex_relay_gain_best'] == 0 for r in cases)
    losses = sum(r['flex_relay_gain_best'] < 0 for r in cases)
    lines = ['# M8 配送骨架放开与空箱传递：四组对照', '',
             f"放开配送后的三种子最好值对比：允许传递相对禁止传递为 {wins} 胜、{ties} 平、{losses} 负。"
             '本轮未显示出传递收益的大幅提升；少数方案确实依赖容量释放，但这不等于优于重新优化的无传递方案。', '',
             '## Material Passport', '',
             '- 状态：实际运行并重新加载复核；原始 standard_same_day 算例，需求、容量、时间窗不变。',
             '- 模式：连续执行；用户已授权继续实现与实验；本轮仅做算法实验与描述性解读。',
             '- fixed 固定配送车辆和顺序；flexible 允许同仓车辆间调整配送归属与顺序。两者均可调整回收。',
             '- no_relay 严格禁止空箱转交；relay 允许原有单次转交、多接车异步分取。',
             '- 四组固定相同 E1 路线和原有 E2 车辆索引，允许闲置车辆，不新增车辆、不跨仓。',
             f"- 每例四组 × 种子 {settings['seeds']} × 每次 {settings['time']} 秒，最多 {settings['jobs']} 个进程并行。",
             '- 每组从相同保存的较好 M3 基础解起步；M3 证明状态在 summary.json 中逐例标注。',
             '- 同一配送政策内，两组使用相同路径操作；relay 额外花时间搜索交接。墙钟预算相同，迭代数不强制相同。', '',
             '## 三种子最好总距离', '',
             '| 实例 | M3 | 固定/禁传 | 固定/可传 | 放开/禁传 | 放开/可传 | 放开后可传相对禁传节省 |',
             '|---|---:|---:|---:|---:|---:|---:|']
    for case in cases:
        values = [case['arms'][arm]['best']['objective'] for arm in ARMS]
        lines.append(f"| {case['instance']} | {case['baseline']:.6f} | " +
                     ' | '.join(f'{v:.6f}' for v in values) + f" | {case['flex_relay_gain_percent']:.4f}% |")
    lines += ['', '正数表示可传组距离更短。最好值是三次运行取最小值，不是数学最优值；'
              '相对 M3 的总改善包含配送和回收调整，不能全部归因于传递。', '',
              '## 逐种子配对比较', '', '| 对照 → 实验组 | 实验组胜/平/负 | 平均距离节省 | 平均相对节省 |',
              '|---|---|---:|---:|']
    for label, row in paired.items():
        lines.append(f"| {label} | {row['right_wins']}/{row['ties']}/{row['right_losses']} | "
                     f"{row['mean_gain']:.6f} | {row['mean_gain_percent']:.4f}% |")
    lines += ['', '这些数值描述固定预算的启发式搜索结果，受随机轨迹及搜索成本影响；每例仅三个种子，不作显著性或全局最优值推断。', '',
              '## 放开且允许传递的最好解：是否真正改变配送并依赖传递', '',
              '| 实例 | 换配送车辆任务数 | 配送序列变化路线数 | 交接对数 | 多接车批次 | 去掉传递是否仍可行 | 去掉传递后最大体积/容量 |',
              '|---|---:|---:|---:|---:|---|---|']
    for case in cases:
        r = case['arms']['flex_relay']['best']
        cf = r['without_relay_same_routes']
        lines.append(f"| {case['instance']} | {r['delivery_owners_changed']} | {r['delivery_sequences_changed']} | "
                     f"{r['handoffs']} | {r['split_deposits']} | {'是' if cf['valid'] else '否'} | "
                     f"{cf['maximum_volume']:.0f}/{cf['volume_capacity']:.0f} |")
    lines += ['', '去掉传递时保持服务任务归属和顺序，删除纯交接访问并复核。此检查只说明该具体方案是否依赖容量释放，'
              '不能代替重新优化的禁止传递对照，更不能证明禁止传递最优值。']
    lines += diagnostic_report(folder, settings['instances'])
    lines += stronger_start_report(folder)
    lines += ['', '## 逐次结果', '', '| 实例 | 组 | 种子 | 距离 | 迭代 | 配送变化路线 | 交接对数 |',
              '|---|---|---:|---:|---:|---:|---:|']
    for row in records:
        lines.append(f"| {row['instance']} | {row['arm']} | {row['seed']} | {row['objective']:.6f} | "
                     f"{row['iterations']} | {row['delivery_sequences_changed']} | {row['handoffs']} |")
    lines += ['', '## 基础路线容量与固定里程', '',
              '| 实例 | 已用 E2 车辆数 | 发车体积利用率 | 曾达到体积上限的路线数 | 固定 E1 里程占比 |',
              '|---|---:|---:|---:|---:|']
    for case in cases:
        r = case['baseline_capacity_profile']
        lines.append(f"| {case['instance']} | {r['vehicles']} | {100*r['initial_volume_utilization']:.2f}% | "
                     f"{r['ever_volume_full']} | {100*r['fixed_e1_distance_share']:.2f}% |")
    lines += ['', '这是已有可行路线的描述，不能由利用率较低推断容量约束无效：基础规划可能已经为规避容量而分散任务。'
              '固定 E1 占比也意味着本轮并非对全部两级路线同时优化。', '',
              '## 复核与适用范围', '',
              f"- {len(checks)} 个解（基础解与全部第二阶段结果）重新加载审计通过；代码、配置、数据来源哈希一致。",
              '- 新功能及原有 M8/M6 相关测试 45 项通过，见 [测试记录](test_verification.json)。',
              '- 复核全部任务覆盖、同仓回路、发车与逐弧载荷、一次交接、先放后取、配送先于回收、两级时序与距离。',
              '- 所有不含传递的结果额外通过 M3 路线校验器。最终方案不能比输入基础解更差。',
              '- 配送换车、配送交换、同顾客配送回收成组移动均为有限邻域；回收最多移动 3 任务，插入 beam 为 4。',
              '- 传递修复目前只处理一个发送车的容量缺口；不同时联合重构所有车辆，不允许新交接先增加当前距离。',
              '- 本轮放开功能属于 ALNS 实验开关；默认 M8 与现有精确入口仍固定配送骨架。不能声称已求解完全自由的全局问题。',
              '- [全部数值](summary.json)、[审计](verification.json)、[交接明细](relay_examples.json)、[运行日志](jobs.json)。']
    (folder / 'findings.md').write_text('\n'.join(lines) + '\n')
    source = Path(__file__).resolve()
    dest = folder / 'source' / source.relative_to(ROOT)
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, dest)
    write(folder / 'analysis_source_sha256.json', {str(source.relative_to(ROOT)): hashlib.sha256(source.read_bytes()).hexdigest()})
    print(json.dumps({k: v for k, v in stats.items() if k != 'paired_contrasts'}, ensure_ascii=False))


if __name__ == '__main__':
    main()
