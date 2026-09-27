# Historical M8 experiments: retain their original two-stage semantics.
"""Re-audit and compare the three matched M8 handoff-search arms."""
import argparse
import hashlib
import json
import math
from pathlib import Path
import runpy
import shutil
import statistics

from experiments.runs.run_model8_capacity_probe import ROOT, load_problem
from experiments.runs.run_model8_split_comparison import ARMS, DEFAULT_OUTPUT
from experiments.runs.run_model8_small_comparison import write
from models.routing.variants.model_8.two_stage_v2.baseline import prepare_baseline
from models.routing.variants.model_8.two_stage_v2.state import Evaluator, baseline_state


def read(path):
    return json.loads(path.read_text())


def delta(left, right):
    result = left - right
    return 0.0 if abs(result) < 1e-6 else result


def diagnostic_report(folder):
    target = folder / 'diagnostic'
    if not (target / 'rejections.json').exists():
        return []
    manifest = read(target / 'manifest.json')
    if hashlib.sha256((target / 'diagnose_model8_split.py').read_bytes()).hexdigest() != manifest['source_sha256']:
        raise RuntimeError('Diagnostic source snapshot mismatch')
    fixture = runpy.run_path(str(folder / 'source/tests/routing/test_model8_split.py'))
    _, _, _, base, evaluator, _ = fixture['split_example']()
    demo = read(target / 'constructed_end_to_end.json')
    checks = []

    def verify(name, result, evaluator, base):
        audit = evaluator.audit(result['info']['state'])
        if (not math.isclose(audit['objective'], result['info']['objective'], abs_tol=1e-6) or
                result['e1_routes'] != base['e1_routes'] or
                result['e2_routes'] != result['info']['state']['e2_routes'] or
                audit['handoff_batches'] != result['info']['handoff_batches']):
            raise RuntimeError(f'Diagnostic audit mismatch: {name}')
        checks.append({'file': name, 'valid': True})

    verify('constructed_end_to_end.json', demo, evaluator, base)
    rows = read(target / 'rejections.json')
    for row in rows:
        instance = row['instance']
        p, a, b, _ = load_problem(instance)
        saved = read(folder / instance / 'base.json')
        base = prepare_baseline(p, a, b, p.fleet.e2_per_sat_count,
                                (saved['e1_routes'], saved['e2_routes']))
        result = read(target / f'{instance}.json')
        if row != result['diagnostic']:
            raise RuntimeError('Diagnostic rejection records mismatch')
        verify(f'{instance}.json', result, Evaluator(p, a, b, base), base)
    write(target / 'verification.json', {'status': 'PASS', 'audited_solutions': len(checks), 'checks': checks})
    lines = ['', '## 构造机制测试与另行诊断', '',
             f"完整 ALNS 从合法基础解出发，构造例距离由 {demo['base']['objective']:.0f} 降至 {demo['info']['objective']:.0f}，"
             f"保留 {demo['info']['split_handoff_deposit_count']} 次同批多车分取：一车放下 2 箱，另两车各取 1 箱。",
             '这是为容量缺口设计的机制测试，基础解未证明最优，距离下降不能作为实际算例的分取收益。'
             '该运行两辆接车同刻取箱；另有独立单元测试验证不同时刻分取以及库存逐次清零。', '',
             '主实验结束后，对以下三例另跑种子 19、每例 5 秒，记录含同批分取的完整候选被拒绝的原因。'
             '这些诊断运行不并入上面的配对比较，计数只覆盖本次生成的候选状态。', '',
             '| 实例 | 时间窗或两级衔接失败 | 容量或原生空箱来源失败 |', '|---|---:|---:|']
    for row in rows:
        failures = row['rejections']
        lines.append(f"| {row['instance']} | {failures.get('with_split: 时间窗或两级衔接不可行', 0)} | "
                     f"{failures.get('with_split: 容量超限或转出并非已携带的原生空箱', 0)} |")
    lines += ['', '这些结果表明当前生成的部分机会受时间与载荷约束阻挡，不能证明全局没有可行分取。'
              '还需考虑有限邻域、固定配送骨架和计算预算对机会发现的限制。', '',
              f"诊断另有 {len(checks)} 个保存的解重新加载审计通过；"
              '见 [诊断记录](diagnostic/rejections.json)、[机制测试原始解](diagnostic/constructed_end_to_end.json)'
              '和 [诊断审计](diagnostic/verification.json)。']
    return lines


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    folder = ROOT / args.input
    manifest, jobs = read(folder / 'manifest.json'), read(folder / 'jobs.json')
    settings = manifest['settings']
    expected = len(settings['instances']) * len(settings['seeds']) * len(ARMS)
    if len(jobs['jobs']) != expected or any(j['status'] != 'completed' for j in jobs['jobs']):
        raise RuntimeError('Experiment incomplete; failed/missing runs are not zero improvement')
    for source, sha in manifest['source_sha256'].items():
        for path in (ROOT / source, folder / 'source' / source):
            if hashlib.sha256(path.read_bytes()).hexdigest() != sha:
                raise RuntimeError(f'Code/config snapshot mismatch: {path}')
    records, checks, instances, split_examples = [], [], [], []
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
        evaluator = Evaluator(p, a, b, base)
        base_audit = evaluator.audit(baseline_state(base))
        if not math.isclose(base_audit['objective'], saved['info']['objective'], abs_tol=1e-6):
            raise RuntimeError('M3 objective mismatch')
        checks.append({'file': f'{instance}/base.json', 'valid': True})
        grouped = {}
        for arm, (limit, flexible) in ARMS.items():
            arm_rows = []
            for seed in settings['seeds']:
                path = case / f'{arm}_{seed}.json'
                result = read(path)
                info = result['info']
                audit = evaluator.audit(info['state'])
                if (info['baseline']['route_fingerprint'] != meta['baseline_fingerprint'] or
                        result['e1_routes'] != base['e1_routes'] or
                        result['e2_routes'] != info['state']['e2_routes'] or
                        info['max_handoff_receivers'] != limit or
                        info['search_all_receivers'] != flexible or
                        not info['multi_donor_pickups'] or
                        not math.isclose(audit['objective'], info['objective'], abs_tol=1e-6) or
                        audit['handoff_batches'] != info['handoff_batches'] or
                        info['objective'] > base['objective'] + 1e-6):
                    raise RuntimeError(f'Audit/arm mismatch: {path}')
                if any(batch['collections'][-1]['inventory_after_pickup'] != 0
                       for batch in audit['handoff_batches']):
                    raise RuntimeError('Uncollected deposit inventory')
                checks.append({'file': str(path.relative_to(folder)), 'valid': True})
                row = {'instance': instance, 'arm': arm, 'seed': seed,
                       'objective': info['objective'], 'baseline': base['objective'],
                       'saving': delta(base['objective'], info['objective']),
                       'iterations': info['iterations'], 'runtime': info['runtime'],
                       'pooled_pickup_attempts': info['pooled_pickup_attempts'],
                       'multi_receiver_audits': info['multi_receiver_audits'],
                       'multi_receiver_feasible': info['multi_receiver_feasible'],
                       'final_split_deposits': info['split_handoff_deposit_count'],
                       'deposit_count': info['handoff_deposit_count'],
                       'collection_visits': info['handoff_collection_visit_count']}
                records.append(row)
                arm_rows.append(row)
                for batch in audit['handoff_batches']:
                    if len(batch['collections']) > 1:
                        split_examples.append({'instance': instance, 'arm': arm, 'seed': seed,
                                               'objective': info['objective'], 'batch': batch})
            best = min(arm_rows, key=lambda r: r['objective'])
            grouped[arm] = {'best': best['objective'], 'mean': statistics.mean(r['objective'] for r in arm_rows),
                            'best_seed': best['seed'], 'best_split_deposits': best['final_split_deposits'],
                            'runs_with_split': sum(r['final_split_deposits'] > 0 for r in arm_rows),
                            'multi_receiver_audits': sum(r['multi_receiver_audits'] for r in arm_rows),
                            'multi_receiver_feasible': sum(r['multi_receiver_feasible'] for r in arm_rows),
                            'mean_iterations': statistics.mean(r['iterations'] for r in arm_rows)}
        instances.append({'instance': instance, 'baseline': base['objective'],
                          'm3_proven': bool(saved['info'].get('proven_optimal')), 'arms': grouped,
                          'pair_minus_split_best': delta(grouped['pair']['best'], grouped['split']['best']),
                          'single_minus_split_best': delta(grouped['flex_single']['best'], grouped['split']['best'])})
    by_key = {(r['instance'], r['seed'], r['arm']): r for r in records}
    comparisons = {}
    for arm in ('pair', 'flex_single'):
        values = [delta(by_key[instance, seed, arm]['objective'], by_key[instance, seed, 'split']['objective'])
                  for instance in settings['instances'] for seed in settings['seeds']]
        comparisons[arm + '_versus_split'] = {
            'split_wins': sum(v > 1e-6 for v in values), 'ties': sum(abs(v) <= 1e-6 for v in values),
            'split_losses': sum(v < -1e-6 for v in values), 'mean_absolute_distance_saving': statistics.mean(values)}
    stats = {'runs': len(records), 'audited_solutions': len(checks),
             'runs_with_split': {arm: sum(r['final_split_deposits'] > 0 for r in records if r['arm'] == arm) for arm in ARMS},
             'joint_multi_receiver_feasible': sum(r['multi_receiver_feasible'] for r in records if r['arm'] == 'split'),
             'elapsed_batch_seconds': jobs['elapsed'], 'paired_comparisons': comparisons}
    write(folder / 'summary.json', {'settings': settings, 'statistics': stats, 'instances': instances, 'runs': records})
    write(folder / 'verification.json', {'status': 'PASS', 'checks': checks, 'audited_solutions': len(checks)})
    write(folder / 'split_examples.json', split_examples)
    matched_best = sum(abs(r['single_minus_split_best']) <= 1e-6 for r in instances)
    lines = ['# M8 多接收车联合搜索：实现与三组对照', '',
             f"本轮 {len(records)} 次对照运行中，{matched_best}/{len(instances)} 例的联合多车搜索最好值与任选单接收车相同；"
             f"联合组最终含同批多车分取的运行数为 {stats['runs_with_split']['split']}。"
             '当前实验未验证出联合分取的额外里程收益。构造测试能够生成并保留分取，详见后文。', '',
             '## Material Passport', '',
             '- 状态：实际执行，原始 standard_same_day 100/200 客户数据；容量、时间窗与需求不变。',
             '- 固定同一保存的较好 M3 基础解；5 例使用此前强化解，100-10-1 使用此前已证明最优的解。',
             f"- 每组种子 {settings['seeds']}，每次 {settings['time']} 秒；最多 {settings['jobs']} 个 ALNS 进程并发，记录实际迭代数。",
             '- 三组都具有从多辆车收集附近回收任务、联合重分配给一辆车的相同任务调整操作。',
             '- pair：一次仅向原回收车交接；flex_single：接收车可另选，但一次只选一辆；split：一次可联合选择最多三辆接车。',
             '- 单接收车限制作用于一次修复，多个迭代仍可能累积形成同点多车分取；这是搜索算法对照，不是禁止分取的数学模型消融。',
             '- M3 基础解证明状态逐例记录；三个第二阶段均为 ALNS，不提供最优性证明。', '',
             '## 三种子最好结果', '',
             '| 实例 | M3 基础距离 | pair | flex_single | split | pair−split | single−split | split 最好解分取批次 |',
             '|---|---:|---:|---:|---:|---:|---:|---:|']
    for r in instances:
        g = r['arms']
        lines.append(f"| {r['instance']} | {r['baseline']:.6f} | {g['pair']['best']:.6f} | {g['flex_single']['best']:.6f} | "
                     f"{g['split']['best']:.6f} | {r['pair_minus_split_best']:.6f} | {r['single_minus_split_best']:.6f} | {g['split']['best_split_deposits']} |")
    lines += ['', '差值为正表示联合分取搜索得到的距离更短。最好值是三个种子的组合搜索结果，并非期望性能或最优值。', '',
              '## 逐种子配对比较', '',
              '| 对照 → split | split 胜/平/负 | 平均距离节省（可为负） |', '|---|---|---:|']
    for name, r in comparisons.items():
        lines.append(f"| {name} | {r['split_wins']}/{r['ties']}/{r['split_losses']} | {r['mean_absolute_distance_saving']:.6f} |")
    lines += ['', '## 是否真正出现一次放箱、多车分取', '',
              '| 实例 | pair 含分取运行数 | single 含分取运行数 | split 含分取运行数 | 联合多接车候选审计 | 联合多接车可行候选 |',
              '|---|---:|---:|---:|---:|---:|']
    for r in instances:
        g = r['arms']
        lines.append(f"| {r['instance']} | {g['pair']['runs_with_split']} | {g['flex_single']['runs_with_split']} | "
                     f"{g['split']['runs_with_split']} | {g['split']['multi_receiver_audits']} | {g['split']['multi_receiver_feasible']} |")
    lines += ['', '分取批次按同一发送车、同一交接事件合并，要求至少两辆不同接车；重复记录不会重复计作放箱。'
              '仅有多个独立交接点不算同批多车分取。完整时刻、数量和取箱后库存见 [split_examples.json](split_examples.json)。', '',
              '审计次数仅覆盖当前邻域生成并送审的候选；零可行候选不代表所有可能的联合分取均不可行。']
    lines += diagnostic_report(folder)
    lines += ['', '## 逐种子原始结果', '',
              '| 实例 | 组 | 种子 | 距离 | 迭代数 | 最终分取批次 |', '|---|---|---:|---:|---:|---:|']
    for r in records:
        lines.append(f"| {r['instance']} | {r['arm']} | {r['seed']} | {r['objective']:.6f} | {r['iterations']} | {r['final_split_deposits']} |")
    lines += ['', '## 审计与限制', '',
              f"- 重新加载 {len(checks)} 个解（6 个基础解、{len(records)} 个第二阶段），全部通过；代码、数据与基线来源哈希匹配。",
              '- 校验任务覆盖、固定配送骨架、O/R 数量与容量、一次转交、先放后取、两级时序、批次清空和距离复算。',
              '- 当前每次最多移动 3 个回收任务，联合修复候选为 16 个事件、6 辆接收车、每车至多 5 个位置，属于有限邻域。',
              '- 启发式扩大搜索会增加每次修复的成本；同预算更慢或更差不代表扩大的模型可行域更差。',
              '- 原始结果：[summary.json](summary.json)；审计：[verification.json](verification.json)；运行记录：[jobs.json](jobs.json)。']
    (folder / 'findings.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')
    source = Path(__file__).resolve()
    dest = folder / 'source' / source.relative_to(ROOT)
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, dest)
    write(folder / 'analysis_source_sha256.json', {str(source.relative_to(ROOT)): hashlib.sha256(source.read_bytes()).hexdigest()})
    print(json.dumps(stats, ensure_ascii=False))


if __name__ == '__main__':
    main()
