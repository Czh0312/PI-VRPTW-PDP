# Historical M8 experiments: retain their original two-stage semantics.
"""Reload every saved large-instance solution, verify, and summarize M8 versus M3."""
import argparse
from collections import Counter
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
import shutil
import statistics

from experiments.runs.run_model8_capacity_probe import ROOT, load_problem
from experiments.runs.run_model8_large_comparison import DEFAULT_OUTPUT
from experiments.runs.run_model8_small_comparison import write
from models.routing.variants.model_8.two_stage_v2.baseline import prepare_baseline
from models.routing.variants.model_8.two_stage_v2.state import Evaluator, baseline_state


def read(path):
    return json.loads(path.read_text())


def gain(a, b):
    delta = a - b
    return 0.0 if abs(delta) < 1e-6 else delta


def mechanism_check(e, state):
    """Remove handoffs, retaining the pickup assignment/order, to test capacity."""
    trial = deepcopy(state)
    trial['transfers'] = []
    for sat, paths in trial['e2_routes'].items():
        trial['e2_routes'][sat] = [[n for n in path if not n.startswith('MEET@')]
                                  for path in paths]
    sent, received = e.transfer_maps(trial)
    overloaded = []
    for sat, paths in trial['e2_routes'].items():
        for vehicle, path in enumerate(paths):
            profile, valid, _ = e.profile(path, sat, vehicle, sent, received)
            if not valid:
                overloaded.append({'satellite': sat, 'vehicle': vehicle,
                                   'max_volume': max(r['volume'] for r in profile),
                                   'max_weight': max(r['weight'] for r in profile)})
    aggregated = Counter()
    for t in state['transfers']:
        aggregated[t['satellite'], t['event'], t['sender_vehicle'], t['receiver_vehicle']] += t['quantity']
    return {'scope': 'fixed final pickup assignment/order, remove all handoffs and handoff-only visits',
            'volume_capacity': e.problem.e2_volume_cap,
            'weight_capacity': e.problem.e2_weight_cap,
            'overloaded_routes_without_transfers': overloaded,
            'transfer_records': len(state['transfers']),
            'distinct_event_vehicle_pairs': len(aggregated),
            'boxes': sum(aggregated.values()),
            'aggregated_transfers': [{'satellite': s, 'event': n, 'sender_vehicle': a,
                                      'receiver_vehicle': b, 'quantity': q}
                                     for (s, n, a, b), q in aggregated.items()]}


def append_refinement(folder):
    strong = folder / 'strong_baseline'
    if not strong.exists():
        return
    manifest = read(strong / 'manifest.json')
    jobs = read(strong / 'jobs.json')
    if len(jobs) != len(manifest['instances']) or any(j['status'] != 'completed' for j in jobs):
        raise RuntimeError('Strong-baseline checks incomplete; report remaining work explicitly')
    source = ROOT / 'experiments/runs/refine_model8_large_baseline.py'
    if any(hashlib.sha256(p.read_bytes()).hexdigest() != manifest['source_sha256']
           for p in (source, strong / source.name)):
        raise RuntimeError('Refinement source hash mismatch')
    checks, rows = [], []
    for instance in manifest['instances']:
        case = strong / instance
        p, a, b, _ = load_problem(instance)
        selected = read(case / 'base.json')
        base = prepare_baseline(p, a, b, p.fleet.e2_per_sat_count,
                                (selected['e1_routes'], selected['e2_routes']))
        e = Evaluator(p, a, b, base)
        e.audit(baseline_state(base))
        history_path = case / 'historical_warm_start.json'
        if history_path.exists():
            history = read(history_path)
            if hashlib.sha256((ROOT / history['source']).read_bytes()).hexdigest() != history['historical_source_sha256']:
                raise RuntimeError('Historical baseline source hash mismatch')
            hist_base = prepare_baseline(p, a, b, p.fleet.e2_per_sat_count,
                                         (history['e1_routes'], history['e2_routes']))
            hist_audit = Evaluator(p, a, b, hist_base).audit(baseline_state(hist_base))
            if not math.isclose(hist_audit['objective'], history['info']['objective'], abs_tol=1e-6):
                raise RuntimeError('Historical baseline objective mismatch')
            checks.append({'file': str(history_path.relative_to(strong)), 'valid': True})
        exact = read(case / 'm3_exact.json')
        if exact['e1_routes'] is not None:
            ex_base = prepare_baseline(p, a, b, p.fleet.e2_per_sat_count,
                                       (exact['e1_routes'], exact['e2_routes']))
            audit = Evaluator(p, a, b, ex_base).audit(baseline_state(ex_base))
            if not math.isclose(audit['objective'], exact['info']['objective'], abs_tol=1e-6):
                raise RuntimeError('Refined M3 objective mismatch')
            checks.append({'file': str((case / 'm3_exact.json').relative_to(strong)), 'valid': True})
        result_rows = {}
        for method in ('control', 'relay'):
            result = read(case / f'{method}_42.json')
            info = result['info']
            audit = e.audit(info['state'])
            if (info['baseline']['route_fingerprint'] != base['route_fingerprint'] or
                    result['e1_routes'] != base['e1_routes'] or
                    result['e2_routes'] != info['state']['e2_routes'] or
                    not math.isclose(audit['objective'], info['objective'], abs_tol=1e-6) or
                    (method == 'control' and info['transshipment_count'])):
                raise RuntimeError('Refined M8 audit mismatch')
            checks.append({'file': str((case / f'{method}_42.json').relative_to(strong)), 'valid': True})
            result_rows[method] = info
        relay, control = result_rows['relay'], result_rows['control']
        rows.append({'instance': instance, 'm3': base['objective'],
                     'm3_proven': bool(selected['info'].get('proven_optimal')),
                     'm3_bound': exact['info'].get('best_bound'),
                     'control': control['objective'], 'relay': relay['objective'],
                     'saving': gain(base['objective'], relay['objective']),
                     'saving_percent': 100 * gain(base['objective'], relay['objective']) / base['objective'],
                     'control_minus_relay': gain(control['objective'], relay['objective']),
                     'mechanism': mechanism_check(e, relay['state']),
                     'transfer_records': relay['transshipment_count']})
    write(strong / 'verification.json', {'status': 'PASS', 'audited_solutions': len(checks), 'checks': checks})
    write(strong / 'summary.json', {'settings': manifest, 'instances': rows})
    lines = ['', '## 加强 M3 基线后的复核（事后选取正收益案例）', '',
             f"主实验中返回有传递且改善基础解的案例，先纳入通过同一数据与可行性审计的更好历史 M3 解（若有），"
             f"再追加 {manifest['exact_time']} 秒带初始解的 M3 MILP。"
             f"保留更好的 M3 解，再以种子 42 对两分支各运行 {manifest['stage2_time']} 秒。"
             '这些是额外的基线敏感性检查，不混入主实验三种子统计。', '',
             '| 实例 | 加强 M3 | M3 证明最优 | 禁止传递 | M8 | 相对加强 M3 节省% | 交接记录数 |',
             '|---|---:|---|---:|---:|---:|---:|']
    for r in rows:
        lines.append(f"| {r['instance']} | {r['m3']:.6f} | {r['m3_proven']} | {r['control']:.6f} | "
                     f"{r['relay']:.6f} | {r['saving_percent']:.4f}% | {r['transfer_records']} |")
    lines += ['', '基线变好后收益消失，说明先前收益依赖所选配送骨架；收益仍存在也只证明找到更好的可行传递方案。'
              'M8 第二阶段仍为 ALNS，零改善仍不能作为不存在更好传递方案的最优性证明。', '',
              f"额外重新审计 {len(checks)} 个解，全部通过；详见 [强化基线结果](strong_baseline/summary.json) "
              '与 [复核记录](strong_baseline/verification.json)。']
    with (folder / 'findings.md').open('a', encoding='utf-8') as handle:
        handle.write('\n'.join(lines) + '\n')
    positive = [r for r in rows if r['saving'] > 1e-6]
    if positive:
        pilot = read(folder / 'summary.json')['statistics']
        lead = (f"原始 {pilot['instances']} 例初轮有 {pilot['m8_improved_instances']} 例出现改善；"
                f"对其中 {len(rows)} 例加强 M3 基线后，{len(positive)} 例仍有改善，"
                f"幅度为 {min(r['saving_percent'] for r in positive):.4f}%–"
                f"{max(r['saving_percent'] for r in positive):.4f}%。"
                f"其余 {len(rows) - len(positive)} 例的初轮收益未在强化复核中保留。"
                '当前结论是容量协作在这些可行基础路线上能产生小幅收益，且对基础解质量敏感；'
                '全部强化 M3 解与 M8 解的最优性状态见末节，不将同预算差值解释为最优增量价值。')
        text = (folder / 'findings.md').read_text()
        title, rest = text.split('\n', 1)
        (folder / 'findings.md').write_text(title + '\n\n' + lead + '\n' + rest, encoding='utf-8')
    print(json.dumps({'strong_baseline': rows, 'extra_audited_solutions': len(checks)}, ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    folder = ROOT / args.output
    manifest = read(folder / 'manifest.json')
    settings = manifest['settings']
    jobs = read(folder / 'jobs.json')
    expected_jobs = len(settings['instances']) * (1 + 2 * len(settings['seeds']))
    if len(jobs['jobs']) != expected_jobs or any(j['status'] != 'completed' for j in jobs['jobs']):
        raise RuntimeError('Incomplete experiment: inspect jobs.json, missing jobs are not zero gain')
    for source, expected_hash in manifest['source_sha256'].items():
        for path in (ROOT / source, folder / 'source' / source):
            if hashlib.sha256(path.read_bytes()).hexdigest() != expected_hash:
                raise RuntimeError(f'Source changed: {path}')
    checks, records, rows, mechanisms = [], [], [], []
    for instance in settings['instances']:
        directory = folder / instance
        p, e1, e2, source = load_problem(instance)
        meta = read(directory / 'input.json')
        if hashlib.sha256(source.read_bytes()).hexdigest() != meta['source_sha256']:
            raise RuntimeError('Dataset changed')
        fleet = read(ROOT / 'config/routing/validation/zhou_large.json')
        if meta['fleet'] != fleet:
            raise RuntimeError('Fleet mismatch')
        base_record = read(directory / 'base.json')
        base = prepare_baseline(p, e1, e2, base_record['effective_vehicle_count'],
                                (base_record['e1_routes'], base_record['e2_routes']))
        e = Evaluator(p, e1, e2, base)
        e.audit(baseline_state(base))
        if base['route_fingerprint'] != base_record['baseline_fingerprint']:
            raise RuntimeError('Selected baseline fingerprint mismatch')
        m3_costs = []
        for seed in settings['seeds']:
            path = directory / f'm3_{seed}.json'
            result = read(path)
            if result['e1_routes'] is None:
                raise RuntimeError('M3 failed; report failure explicitly')
            trial_base = prepare_baseline(p, e1, e2, p.fleet.e2_per_sat_count,
                                          (result['e1_routes'], result['e2_routes']))
            checked = Evaluator(p, e1, e2, trial_base).audit(baseline_state(trial_base))
            if not math.isclose(checked['objective'], result['info']['objective'], abs_tol=1e-6):
                raise RuntimeError('M3 objective mismatch')
            if trial_base['route_fingerprint'] != result['baseline_fingerprint']:
                raise RuntimeError('M3 fingerprint mismatch')
            m3_costs.append(checked['objective'])
            checks.append({'file': str(path.relative_to(folder)), 'valid': True,
                           'objective': checked['objective']})
        if not math.isclose(min(m3_costs), base['objective'], abs_tol=1e-6):
            raise RuntimeError('Selected baseline is not the best M3 seed')
        instance_records = []
        rejections = Counter()
        for seed in settings['seeds']:
            for method in ('control', 'relay'):
                path = directory / f'{method}_{seed}.json'
                result = read(path)
                info = result['info']
                checked = e.audit(info['state'])
                if (info['baseline']['route_fingerprint'] != base['route_fingerprint'] or
                        result['e1_routes'] != base['e1_routes'] or
                        result['e2_routes'] != info['state']['e2_routes'] or
                        not math.isclose(checked['objective'], info['objective'], abs_tol=1e-6) or
                        info['objective'] > base['objective'] + 1e-6 or
                        (method == 'control' and info['state']['transfers'])):
                    raise RuntimeError(f'Audit mismatch: {path}')
                checks.append({'file': str(path.relative_to(folder)), 'valid': True,
                               'objective': checked['objective']})
                row = {'instance': instance, 'method': method, 'seed': seed,
                       'objective': info['objective'], 'baseline': base['objective'],
                       'saving': gain(base['objective'], info['objective']),
                       'saving_percent': 100 * gain(base['objective'], info['objective']) / base['objective'],
                       'transfers': info['transshipment_count'],
                       'transferred_boxes': info.get('transferred_boxes', 0),
                       'iterations': info['iterations'], 'runtime': info['runtime'],
                       'wall_seconds': result['wall_seconds'],
                       'relay_audits': info['relay_audits'],
                       'capacity_blocked_candidates': info['capacity_blocked_candidates'],
                       'feasible_relay_repairs': info['feasible_relay_repairs'],
                       'audit_rejections': result['audit_rejections'],
                       'fingerprint': base['route_fingerprint']}
                records.append(row)
                instance_records.append(row)
                if method == 'relay':
                    rejections.update(result['audit_rejections'])
        controls = [r for r in instance_records if r['method'] == 'control']
        relays = [r for r in instance_records if r['method'] == 'relay']
        cb, rb = min(controls, key=lambda r: r['objective']), min(relays, key=lambda r: r['objective'])
        if rb['transfers']:
            state = read(directory / f"relay_{rb['seed']}.json")['info']['state']
            mechanisms.append({'instance': instance, 'seed': rb['seed'], **mechanism_check(e, state)})
        rows.append({'instance': instance, 'customers': meta['customers'],
                     'satellites': len(p.satellites), 'm3_best': base['objective'],
                     'm3_seed': base_record['seed'], 'm3_mean': statistics.mean(m3_costs),
                     'control_best': cb['objective'], 'control_mean': statistics.mean(r['objective'] for r in controls),
                     'relay_best': rb['objective'], 'relay_mean': statistics.mean(r['objective'] for r in relays),
                     'm8_saving': gain(base['objective'], rb['objective']),
                     'm8_saving_percent': 100 * gain(base['objective'], rb['objective']) / base['objective'],
                     'control_minus_relay_best': gain(cb['objective'], rb['objective']),
                     'best_relay_transfers': rb['transfers'], 'best_relay_seed': rb['seed'],
                     'relay_runs_with_transfers': sum(r['transfers'] > 0 for r in relays),
                     'capacity_blocked_candidates': sum(r['capacity_blocked_candidates'] for r in relays),
                     'relay_audits': sum(r['relay_audits'] for r in relays),
                     'feasible_relay_repairs': sum(r['feasible_relay_repairs'] for r in relays),
                     'relay_branch_rejections': dict(rejections)})
    relays = [r for r in records if r['method'] == 'relay']
    controls = {(r['instance'], r['seed']): r for r in records if r['method'] == 'control'}
    paired = [gain(controls[r['instance'], r['seed']]['objective'], r['objective']) for r in relays]
    stats = {'instances': len(rows), 'm3_runs': len(rows) * len(settings['seeds']),
             'stage2_runs': len(records), 'audited_solutions': len(checks),
             'm8_improved_instances': sum(r['m8_saving'] > 1e-6 for r in rows),
             'm8_beats_control_instances': sum(r['control_minus_relay_best'] > 1e-6 for r in rows),
             'relay_runs_with_transfers': sum(r['transfers'] > 0 for r in relays),
             'feasible_relay_repairs': sum(r['feasible_relay_repairs'] for r in relays),
             'paired_relay_wins_ties_losses': [sum(d > 1e-6 for d in paired), sum(abs(d) <= 1e-6 for d in paired), sum(d < -1e-6 for d in paired)],
             'elapsed_batch_seconds': jobs['elapsed']}
    write(folder / 'summary.json', {'settings': settings, 'statistics': stats,
                                   'instances': rows, 'runs': records})
    write(folder / 'mechanism_checks.json', mechanisms)
    write(folder / 'verification.json', {'status': 'PASS', 'audited_solutions': len(checks),
          'dataset_and_source_hashes_verified': True, 'checks': checks})
    lines = ['# 大规模原始算例：M3 与 M8 v2', '', '## Material Passport', '',
             '- 状态：实际执行与重新加载审计；2026-09-22。',
             '- 原始 standard_same_day 数据；预先选定三类规模/仓数的编号 1、2 算例，未按结果筛选。',
             '- 体积容量 5、重量容量 300、每仓最多 25 辆；容量、时间窗、需求均未修改。',
             f"- M3：每例种子 {settings['seeds']}，每次 {settings['base_time']} 秒；选三次中的最好可行解。",
             f"- 两个第二阶段：共用同一 M3 基础路线，种子 {settings['seeds']}，每次 {settings['stage2_time']} 秒。",
             '- 两分支都固定 E1 与配送车辆/相对顺序，允许调整回收任务；只有 relay 允许交接。',
             '- 最多两个独立 ALNS 进程并发，均使用相同墙钟预算；记录实际迭代数与时间，未比较 CPU 性能。',
             '- 本节主实验均为启发式可行解，不含最优性证明；追加的 M3 强化复核单独报告。',
             '- 距离单位为算例坐标距离，不等同于真实公里；三种子最好值为组合搜索结果。', '',
             '## 每例最好结果', '',
             '| 实例 | M3 | 禁止传递 | 允许传递 M8 | M8 相对 M3 节省% | 对照−M8 | M8 最好解交接记录数 |',
             '|---|---:|---:|---:|---:|---:|---:|']
    for r in rows:
        lines.append(f"| {r['instance']} | {r['m3_best']:.6f} | {r['control_best']:.6f} | {r['relay_best']:.6f} | "
                     f"{r['m8_saving_percent']:.4f}% | {r['control_minus_relay_best']:.6f} | {r['best_relay_transfers']} |")
    lines += ['', f"M8 在 {stats['m8_improved_instances']}/{len(rows)} 个实例上改善 M3 基础解，"
              f"在 {stats['m8_beats_control_instances']}/{len(rows)} 个实例上优于禁止传递分支的最好解。"
              f"{len(relays)} 次允许传递运行中，{stats['relay_runs_with_transfers']} 次最终返回的解含交接。", '',
              '上述改善不能全部归因于空箱传递：M3 基础解未证明最优，普通回收调整也可能缩短路线。'
              '禁止传递与允许传递的同预算差值只表示搜索表现，不是最优传递增量价值的证明。'
              '允许传递理论上包含禁止传递的可行域，但有限时间的启发式可能返回更差的结果。', '',
              '交接记录数沿用求解器字段；同一事件、相同发送/接收车辆的多条记录应合并为一次实际交接。'
              '去掉传递但保持最终回收任务归属与顺序的容量检查见 [mechanism_checks.json](mechanism_checks.json)。'
              '该反事实检查证明特定路线是否依赖容量释放，不证明禁止传递的全局最优值。', '',
              '## 搜索中的容量与交接诊断', '',
              '| 实例 | 容量受阻候选 | 交接候选完整审计 | 找到可行交接修复 | 含交接候选时序拒绝 |',
              '|---|---:|---:|---:|---:|']
    for r in rows:
        rejected = r['relay_branch_rejections'].get('with_handoff: 时间窗或两级衔接不可行', 0)
        lines.append(f"| {r['instance']} | {r['capacity_blocked_candidates']} | {r['relay_audits']} | {r['feasible_relay_repairs']} | {rejected} |")
    lines += ['', '“交接候选完整审计”计数为新增交接修复的尝试；时序拒绝还包括保留已有交接的普通路线调整，'
              '两列不是同一分母下的成功/失败比例。'
              '这些是本次搜索实际生成的候选计数，并非全部潜在交接机会。容量可行只是必要条件，'
              '完整审计还要求回收时间窗、配送先于回收、交接先后关系以及 E1 等待 E2 返回同时可行。'
              '最终没有交接或没有改善，不能证明大规模实例不存在有收益的交接。', '',
              '## 逐种子结果', '',
              '| 实例 | 分支 | 种子 | 距离 | 相对 M3 节省% | 交接数 | 迭代数 | 时间s |',
              '|---|---|---:|---:|---:|---:|---:|---:|']
    for r in records:
        lines.append(f"| {r['instance']} | {r['method']} | {r['seed']} | {r['objective']:.6f} | "
                     f"{r['saving_percent']:.4f}% | {r['transfers']} | {r['iterations']} | {r['runtime']:.3f} |")
    lines += ['', '## 验证与复现', '',
              f"- 共重新读取并审计 {len(checks)} 个解（{stats['m3_runs']} 个 M3、{stats['stage2_runs']} 个第二阶段），全部通过。",
              '- 检查任务覆盖、固定配送骨架、车辆身份、逐段重量/体积、O/R 库存、一次交接、完整时间约束及距离复算。',
              '- 检查所有分支的 M3 路线指纹相同、禁止传递分支确实无交接、当前数据和代码与运行快照哈希一致。',
              '- 原始结果：[summary.json](summary.json)；逐解审计：[verification.json](verification.json)；运行记录：[jobs.json](jobs.json)。',
              '- 随机种子固定但采用墙钟终止，操作系统调度可改变迭代数，不声称重新运行逐位相同。', '',
              '```bash', 'python -m experiments.runs.run_model8_large_comparison',
              'python -m experiments.analysis.summarize_model8_large', '```']
    (folder / 'findings.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')
    source = Path(__file__).resolve()
    dest = folder / 'source' / source.relative_to(ROOT)
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, dest)
    write(folder / 'analysis_source_sha256.json', {str(source.relative_to(ROOT)): hashlib.sha256(source.read_bytes()).hexdigest()})
    print(json.dumps(stats, ensure_ascii=False))
    append_refinement(folder)


if __name__ == '__main__':
    main()
