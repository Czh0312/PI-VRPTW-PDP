"""Frozen two-phase M8 small-case experiment: exact first, then timed ALNS."""
from __future__ import annotations

import argparse
from copy import deepcopy
import csv
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import time
from types import SimpleNamespace

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parents[2]
CASES = [f'S{i:02d}' for i in range(1, 10)]


def now():
    return datetime.now(timezone.utc).isoformat()


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name+f'.{os.getpid()}.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
    temp.replace(path)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def validate_freeze(manifest):
    assert sha(HERE/'manifest.json') == read(HERE/'manifest.sha256.json')['sha256'], 'Manifest changed'
    assert sha(Path(__file__)) == manifest['driver_sha256'], 'Driver changed'
    for relative, digest in manifest['source_hashes'].items():
        assert sha(HERE/'source'/relative) == digest, relative
    for relative, digest in manifest['input_hashes'].items():
        assert sha(HERE/relative) == digest, relative


def prepare(exact_seconds, alns_seconds):
    if (HERE/'manifest.json').exists():
        raise ValueError('Manifest already exists; use run or status')
    sys.path.insert(0, str(PROJECT))
    from tools.configuration.loader import load_routing_case, resolve_project_path
    from models.common.cycle import load_instance
    import psutil

    records = {}
    for case in CASES:
        source_case = PROJECT/f'output/experiments/small7_m8_alns300_20261024_v1/cases/{case}_m8.json'
        config, fleet = load_routing_case(source_case, use_pool_config=False)
        original = load_instance(config, fleet)
        assert original.model_number == 8 and len(original.orders) == 7
        assert len({o.customer for o in original.orders}) == 7
        assert list(original.batch_count_candidates) == list(range(1, 8))
        assert not config.get('initial_joint_result')
        frozen = deepcopy(config)
        record = dict(original_case=str(source_case), original_case_sha256=sha(source_case), files={})
        for key in ('data', 'orders', 'routing_config', 'operating_policy', 'economic_config',
                    'dataset_metadata', 'parameter_contract', 'batch_count_certificate',
                    'distance_matrix', 'duration_matrix'):
            if not isinstance(config.get(key), str):
                continue
            source = resolve_project_path(config[key]).resolve()
            target = HERE/'inputs'/case/(key+source.suffix)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(source.read_bytes())
            frozen[key] = str(target)
            record['files'][key] = dict(source=str(source), snapshot=str(target.relative_to(HERE)), sha256=sha(target))
        frozen.update(solver='exact', exact_time=exact_seconds, alns_time=alns_seconds,
                      alns_iter=10**9, seed=42, milp_options=dict(threads=1, formulation='compact'))
        target = HERE/'cases'/f'{case}.json'
        write(target, frozen)
        check = load_instance(*load_routing_case(target, use_pool_config=False))
        assert check.orders == original.orders
        assert vars(check.fleet) == vars(original.fleet)
        assert check.cost_profile.rates == original.cost_profile.rates
        assert check.batch_count_candidates == original.batch_count_candidates
        for a in original.network.nodes:
            for b in original.network.nodes:
                assert check.travel(a, b) == original.travel(a, b)
                assert check.distance(a, b) == original.distance(a, b)
        records[case] = record
    files = list((PROJECT/'models').rglob('*.py')) + list((PROJECT/'tools/configuration').rglob('*.py'))
    files += [PROJECT/'tools/__init__.py', PROJECT/'experiments/__init__.py',
              PROJECT/'experiments/runs/__init__.py', PROJECT/'experiments/runs/probe_model8_physical_bounds.py']
    hashes = {}
    for source in sorted(set(files)):
        relative = source.relative_to(PROJECT)
        target = HERE/'source'/relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(source.read_bytes())
        hashes[relative.as_posix()] = sha(target)
    write(HERE/'input_evidence.json', records)
    inputs = {p.relative_to(HERE).as_posix(): sha(p)
              for folder in ('inputs', 'cases') for p in (HERE/folder).rglob('*') if p.is_file()}
    inputs['input_evidence.json'] = sha(HERE/'input_evidence.json')
    memory = psutil.virtual_memory()
    manifest = dict(schema_version=1, registered_at=now(), cases=CASES, model=8, customers=7,
                    dataset='formal_benchmark27_spacetime_v4_1', K=list(range(1, 8)),
                    exact_seconds=exact_seconds, alns_seconds=alns_seconds, seed=42,
                    workers=1, exact_threads=1, formulation='compact', strengthening='physical_time',
                    alns_iterations=10**9, alns_budget_strategy='adaptive',
                    calendar_reconstruction_period=1, external_starts=False,
                    phase_order=['exact', 'alns'], driver_sha256=sha(Path(__file__)),
                    source_hashes=hashes, input_hashes=inputs,
                    exact_budget_scope='optimization_only; construction/extraction/audit separately reported',
                    alns_budget_scope='total algorithm including internal K allocation; offline audit separate',
                    environment=dict(interpreter=sys.executable, python=sys.version,
                        physical_cpus=psutil.cpu_count(logical=False), logical_cpus=psutil.cpu_count(),
                        total_memory_mib=memory.total//1024**2, available_memory_mib=memory.available//1024**2))
    write(HERE/'manifest.json', manifest)
    write(HERE/'manifest.sha256.json', dict(sha256=sha(HERE/'manifest.json')))
    summarize(manifest)
    print(json.dumps(dict(stage='prepared', cases=CASES, exact_seconds=exact_seconds,
                          alns_seconds=alns_seconds, source_files=len(hashes)), ensure_ascii=False), flush=True)


def job_path(method, case):
    return HERE/'runs'/method/case


def worker(method, case):
    manifest = read(HERE/'manifest.json')
    validate_freeze(manifest)
    sys.path.insert(0, str(HERE/'source'))
    from tools.configuration.loader import load_routing_case
    from models.common.cycle import load_instance
    p = load_instance(*load_routing_case(HERE/'cases'/f'{case}.json', use_pool_config=False))
    path = job_path(method, case)
    begun = time.perf_counter()
    if method == 'exact':
        from experiments.runs import probe_model8_physical_bounds as probe
        gp = probe.gp
        optimize = gp.Model.optimize
        last = [-100., None]

        def tracked(model, callback=None):
            def observe(m, where):
                if callback is not None:
                    callback(m, where)
                if where not in (gp.GRB.Callback.MIP, gp.GRB.Callback.MIPSOL):
                    return
                elapsed = m.cbGet(gp.GRB.Callback.RUNTIME)
                if where == gp.GRB.Callback.MIPSOL:
                    candidate = m.cbGet(gp.GRB.Callback.MIPSOL_OBJ)
                    last[1] = candidate if last[1] is None else min(last[1], candidate)
                if elapsed < last[0]+15 and where != gp.GRB.Callback.MIPSOL:
                    return
                bound = m.cbGet(gp.GRB.Callback.MIP_OBJBND if where == gp.GRB.Callback.MIP else gp.GRB.Callback.MIPSOL_OBJBND)
                if where == gp.GRB.Callback.MIP:
                    value = m.cbGet(gp.GRB.Callback.MIP_OBJBST)
                    if abs(value) < 1e99:
                        last[1] = value
                write(path/'live.json', dict(method=method, case=case, optimize_seconds=elapsed,
                      best_objective=last[1], best_bound=bound if abs(bound) < 1e99 else None, updated_at=now()))
                last[0] = elapsed
            return optimize(model, observe)

        gp.Model.optimize = tracked
        args = SimpleNamespace(seconds=manifest['exact_seconds'], seed=42,
                               formulation='compact', solution_limit=None)
        summary = probe.run(args, p, 'physical_time', path)
        result_file = 'physical_time_result.json' if summary['sol_count'] else None
    else:
        from models.routing.model_8.alns import solve_alns
        result = solve_alns(p, max_time=manifest['alns_seconds'], max_iterations=10**9,
                            seed=42, initial_result=None, budget_strategy='adaptive',
                            calendar_reconstruction_period=1)
        write(path/'result.json', result)
        info = result['info']
        summary = dict(status=info.get('status'), objective=info.get('objective'),
                       proven_optimal=False, best_bound=None, mip_gap=None,
                       iterations=info.get('iterations'), first_solver_incumbent_seconds=None,
                       build_time=None, mip_runtime=None, algorithm_seconds=time.perf_counter()-begun,
                       batch_count_candidates=list(p.batch_count_candidates), complete_count_domain=True,
                       selected_K=result.get('cycle_batch_count'), pool=result.get('container_pool_size'),
                       budget_strategy='adaptive', calendar_reconstruction_period=1,
                       mip_start_supplied=False)
        result_file = 'result.json'
    summary.update(case=case, method=method, result_file=result_file, seed=42,
                   budget_seconds=manifest[f'{method}_seconds'], worker_wall_seconds=time.perf_counter()-begun)
    write(path/'summary.json', summary)
    print(json.dumps(dict(stage='worker_complete', case=case, method=method,
                          status=summary['status'], objective=summary['objective']), ensure_ascii=False), flush=True)


def verify_job(method, case):
    manifest = read(HERE/'manifest.json')
    validate_freeze(manifest)
    sys.path.insert(0, str(HERE/'source'))
    from tools.configuration.loader import load_routing_case
    from models.common.cycle import load_instance
    from models.common.cycle.audit import audit_solution
    from models.cost.comparison import evaluate_comparison_cost
    path = job_path(method, case)
    summary = read(path/'summary.json')
    p = load_instance(*load_routing_case(HERE/'cases'/f'{case}.json', use_pool_config=False))
    assert list(p.batch_count_candidates) == list(range(1, 8))
    summary.update(valid=False, periodic_valid=False)
    if summary['result_file']:
        result = read(path/summary['result_file'])
        begun = time.perf_counter()
        audit = audit_solution(p, result)
        write(path/'independent_audit.json', audit)
        if summary['objective'] is not None:
            assert audit['valid'] and audit['periodic_replay']['valid'], audit.get('errors')
            cost = evaluate_comparison_cost(p, result, p.cost_profile.config, audit=audit)
            assert math.isclose(cost['total'], summary['objective'], rel_tol=1e-7, abs_tol=1e-6)
            write(path/'independent_cost.json', cost)
            summary.update(valid=True, periodic_valid=True, objective=audit['objective'],
                           selected_K=result['cycle_batch_count'], pool=result['container_pool_size'])
        summary['offline_audit_seconds'] = time.perf_counter()-begun
    if summary.get('proven_optimal'):
        assert method == 'exact' and summary['status'] == 'OPTIMAL' and summary['valid']
        assert math.isclose(summary['objective'], summary['best_bound'], abs_tol=1e-5, rel_tol=1e-7)
    summary['verified_at'] = now()
    write(path/'summary.json', summary)
    write(path/'complete.json', dict(verified_at=now(), summary_sha256=sha(path/'summary.json'),
                                    files={p.name: sha(p) for p in path.glob('*.json') if p.name != 'complete.json'}))


def gap_metrics(exact, alns):
    result = dict(optimality_gap_percent=None, deviation_to_mip_incumbent_percent=None,
                  gap_upper_bound_from_lower_bound_percent=None)
    if not alns or not alns.get('valid'):
        return result
    z = alns['objective']
    if exact and exact.get('valid'):
        incumbent = exact['objective']
        result['deviation_to_mip_incumbent_percent'] = 100*(z-incumbent)/abs(incumbent) if incumbent else None
        if exact.get('proven_optimal'):
            if z < incumbent-max(1e-5, abs(incumbent)*1e-7):
                raise ValueError('ALNS beats a proved optimum: investigate domains/formulation/audit')
            result['optimality_gap_percent'] = max(0., 100*(z-incumbent)/abs(incumbent)) if incumbent else None
    bound = exact.get('best_bound') if exact else None
    if bound is not None and bound > 0:
        if bound > z+max(1e-5, abs(z)*1e-7):
            raise ValueError('MIP lower bound exceeds audited ALNS cost')
        result['gap_upper_bound_from_lower_bound_percent'] = max(0., 100*(z-bound)/bound)
    return result


def completed(method, case):
    path = job_path(method, case)
    if not (path/'complete.json').exists():
        return None
    item = read(path/'complete.json')
    assert sha(path/'summary.json') == item['summary_sha256']
    return read(path/'summary.json')


def summarize(manifest, active=None):
    rows, counts = [], dict(exact_completed=0, exact_valid=0, exact_optimal=0, alns_completed=0, alns_valid=0)
    for case in CASES:
        exact, alns = completed('exact', case), completed('alns', case)
        for method, item in (('exact', exact), ('alns', alns)):
            if item:
                counts[f'{method}_completed'] += 1
                counts[f'{method}_valid'] += bool(item.get('valid'))
        counts['exact_optimal'] += bool(exact and exact.get('proven_optimal'))
        row = dict(case=case, exact_status=exact['status'] if exact else 'NOT_COMPLETED',
                   exact_objective=exact.get('objective') if exact else None,
                   exact_lower_bound=exact.get('best_bound') if exact else None,
                   exact_native_gap=exact.get('mip_gap') if exact else None,
                   exact_proven_optimal=bool(exact and exact.get('proven_optimal')),
                   exact_K=exact.get('selected_K') if exact else None, exact_pool=exact.get('pool') if exact else None,
                   exact_optimize_seconds=exact.get('mip_runtime') if exact else None,
                   alns_status=alns['status'] if alns else 'NOT_COMPLETED',
                   alns_objective=alns.get('objective') if alns else None,
                   alns_K=alns.get('selected_K') if alns else None, alns_pool=alns.get('pool') if alns else None)
        row.update(gap_metrics(exact, alns))
        rows.append(row)
    finished = counts['exact_completed'] == counts['alns_completed'] == 9
    state = dict(**counts, planned_cases=9, planned_calls=18, active=active,
                 phase='COMPLETE' if finished else ('EXACT' if counts['exact_completed'] < 9 else 'ALNS'),
                 updated_at=now())
    write(HERE/'progress.json', state)
    write(HERE/'comparison.json', rows)
    with (HERE/'comparison.csv').open('w', encoding='utf-8-sig', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader(); writer.writerows(rows)
    fmt = lambda x: '—' if x is None else f'{x:.4f}'
    gaps = [r['optimality_gap_percent'] for r in rows if r['optimality_gap_percent'] is not None]
    lines = ['# 模型八：全部9个7顾客算例精确求解与限时ALNS', '',
        f"阶段：{state['phase']}；精确完成{counts['exact_completed']}/9，可行{counts['exact_valid']}/9，原生最优{counts['exact_optimal']}/9；ALNS完成{counts['alns_completed']}/9，可行{counts['alns_valid']}/9。", '',
        f"精确每例优化上限{manifest['exact_seconds']}秒（建模/审计另计）；随后ALNS每例总预算{manifest['alns_seconds']}秒。固定种子42，串行、单线程精确、compact、physical_time增强。",
        '使用同一份冻结物理输入、完整K=1～7、原时钟及各K细分精度。两种算法分别冷启动；不互相提供初解。ALNS迭代上限10^9、adaptive K预算、日历重建周期1。', '',
        '最优性GAP=(ALNS成本−已证最优值)/已证最优值×100%，仅在本轮精确原生OPTIMAL且独立审计通过时填写。',
        '未闭合算例的ALNS与MIP当前最好解差值不是最优性GAP；下界推导的GAP上界另列CSV，不混入平均最优性GAP。', '',
        '| 算例 | 精确状态 | 精确成本 | 全域下界 | ALNS成本 | 最优性GAP% | 相对MIP当前解差值% |',
        '|---|---|---:|---:|---:|---:|---:|']
    for row in rows:
        lines.append('| '+' | '.join([row['case'], row['exact_status'], fmt(row['exact_objective']),
            fmt(row['exact_lower_bound']), fmt(row['alns_objective']), fmt(row['optimality_gap_percent']),
            fmt(row['deviation_to_mip_incumbent_percent'])])+' |')
    lines += ['', f"有可靠本轮最优基准且ALNS可行的算例数：{len(gaps)}；其单次ALNS平均最优性GAP：{fmt(sum(gaps)/len(gaps) if gaps else None)}%。",
        '单种子结果不代表多次运行均值。TIME_LIMIT无解不表示不可行；所有超时、无解及失败均保留。',
        '每个最终方案在独立进程中做业务、成本复算与两周期审计。所有计算只读取冻结source/、inputs/和cases/。',
        '完整结果与日志见runs/；当前活动和进度见progress.json。此批次未更改生产求解器。', '']
    (HERE/'report.md').write_text('\n'.join(lines), encoding='utf-8')
    return state


def execute(method, case, manifest):
    import psutil
    path = job_path(method, case)
    path.mkdir(parents=True, exist_ok=True)
    if (path/'summary.json').exists():
        raise RuntimeError(f'Incomplete prior attempt requires inspection: {method}/{case}')
    budget = manifest[f'{method}_seconds']
    env = dict(os.environ, OMP_NUM_THREADS='1', OPENBLAS_NUM_THREADS='1', MKL_NUM_THREADS='1',
               NUMEXPR_NUM_THREADS='1', PYTHONUTF8='1')
    command = [sys.executable, str(Path(__file__)), 'worker', '--method', method, '--case', case]
    begun, peak, low_since, reason = time.perf_counter(), 0, None, None
    with (path/'stdout.log').open('w', encoding='utf-8') as log:
        process = subprocess.Popen(command, cwd=PROJECT, stdout=log, stderr=subprocess.STDOUT,
                                   env=env, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        while process.poll() is None:
            elapsed = time.perf_counter()-begun
            try:
                rss = psutil.Process(process.pid).memory_info().rss//1024**2
            except psutil.Error:
                rss = 0
            peak = max(peak, rss)
            available = psutil.virtual_memory().available//1024**2
            if rss > 2048 and available < 128:
                low_since = low_since or time.perf_counter()
            else:
                low_since = None
            if (HERE/'STOP').exists():
                reason = 'USER_STOP'
            elif elapsed > budget+600:
                reason = 'PROCESS_WALL_LIMIT'
            elif low_since and time.perf_counter()-low_since > 60:
                reason = 'MEMORY_PRESSURE'
            if reason:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill(); process.wait()
                break
            active = dict(method=method, case=case, pid=process.pid, wall_seconds=elapsed,
                          rss_mib=rss, peak_rss_mib=peak, available_memory_mib=available)
            live = path/'live.json'
            if live.exists():
                active['solver_live'] = read(live)
            write(HERE/'active.json', active)
            time.sleep(5)
    write(path/'resource_usage.json', dict(wall_seconds=time.perf_counter()-begun,
                                         peak_rss_mib=peak, returncode=process.returncode, stopped_reason=reason))
    if process.returncode == 0 and reason is None:
        try:
            with (path/'verify.log').open('w', encoding='utf-8') as log:
                checked = subprocess.run([sys.executable, str(Path(__file__)), 'verify-job', '--method', method, '--case', case],
                                         cwd=PROJECT, env=env, stdout=log, stderr=subprocess.STDOUT, timeout=180)
            if checked.returncode == 0:
                return
            reason = 'OFFLINE_AUDIT_FAILED'
        except subprocess.TimeoutExpired:
            reason = 'OFFLINE_AUDIT_TIMEOUT'
    reason = reason or 'WORKER_ERROR'
    failure = dict(case=case, method=method, status=reason, objective=None, best_bound=None,
                   valid=False, periodic_valid=False, proven_optimal=False, failed_at=now())
    write(path/'failed_summary.json', failure)
    write(path/'summary.json', failure)
    write(path/'complete.json', dict(summary_sha256=sha(path/'summary.json'), failed=True, reason=reason))


def run():
    manifest = read(HERE/'manifest.json')
    validate_freeze(manifest)
    lock = HERE/'driver.lock'
    if lock.exists():
        old_pid = int(lock.read_text(encoding='utf-8'))
        import psutil
        if psutil.pid_exists(old_pid):
            raise RuntimeError(f'Batch already active: PID {old_pid}')
    lock.write_text(str(os.getpid()), encoding='utf-8')
    try:
        for method in manifest['phase_order']:
            for case in CASES:
                if (HERE/'STOP').exists():
                    summarize(manifest)
                    return
                if completed(method, case):
                    continue
                print(json.dumps(dict(stage='start', method=method, case=case,
                                      seconds=manifest[f'{method}_seconds'], timestamp=now())), flush=True)
                summarize(manifest, dict(method=method, case=case))
                execute(method, case, manifest)
                validate_freeze(manifest)
                print(json.dumps(summarize(manifest), ensure_ascii=False), flush=True)
        write(HERE/'verification.json', dict(completed_at=now(), source_and_inputs_verified=True,
                    scope='9 exact calls then 9 ALNS calls; per-job independent audit records retained',
                    selected_unit_test_suite='gap metric tests recorded separately'))
    finally:
        if lock.exists() and lock.read_text(encoding='utf-8') == str(os.getpid()):
            lock.unlink()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('prepare', 'run', 'status', 'worker', 'verify-job'))
    parser.add_argument('--exact-seconds', type=int, default=1800)
    parser.add_argument('--alns-seconds', type=int, default=300)
    parser.add_argument('--method', choices=('exact', 'alns'))
    parser.add_argument('--case', choices=CASES)
    args = parser.parse_args()
    if args.action == 'prepare':
        prepare(args.exact_seconds, args.alns_seconds)
    elif args.action == 'run':
        run()
    elif args.action == 'status':
        print(json.dumps(summarize(read(HERE/'manifest.json'), read(HERE/'active.json') if (HERE/'active.json').exists() else None), ensure_ascii=False))
    elif args.action == 'worker':
        worker(args.method, args.case)
    else:
        verify_job(args.method, args.case)


if __name__ == '__main__':
    main()
