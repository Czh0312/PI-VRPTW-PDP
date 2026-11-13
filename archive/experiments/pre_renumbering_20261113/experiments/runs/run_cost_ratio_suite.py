"""Frozen, resumable execution of the prepared five-model cost-ratio suite."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import csv
from datetime import datetime, timezone, timedelta
import hashlib
import json
import math
import os
from pathlib import Path
import random
import shutil
import subprocess
import sys
import threading
import time
import traceback

ROOT = Path(__file__).resolve().parents[2]
MODELS = (3, 5, 6, 7, 8)


def now():
    return datetime.now(timezone(timedelta(hours=8))).isoformat()


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def write(path, value):
    path = Path(path)
    temporary = path.with_name(path.name+'.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)+'\n', encoding='utf-8')
    temporary.replace(path)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def register(args, out):
    sys.path.insert(0, str(ROOT))
    from tools.configuration.loader import load_routing_case, resolve_project_path
    from models.common.cycle import load_instance
    from models.cost.joint_batch import JointCostProfile
    from experiments.runs.run_model6_comparison import freeze_case, snapshot_source
    if out.exists() and any(out.iterdir()):
        raise ValueError(f'Refusing to overwrite existing study: {out}')
    for folder in ('inputs', 'cases', 'results', 'logs'):
        (out/folder).mkdir(parents=True, exist_ok=True)
    source_manifest = resolve_project_path(args.manifest)
    prepared = read(source_manifest)
    pricing = resolve_project_path(prepared['economic_config'])
    if sha(pricing) != prepared['economic_config_sha256']:
        raise ValueError('Prepared economic profile changed')
    profile = JointCostProfile(read(pricing))
    assert profile.rates['e1_distance_cost_per_km'] == .5
    assert profile.rates['container_pool_cost_per_period'] == 25
    assert profile.rates['cleaning_cost_per_return'] == .2
    shutil.copy2(source_manifest, out/'inputs/prepared_manifest.json')
    shutil.copy2(pricing, out/'inputs/common_economic.json')
    jobs, hashes, checks = [], {}, []
    for index, record in enumerate(prepared['cases']):
        model = record['model']
        key = f'{index:02d}_M{model}_alns_{prepared["seed"]}'
        config, evidence = freeze_case(record['case_path'], out, key)
        hashes.update(evidence)
        config.update(economic_config=str(out/'inputs/common_economic.json'), solver='alns',
                      alns_time=prepared['alns_seconds'], alns_iter=prepared['iterations'],
                      seed=prepared['seed'], save_plots=False)
        target = out/'cases'/f'{key}.json'
        write(target, config)
        problem = load_instance(*load_routing_case(target, use_pool_config=False))
        if model >= 6:
            assert config['joint_batch_options']['batch_count_candidates'] == 'all_positive_integers'
            upper = problem.cycle_duration//(2*problem.minimum_phase_ticks)
            assert problem.batch_count_candidates == tuple(range(1, upper+1))
            assert list(problem.batch_count_candidates) == record['batch_count_candidates']
            assert problem.cost_profile.hash == profile.hash
        checks.append(dict(id=key, model=model, family=record['family'], orders=len(problem.orders),
                           demand=problem.total, batch_count_max=(upper if model >= 6 else None),
                           passed=True))
        jobs.append(dict(id=key, family=record['family'], model=model, method='alns',
                         seed=prepared['seed'], budget=prepared['alns_seconds'],
                         iterations=prepared['iterations'], case_path=str(target),
                         customers=len(problem.network.customers), demand=problem.total))
    assert len(jobs) == 30 and len({j['family'] for j in jobs}) == 6
    random.Random(20261007).shuffle(jobs)
    sources = snapshot_source(out)
    frozen_inputs = {str(p.relative_to(out)): sha(p)
                     for folder in ('inputs', 'cases') for p in sorted((out/folder).iterdir()) if p.is_file()}
    write(out/'preflight_checks.json', checks)
    plan = dict(registered_at=now(), status='REGISTERED', jobs=jobs, workers=args.workers,
                timeout_overhead=args.timeout_overhead, models=list(MODELS),
                commit=subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
                working_tree_modified=True, input_hashes=hashes, frozen_input_hashes=frozen_inputs,
                source_hashes=sources, economic_profile=profile.config, economic_profile_hash=profile.hash,
                prepared_manifest_sha256=sha(source_manifest), python=sys.version, interpreter=sys.executable,
                batch_count_domain='all_positive_integers_subject_to_cycle_and_minimum_phases',
                selection='Six existing families and all five models; no selection based on outcomes.',
                objectives={'3': 'distance; separately priced', '5': 'distance; separately priced',
                            '6': 'joint cost', '7': 'joint cost', '8': 'joint cost'},
                limitations=['One seed; finite search budget, not an optimality certificate.',
                             'Synthetic cost units; not observed CNY.',
                             'Four workers share CPU; wall budgets are soft, actual runtimes are recorded.',
                             'Calendar K differs from active delivery batch count.'])
    write(out/'experiment.json', plan)
    (out/'working_tree.patch').write_text(subprocess.check_output(
        ['git', 'diff', '--binary'], cwd=ROOT, text=True, encoding='utf-8'), encoding='utf-8')
    print(f'REGISTERED {len(jobs)} runs, {args.workers} workers, {prepared["alns_seconds"]}s each', flush=True)
    return plan


def worker(out, job_id):
    plan = read(out/'experiment.json')
    job = next(j for j in plan['jobs'] if j['id'] == job_id)
    target = out/'results'/job_id
    target.mkdir(exist_ok=False)
    sys.path.insert(0, str(out/'source_snapshot'))
    if (out/'source_snapshot/configuration/loader.py').is_file():
        from configuration.loader import load_routing_case
    else:
        from tools.configuration.loader import load_routing_case
    from models.common.cycle import load_instance
    from models.common.cycle.audit import audit_solution
    from models.routing.service import solve_routing
    from models.cost.comparison import evaluate_comparison_cost
    for name, digest in plan['source_hashes'].items():
        if sha(out/'source_snapshot'/name) != digest:
            raise ValueError(f'Frozen code hash mismatch: {name}')
    for name, digest in plan['frozen_input_hashes'].items():
        if sha(out/name) != digest:
            raise ValueError(f'Frozen input hash mismatch: {name}')
    config, fleet = load_routing_case(job['case_path'], use_pool_config=False)
    problem = load_instance(config, fleet)
    write(target/'input_manifest.json', dict(config=config, fleet=fleet,
          economic_profile_hash=plan['economic_profile_hash'], code_verified=True, inputs_verified=True))
    began = time.perf_counter()
    stop = threading.Event()
    def heartbeat():
        while not stop.is_set():
            cpu = os.times()
            write(target/'live_status.json', dict(job_id=job_id, stage='solving', pid=os.getpid(),
                  updated_at=now(), elapsed_seconds=time.perf_counter()-began,
                  cpu_seconds=cpu.user+cpu.system))
            stop.wait(10)
    monitor = threading.Thread(target=heartbeat, daemon=True)
    monitor.start()
    try:
        result = solve_routing(model=job['model'], method='alns', problem=problem,
                               alns_time=job['budget'], alns_iter=job['iterations'], seed=job['seed'])
    finally:
        stop.set()
        monitor.join(timeout=2)
    elapsed = time.perf_counter()-began
    write(target/'result.json', result)
    info = result['info']
    row = {key: value for key, value in job.items() if key != 'case_path'}
    row.update(status=info['status'], valid=False, cost=None, pool=None, k=None, delivery_batches=None,
               solve_wall_seconds=elapsed, iterations_completed=info.get('iterations'),
               proven_optimal=bool(info.get('proven_optimal')), proven_infeasible=bool(info.get('proven_infeasible')),
               objective=info.get('objective'), completed_at=now(), audit_errors=[])
    if info.get('objective') is not None:
        audit = audit_solution(problem, result)
        write(target/'independent_audit.json', audit)
        row.update(valid=bool(audit['valid']), audit_errors=audit['errors'])
        if audit['valid']:
            assert audit['periodic_replay']['valid']
            cost = evaluate_comparison_cost(problem, result, config['economic_config'], audit=audit)
            assert cost['economic_profile_hash'] == plan['economic_profile_hash']
            assert math.isclose(cost['total'], sum(cost['components'].values()), abs_tol=1e-6)
            write(target/'comparison_cost.json', cost)
            row.update(cost=cost['total'], pool=cost['container_pool_size'], cost_components=cost['components'],
                       economic_profile_hash=cost['economic_profile_hash'], periodic_valid=True,
                       distances=audit['distances'], minimum_pool=audit['minimum_pool_for_schedule'],
                       e1_trips=audit['e1_trip_count'], e2_trips=audit['e2_trip_count'],
                       e1_vehicles=audit['e1_vehicle_count'], e2_vehicles=audit['e2_vehicle_count'],
                       working_minutes=audit['time_metrics']['working_minutes'],
                       k=result.get('cycle_batch_count', len(problem.operating_policy.batches)),
                       delivery_batches=result.get('delivery_batch_count', sum(bool(v) for v in problem.batch_demands.values())))
        else:
            row.update(status='AUDIT_FAILED', proven_optimal=False)
    if job['model'] >= 6 and 'count_search' in result:
        counts = result['count_search']
        assert [c['batch_count'] for c in counts] == list(problem.batch_count_candidates)
        assert result['info']['time_budget'] == job['budget']
        candidates = [c for c in counts if c.get('valid')]
        if row['valid']:
            assert math.isclose(min(c['objective'] for c in candidates), row['cost'], abs_tol=1e-6)
            assert math.isclose(info['objective'], row['cost'], abs_tol=1e-6)
        write(target/'count_search.json', counts)
        row.update(candidate_count=len(counts), valid_candidate_count=len(candidates),
                   explored_count=sum(c['status'] != 'NOT_RUN_TIME_LIMIT' for c in counts),
                   unrun_count=sum(c['status'] == 'NOT_RUN_TIME_LIMIT' for c in counts),
                   proved_infeasible_count=sum(c['proven_infeasible'] for c in counts),
                   automatic_selection_checked=bool(row['valid']))
    elif job['model'] >= 6:
        assert info['batch_count_candidates'] == list(problem.batch_count_candidates)
        assert info['solver_config']['time_limit'] == job['budget']
        row.update(candidate_count=len(problem.batch_count_candidates),
                   exact_strategy=info['exact_strategy'], complete_count_domain=info['complete_count_domain'],
                   automatic_selection_checked=bool(row['valid']))
    write(target/'row.json', row)
    write(target/'live_status.json', dict(job_id=job_id, stage='complete', updated_at=now(),
          elapsed_seconds=elapsed, status=row['status'], valid=row['valid']))
    print(json.dumps(row, ensure_ascii=False), flush=True)


def execute(out, plan, job):
    log = out/'logs'/f'{job["id"]}.log'
    command = [sys.executable, str(out/'source_snapshot/experiments/runs/run_cost_ratio_suite.py'),
               '--output', str(out), '--job', job['id']]
    try:
        with log.open('w', encoding='utf-8') as handle:
            process = subprocess.run(command, cwd=ROOT, stdout=handle, stderr=subprocess.STDOUT,
                env={**os.environ, 'PYTHONUTF8': '1', 'PYTHONIOENCODING': 'utf-8', 'PYTHONHASHSEED': '0',
                     'OMP_NUM_THREADS': '1', 'OPENBLAS_NUM_THREADS': '1', 'MKL_NUM_THREADS': '1'},
                timeout=job['budget']+plan['timeout_overhead'])
        if process.returncode:
            raise RuntimeError(f'Worker exit {process.returncode}; see {log.name}')
        return read(out/'results'/job['id']/'row.json')
    except Exception as exc:
        row = {key: value for key, value in job.items() if key != 'case_path'}
        row.update(status='HARD_TIMEOUT' if isinstance(exc, subprocess.TimeoutExpired) else 'ERROR',
                   valid=False, cost=None, pool=None, k=None, delivery_batches=None,
                   completed_at=now(), error=str(exc))
        write(out/'results'/f'{job["id"]}_failure.json', row)
        return row


def run(out, plan):
    rows = read(out/'rows.json') if (out/'rows.json').exists() else []
    completed = {row['id'] for row in rows}
    pending = []
    for job in plan['jobs']:
        if job['id'] in completed:
            continue
        saved = out/'results'/job['id']/'row.json'
        if saved.exists():
            rows.append(read(saved))
        elif (out/'results'/job['id']).exists():
            raise ValueError(f'Incomplete prior run retained: {job["id"]}; inspect before retrying')
        else:
            pending.append(job)
    write(out/'rows.json', rows)
    write(out/'progress.json', dict(status='RUNNING', updated_at=now(), planned=len(plan['jobs']), completed=len(rows)))
    with ThreadPoolExecutor(max_workers=plan['workers']) as executor:
        futures = {executor.submit(execute, out, plan, job): job for job in pending}
        for future in as_completed(futures):
            row = future.result()
            rows.append(row)
            write(out/'rows.json', rows)
            write(out/'progress.json', dict(status='RUNNING', updated_at=now(), planned=len(plan['jobs']),
                  completed=len(rows), valid=sum(r['valid'] for r in rows)))
            print(f'{len(rows)}/{len(plan["jobs"])} {row["family"]} M{row["model"]}: '
                  f'{row["status"]}, cost={row["cost"]}, K={row.get("k")}, valid={row["valid"]}', flush=True)
    summarize(out)


def summarize(out):
    plan, rows = read(out/'experiment.json'), read(out/'rows.json')
    rows.sort(key=lambda r: (r['family'], r['model']))
    fields = sorted({key for row in rows for key, value in row.items() if not isinstance(value, (dict, list))})
    with (out/'runs.csv').open('w', newline='', encoding='utf-8-sig') as handle:
        writer = csv.DictWriter(handle, fields, extrasaction='ignore')
        writer.writeheader()
        writer.writerows(rows)
    valid = [r for r in rows if r['valid']]
    joint = [r for r in valid if r['model'] >= 6]
    summary = dict(completed_at=now(), planned=len(plan['jobs']), completed=len(rows), valid=len(valid),
                   failures=[r for r in rows if not r['valid']],
                   periodic_audits_passed=sum(r.get('periodic_valid', False) for r in rows),
                   automatic_selection_checks=sum(r.get('automatic_selection_checked', False) for r in rows),
                   joint_actual_multibatch=sum(r['delivery_batches'] > 1 for r in joint),
                   joint_selected_k_above_three=sum(r['k'] > 3 for r in joint),
                   joint_pool_reduced=sum(r['pool'] < r['demand'] for r in joint),
                   economic_profile_hash=plan['economic_profile_hash'])
    write(out/'summary.json', summary)
    lines = ['# 一级距离成本0.5：六例五模型正式实验', '',
             f'完成{len(rows)}/{len(plan["jobs"])}组，独立路线及双周期审计通过{len(valid)}组。', '',
             '一级距离系数0.5、二级0.05647058823529412、箱池25、追踪0.1、磨损0.5、实际回库清洗0.2。',
             '每例每模型一次ALNS调用，总时间预算300秒，迭代上限1000000，seed42，4路并行。',
             'M6–M8允许K=1至390，范围由780分钟周期和每阶段至少1分钟推导。等长模型自动细分非整除K的时钟。', '',
             '| 算例 | 客户数 | 需求箱次 | M3成本 | M5成本 | M6成本 | M7成本 | M8成本 | 本轮最低 |',
             '|---|---:|---:|---:|---:|---:|---:|---:|---|']
    for family in dict.fromkeys(j['family'] for j in sorted(plan['jobs'], key=lambda j: j['id'])):
        group = {r['model']: r for r in rows if r['family'] == family}
        values = [f'{group[m]["cost"]:.2f}' if m in group and group[m]['valid'] else '—' for m in MODELS]
        feasible = [r for r in group.values() if r['valid']]
        winner = min(feasible, key=lambda r: r['cost']) if feasible else None
        reference = next(iter(group.values()), next(j for j in plan['jobs'] if j['family'] == family))
        lines.append(f'| {family} | {reference["customers"]} | {reference["demand"]} | '
                     +' | '.join(values)+f' | {"M"+str(winner["model"]) if winner else "—"} |')
    lines += ['', '## 批次、箱池与搜索覆盖', '',
              '| 算例 | 模型 | 日历K | 实际配送批次 | 箱池N | 需求Q | 已进入K数 | 有效候选 | 已证不可行K | 未运行K | 求解秒数 |',
              '|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|']
    for r in rows:
        if r['model'] >= 6:
            lines.append('| '+ ' | '.join(str(r.get(k, '—')) for k in
                ('family', 'model', 'k', 'delivery_batches', 'pool', 'demand', 'explored_count',
                 'valid_candidate_count', 'proved_infeasible_count', 'unrun_count'))
                + f' | {r.get("solve_wall_seconds", 0):.2f} |')
    lines += ['', '## 解释范围', '',
              'M3/M5保持原距离目标并按本轮同一费用计价；M6–M8优化总成本。单种子、有限时间结果属于已找到的可行解，不代表全局最优。',
              '搜索完全部K也不代表每个K均已求得最优；未搜索或限时未找到可行解均不能等同于不可行。日历K与实际配送批次分别报告。',
              '代码和输入分别冻结在source_snapshot与inputs/cases，逐次结果、独立审计和费用在results。',
              '相对旧实验同时改变了价格和K允许域，未经同价重计不能把全部费用差异归因于算法改进。']
    if summary['failures']:
        lines += ['', '## 未通过运行', '']+[f'- {r["id"]}: {r["status"]}; {r.get("error", r.get("audit_errors"))}' for r in summary['failures']]
    (out/'report.md').write_text('\n'.join(lines)+'\n', encoding='utf-8')
    write(out/'progress.json', dict(status='COMPLETE' if len(rows) == len(plan['jobs']) else 'PARTIAL',
          updated_at=now(), planned=len(plan['jobs']), completed=len(rows), valid=len(valid)))
    print(f'COMPLETE {len(valid)}/{len(rows)} valid: {out/"report.md"}', flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', default='config/experiments/pool25_truck0p5_allK_20261007/manifest.json')
    parser.add_argument('--output', required=True)
    parser.add_argument('--workers', type=int, default=4)
    parser.add_argument('--timeout-overhead', type=float, default=180)
    parser.add_argument('--job')
    parser.add_argument('--resume', action='store_true')
    parser.add_argument('--summarize', action='store_true')
    args = parser.parse_args()
    out = Path(args.output).resolve()
    if args.job:
        worker(out, args.job)
    elif args.summarize:
        summarize(out)
    else:
        if not 1 <= args.workers <= 8:
            parser.error('workers must be in 1..8')
        plan = read(out/'experiment.json') if args.resume else register(args, out)
        run(out, plan)


if __name__ == '__main__':
    main()
