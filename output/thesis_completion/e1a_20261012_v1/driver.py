"""Freeze, execute and resume thesis experiments with immutable attempts.

Examples (use the repository runtime launcher on Windows):
  python -m experiments.runs.run_thesis_completion prepare --phase e0 --suite config/cases/thesis_completion/pilot_v1/manifest.json --baseline output/thesis_completion/baseline_92926f9c2/source_snapshot --output output/thesis_completion/e0_v1
  python -m experiments.runs.run_thesis_completion run --output output/thesis_completion/e0_v1
  python -m experiments.runs.run_thesis_completion status --output output/thesis_completion/e0_v1
  python -m experiments.runs.run_thesis_completion run --output output/thesis_completion/e0_v1 --retry-failed

E0 uses only the frozen original. E1a compares that original to the new scheduler.
Retries preserve the first-attempt denominator; completed means terminal, not
necessarily feasible. A STOP file requests shutdown after active jobs finish.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
from copy import deepcopy
import csv
from datetime import datetime, timezone
import hashlib
from importlib import import_module, metadata
import json
import math
import os
from pathlib import Path
import platform
import random
import shutil
import socket
import subprocess
import sys
import time
import traceback
import uuid
import zipfile

ROOT = Path(__file__).resolve().parents[2]
SOURCE_FOLDERS = ('models', 'cli', 'experiments', 'tools')


def now():
    return datetime.now(timezone.utc).isoformat()


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name+f'.{uuid.uuid4().hex}.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)+'\n', encoding='utf-8')
    os.replace(temporary, path)


def snapshot(source, target):
    """Only portable project source; exclude machine-local runtime helpers."""
    paths = [p for directory in SOURCE_FOLDERS for p in (source/directory).rglob('*.py')]
    paths += [source/'main.py']
    hashes = {}
    for path in sorted(paths):
        relative = path.relative_to(source)
        if path.name in ('project_runtime.py',) or '__pycache__' in relative.parts:
            continue
        dest = target/relative
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, dest)
        hashes[relative.as_posix()] = sha(dest)
    return hashes


def verify(out, manifest):
    if sha(out/'manifest.json') != read(out/'manifest.sha256.json')['sha256']:
        raise ValueError('Frozen manifest changed')
    for group in ('source_hashes', 'input_hashes'):
        for name, digest in manifest[group].items():
            if sha(out/name) != digest:
                raise ValueError(f'Frozen {group} mismatch: {name}')


def restore_sources(out):
    manifest = read(out/'manifest.json')
    if sha(out/'manifest.json') != read(out/'manifest.sha256.json')['sha256']:
        raise ValueError('Frozen manifest changed')
    restored = 0
    for name, digest in manifest['source_archive_hashes'].items():
        archive = out/name
        if sha(archive) != digest:
            raise ValueError(f'Source archive hash mismatch: {name}')
        with zipfile.ZipFile(archive) as zipped:
            for entry in zipped.infolist():
                expected = manifest['source_hashes'].get(entry.filename)
                target = (out/entry.filename).resolve()
                if expected is None or not target.is_relative_to(out.resolve()):
                    raise ValueError('Archive contains an unregistered source path')
                payload = zipped.read(entry)
                if hashlib.sha256(payload).hexdigest() != expected:
                    raise ValueError(f'Archive source hash mismatch: {entry.filename}')
                if target.exists():
                    if sha(target) != expected:
                        raise ValueError(f'Refusing to overwrite changed source: {target}')
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(payload)
                    restored += 1
    verify(out, manifest)
    print(f'Restored {restored} source files; all registered hashes verified')


def prepare(args):
    sys.path.insert(0, str(ROOT))
    from experiments.runs.run_model6_comparison import freeze_case
    from tools.configuration.loader import load_routing_case
    from models.common.cycle import load_instance

    out = Path(args.output).resolve()
    if out.exists() and any(out.iterdir()):
        raise ValueError(f'Refusing to overwrite existing study: {out}')
    if args.workers < 1 or args.timeout_overhead <= 0:
        raise ValueError('Require positive workers and timeout overhead')
    suite = read(args.suite)
    phases = args.phase
    if phases != 'smoke' and args.overhead_basis.startswith('provisional'):
        raise ValueError('Formal registration requires a recorded smoke-based outer grace')
    records = suite['cases']
    if phases == 'e1a':
        selected = ['ordinary_0', 'repeated_orders_0', 'tight_pool_0']
    elif phases == 'e1exact':
        selected = suite['exact_strategy_cases']
    elif phases == 'smoke':
        selected = ['ordinary_0', 'repeated_orders_0', 'release_limited_0']
    else:
        selected = [r['id'] for r in records]
    records = [r for r in records if r['id'] in selected]
    if {r['id'] for r in records} != set(selected):
        raise ValueError('Missing preregistered case')
    for directory in ('inputs', 'cases', 'runs', 'sources', 'tables'):
        (out/directory).mkdir(parents=True, exist_ok=True)
    versions = ['original'] if phases == 'e0' else ['original', 'recycle']
    source_hashes, archives = {}, {}
    for version in versions:
        source = Path(args.baseline).resolve() if version == 'original' else ROOT
        hashes = snapshot(source, out/'sources'/version)
        source_hashes.update({f'sources/{version}/{name}': digest for name, digest in hashes.items()})
        archive_path = out/f'source_{version}.zip'
        with zipfile.ZipFile(archive_path, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
            for name in hashes:
                relative = f'sources/{version}/{name}'
                entry = zipfile.ZipInfo(relative, date_time=(1980, 1, 1, 0, 0, 0))
                archive.writestr(entry, (out/relative).read_bytes(), compress_type=zipfile.ZIP_DEFLATED)
        archives[archive_path.name] = sha(archive_path)
    solver_changes = []
    if len(versions) == 2:
        for path in sorted((out/'sources/original/models').rglob('*.py')):
            relative = path.relative_to(out/'sources/original')
            other = out/'sources/recycle'/relative
            if not other.is_file() or path.read_text(encoding='utf-8') != other.read_text(encoding='utf-8'):
                solver_changes.append(relative.as_posix())
        for path in (out/'sources/recycle/models').rglob('*.py'):
            relative = path.relative_to(out/'sources/recycle')
            if not (out/'sources/original'/relative).is_file():
                solver_changes.append(relative.as_posix())
        allowed = {f'models/routing/model_{n}/{m}/batch_search.py' for n in (6, 7, 8) for m in ('alns', 'exact')}
        if set(solver_changes) != allowed:
            raise ValueError(f'Budget-only comparison includes unexpected solver changes: {solver_changes}')
    # The coordinator and worker themselves are frozen, including for old solvers.
    shutil.copyfile(Path(__file__), out/'driver.py')
    source_hashes['driver.py'] = sha(out/'driver.py')
    jobs, checks = [], []
    source_evidence = {}
    for record in records:
        for model in (6, 7, 8):
            key = f'{record["id"]}_m{model}'
            config, evidence = freeze_case(record['cases'][str(model)], out, key)
            source_evidence.update(evidence)
            if args.full_k_domain:
                config['joint_batch_options']['batch_count_candidates'] = 'all_positive_integers'
            target = out/'cases'/f'{key}.json'
            write(target, config)
            problem = load_instance(*load_routing_case(target, use_pool_config=False))
            if phases == 'e0':
                for count in problem.batch_count_candidates:
                    if problem.for_count(count).operating_policy.time_tick_subdivision != 1:
                        raise ValueError('E0 requires the common integer clock')
            checks.append(dict(case=key, counts=list(problem.batch_count_candidates),
                               orders=len(problem.orders), demand=problem.total,
                               pool_min=problem.pool_min, pool_max=problem.pool_max,
                               cost_hash=problem.cost_profile.hash))
            methods = ['alns'] if phases == 'e1a' else ['exact'] if phases == 'e1exact' else ['exact', 'alns']
            for version in versions:
                for method in methods:
                    seeds = [42] if method == 'exact' or phases == 'smoke' else [42, 43, 44, 45, 46]
                    budget = args.smoke_seconds if phases == 'smoke' else 30 if phases == 'e0' and method == 'alns' else 300
                    for seed in seeds:
                        job_id = f'{phases}_{key}_{method}_{version}_k{len(problem.batch_count_candidates)}_t{budget:g}_s{seed}'
                        jobs.append(dict(id=job_id, case=record['id'], structure=record['structure'],
                                         split=record['split'], family=record['source_family'], model=model,
                                         method=method, version=version, seed=seed, budget=budget,
                                         iterations=1000000000, counts=list(problem.batch_count_candidates),
                                         case_path=target.relative_to(out).as_posix()))
    random.Random(20261012).shuffle(jobs)
    for job in jobs:
        job['command'] = [sys.executable, str(out/'driver.py'), 'worker', '--output', str(out),
                          '--job', job['id'], '--attempt', '{attempt}']
    inputs = {p.relative_to(out).as_posix(): sha(p) for folder in ('inputs', 'cases')
              for p in sorted((out/folder).iterdir()) if p.is_file()}
    environment = dict(captured_at=now(), interpreter=sys.executable, python=sys.version,
                       platform=platform.platform(), machine=platform.machine(), hostname=socket.gethostname(),
                       logical_cpus=os.cpu_count(), workers=args.workers, exact_threads=1,
                       dependencies={name: metadata.version(name) for name in ('numpy', 'scipy', 'pandas', 'pytest')},
                       gurobi_required=True, gurobi_version=None, integer_preflight=None)
    # Import and solve, rather than treating successful import as license proof.
    import gurobipy as gp
    environment['gurobi_version'] = list(gp.gurobi.version())
    with gp.Env(empty=True) as env:
        env.setParam('OutputFlag', 0)
        env.start()
        with gp.Model(env=env) as model:
            model.Params.Threads = 1
            x = model.addVar(vtype=gp.GRB.INTEGER, lb=0)
            model.addConstr(2*x >= 1)
            model.setObjective(x)
            model.optimize()
            if model.Status != gp.GRB.OPTIMAL or abs(model.ObjVal-1) > 1e-8:
                raise RuntimeError('Actual integer preflight failed')
            environment['integer_preflight'] = dict(status=int(model.Status), objective=model.ObjVal)
    write(out/'environment.json', environment)
    write(out/'input_checks.json', checks)
    write(out/'source_input_evidence.json', source_evidence)
    baseline_commit_file = Path(args.baseline).resolve().parent/'commit.txt'
    baseline_commit = baseline_commit_file.read_text(encoding='utf-8-sig').strip() if baseline_commit_file.exists() else None
    manifest = dict(schema_version=1, phase=phases, registered_at=now(), original_root=str(out),
                    jobs=jobs, workers=args.workers, timeout_overhead=args.timeout_overhead,
                    timeout_overhead_basis=args.overhead_basis, source_hashes=source_hashes, input_hashes=inputs,
                    source_archive_hashes=archives, budget_only_solver_changes=solver_changes,
                    commit=subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
                    original_commit=baseline_commit,
                    uncommitted_source_status=subprocess.check_output(
                        ['git', 'status', '--porcelain', '--', 'models', 'experiments', 'tools', 'cli'],
                        cwd=ROOT, text=True, encoding='utf-8'),
                    source_versions=versions, suite=suite, plan_count=len(jobs),
                    total_call_budget=sum(j['budget'] for j in jobs), interpreter=sys.executable,
                    ordering_seed=20261012, first_attempt_is_primary=True,
                    limitations=['Synthetic mechanism instances; paired variants share a source family.',
                                 'Finite budgets and configured K domains only.',
                                 'Both versions use a common global observer; discoveries are recorded only after outer processing.',
                                 'Full K experiments use each model clock; cross-model inclusion is not asserted.'])
    write(out/'manifest.json', manifest)
    write(out/'manifest.sha256.json', dict(sha256=sha(out/'manifest.json')))
    summarize(out, manifest)
    print(json.dumps(dict(output=str(out), planned=len(jobs), total_call_budget=manifest['total_call_budget']), ensure_ascii=False))


@contextmanager
def study_lock(out):
    """OS locks release on crashes and prevent competing coordinators."""
    handle = (out/'driver.lock').open('a+b')
    if handle.seek(0, os.SEEK_END) == 0:
        handle.write(b'0')
        handle.flush()
    handle.seek(0)
    try:
        if os.name == 'nt':
            import msvcrt
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        raise RuntimeError('Another driver holds this study lock') from None
    try:
        yield
    finally:
        handle.seek(0)
        if os.name == 'nt':
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            fcntl.flock(handle, fcntl.LOCK_UN)
        handle.close()


def attempt_dirs(out, job):
    return sorted((out/'runs'/job['id']).glob('attempt_*'))


def pid_alive(pid):
    # On Windows os.kill(pid, 0) can terminate a process. Query the process
    # handle instead; an inaccessible process must conservatively block resume.
    if os.name == 'nt':
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel.OpenProcess(0x1000, False, pid)
        if not handle:
            if ctypes.get_last_error() == 87:
                return False
            raise PermissionError(f'Cannot query previous worker PID {pid}')
        try:
            code = wintypes.DWORD()
            if not kernel.GetExitCodeProcess(handle, ctypes.byref(code)):
                raise PermissionError(f'Cannot read worker PID {pid}')
            return code.value == 259
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def complete_attempt(path):
    marker = path/'completion.json'
    if not marker.is_file():
        return False
    record = read(marker)
    for name, digest in record['hashes'].items():
        if sha(path/name) != digest:
            raise ValueError(f'Completed attempt has changed: {path/name}')
    return True


def terminal_row(job, status, error=None):
    return {**job, 'status': status, 'valid': False, 'objective': None,
            'proven_optimal': False, 'proven_infeasible': False, 'error': error, 'finished_at': now()}


def finalize_attempt(path, row, extra=()):
    write(path/'row.json', row)
    names = ['row.json', *extra]
    write(path/'completion.json', dict(finished_at=now(), hashes={name: sha(path/name) for name in names}))


def observe_counts(aggregate, trace, history, begun):
    """Common observational wrapper; preserves candidate inputs and decisions.

Confirm a child only after the outer scheduler has processed it, at the next
child call or the final return. Do not backdate discoveries to local history.
The same observer wraps both source versions and its time counts in the call.
"""
    def observed(problem, *, solve_candidate, **kwargs):
        pending = None
        best_cost, best_count = None, None

        def confirm():
            nonlocal pending, best_cost, best_count
            if pending is None:
                return
            result, count = pending
            info = result['info']
            if result.get('audit', {}).get('valid') and info.get('objective') is not None:
                cost = info['objective']
                if best_cost is None or cost < best_cost:
                    best_cost, best_count = cost, count
            history.append(dict(elapsed=time.perf_counter()-begun, objective=best_cost,
                                batch_count=best_count, visited_count=count,
                                source='confirmed_next_candidate_or_call_return'))
            pending = None

        def candidate(child, **child_kwargs):
            nonlocal pending
            confirm()
            start = time.perf_counter()-begun
            result = solve_candidate(child, **child_kwargs)
            count = len(child.operating_policy.batches)
            trace.append(dict(batch_count=count, started=start, returned=time.perf_counter()-begun,
                              seed=child_kwargs.get('seed'), budget=child_kwargs.get('time_limit', child_kwargs.get('max_time')),
                              iteration_budget=child_kwargs.get('max_iterations'), info=deepcopy(result['info']),
                              child_audit_valid=bool(result.get('audit', {}).get('valid')),
                              time_tick_subdivision=child.operating_policy.time_tick_subdivision))
            pending = result, count
            return result

        result = aggregate(problem, solve_candidate=candidate, **kwargs)
        confirm()
        return result
    return observed


def worker(args):
    out = Path(args.output).resolve()
    manifest = read(out/'manifest.json')
    verify(out, manifest)
    job = next(j for j in manifest['jobs'] if j['id'] == args.job)
    path = out/'runs'/job['id']/f'attempt_{args.attempt:03d}'
    sys.path.insert(0, str(out/'sources'/job['version']))
    from tools.configuration.loader import load_routing_case
    from models.common.cycle import load_instance
    from models.common.cycle.audit import audit_solution
    from models.routing.service import solve_routing
    from models.cost.comparison import evaluate_comparison_cost
    config, fleet = load_routing_case(out/job['case_path'], use_pool_config=False)
    problem = load_instance(config, fleet)
    started = time.perf_counter()
    trace = []
    observed_history = [dict(elapsed=0., objective=None, batch_count=None, source='call_start')]
    scheduler = import_module(f'models.routing.model_{job["model"]}.{job["method"]}.batch_search')
    name = 'solve_uniform' if job['model'] == 6 else 'solve_counts'
    original = getattr(scheduler, name)
    observed = observe_counts(original, trace, observed_history, started)
    setattr(scheduler, name, observed)
    calendar_entry = None
    if job['model'] != 6:
        calendar_entry = import_module(f'models.routing.model_{job["model"]}.{job["method"]}.calendar_search')
        calendar_entry.aggregate = observed
    try:
        result = solve_routing(model=job['model'], method=job['method'], problem=problem,
                               exact_time=job['budget'], alns_time=job['budget'],
                               alns_iter=job['iterations'], seed=job['seed'])
    finally:
        setattr(scheduler, name, original)
        if calendar_entry is not None:
            calendar_entry.aggregate = original
    solve_wall = time.perf_counter()-started
    write(path/'result.json', result)
    write(path/'candidate_diagnostics.json', trace)
    info = result['info']
    row = terminal_row(job, info['status'])
    row.update(attempt=args.attempt, solve_wall_seconds=solve_wall, solver_runtime=info.get('runtime'),
               iterations=info.get('iterations'), best_bound=info.get('best_bound'),
               mip_gap=info.get('mip_gap'), proven_infeasible=bool(info.get('proven_infeasible')),
               k=result.get('cycle_batch_count'), demand=problem.total,
               any_audit_failure=bool(info.get('audit_failure_count')),
               certificate_errors=info.get('certificate_error_count', 0))
    audit_start = time.perf_counter()
    audit = audit_solution(problem, result) if info.get('objective') is not None else dict(
        valid=False, not_applicable=True, errors=[], reason='No feasible solution supplied')
    write(path/'audit.json', dict(result_sha256=sha(path/'result.json'), audit=audit))
    row['offline_audit_seconds'] = time.perf_counter()-audit_start
    if info.get('objective') is not None:
        row.update(valid=bool(audit['valid']), audit_errors=audit['errors'])
        if audit['valid']:
            cost = evaluate_comparison_cost(problem, result, config['economic_config'], audit=audit)
            if not math.isclose(cost['total'], info['objective'], rel_tol=1e-7, abs_tol=1e-6):
                raise ValueError('Independent cost differs from solver objective')
            if not audit['periodic_replay']['valid']:
                raise ValueError('Two-cycle replay failed')
            row.update(objective=cost['total'], cost_components=cost['components'],
                       pool=cost['container_pool_size'], minimum_pool=audit['minimum_pool_for_schedule'],
                       delivery_batches=result.get('delivery_batch_count'),
                       recovery_rounds=result.get('actual_recovery_round_count'),
                       proven_optimal=bool(info.get('proven_optimal')), periodic_valid=True)
        else:
            row.update(status='AUDIT_FAILED', proven_optimal=False)
    if row['any_audit_failure'] or row['certificate_errors']:
        row.update(status='DIAGNOSTIC_FAILURE', valid=False, objective=None, proven_optimal=False)
    rows = result.get('count_search', [])
    row.update(candidate_count=len(rows), visited_count=sum(not r['status'].startswith('NOT_RUN') for r in rows),
               visits=sum(len(r.get('visits', [r])) for r in rows if not r['status'].startswith('NOT_RUN')),
               unresolved_count=sum(not (r['proven_optimal'] or r['proven_infeasible']) for r in rows))
    history = observed_history
    with (path/'anytime.csv').open('w', encoding='utf-8', newline='') as handle:
        fields = ['elapsed', 'objective', 'batch_count', 'visited_count', 'visit', 'source']
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction='ignore')
        writer.writeheader()
        writer.writerows(history)
    row['anytime_scope'] = 'global_all_counts'
    row['anytime_granularity'] = 'confirmed_next_candidate_or_call_return'
    finite_history = [v for v in history if v.get('objective') is not None]
    row['first_observed_feasible_seconds'] = finite_history[0]['elapsed'] if finite_history else None
    row['first_observed_cost'] = finite_history[0]['objective'] if finite_history else None
    row['best_observed_seconds'] = next((v['elapsed'] for v in finite_history
                                        if math.isclose(v['objective'], row['objective'], abs_tol=1e-7)), None) if row['valid'] else None
    finalize_attempt(path, row, ('result.json', 'audit.json', 'anytime.csv', 'candidate_diagnostics.json'))


def execute(out, manifest, job):
    attempts = attempt_dirs(out, job)
    number = max((int(p.name.split('_')[-1]) for p in attempts), default=0)+1
    path = out/'runs'/job['id']/f'attempt_{number:03d}'
    path.mkdir(parents=True, exist_ok=False)
    command = [sys.executable, str(out/'driver.py'), 'worker', '--output', str(out),
               '--job', job['id'], '--attempt', str(number)]
    env = {**os.environ, 'PYTHONUTF8': '1', 'PYTHONIOENCODING': 'utf-8', 'PYTHONHASHSEED': '0',
           'OMP_NUM_THREADS': '1', 'OPENBLAS_NUM_THREADS': '1', 'MKL_NUM_THREADS': '1'}
    started = time.perf_counter()
    process = None
    try:
        with (path/'worker.log').open('w', encoding='utf-8') as log:
            process = subprocess.Popen(command, cwd=out, env=env, stdout=log, stderr=subprocess.STDOUT)
            write(path/'launch.json', dict(pid=process.pid, command=command, cwd=str(out), started_at=now(),
                                          manifest_sha256=sha(out/'manifest.json'), attempt=number))
            while process.poll() is None:
                elapsed = time.perf_counter()-started
                write(path/'live.json', dict(pid=process.pid, elapsed=elapsed, updated_at=now(), state='RUNNING'))
                if elapsed > job['budget']+manifest['timeout_overhead']:
                    process.kill()
                    process.wait()
                    raise TimeoutError('Worker exceeded call budget plus registered outer grace')
                time.sleep(.5)
        if process.returncode != 0:
            raise RuntimeError(f'Worker exited with {process.returncode}; see worker.log')
        if not complete_attempt(path):
            raise RuntimeError('Worker exited without complete result records')
    except Exception as exc:
        if process is not None and process.poll() is None:
            process.kill()
            process.wait()
        if not complete_attempt(path):
            row = terminal_row(job, 'OUTER_TIMEOUT' if isinstance(exc, TimeoutError) else 'ERROR', str(exc))
            row.update(attempt=number, outer_wall_seconds=time.perf_counter()-started)
            finalize_attempt(path, row)
    row = read(path/'row.json')
    write(path/'process.json', dict(exit_code=process.returncode if process else None,
                                   outer_wall_seconds=time.perf_counter()-started, finished_at=now()))
    return row


def summarize(out, manifest):
    rows, attempts = [], []
    for job in manifest['jobs']:
        directories = attempt_dirs(out, job)
        valid_attempts = []
        for path in directories:
            if complete_attempt(path):
                valid_attempts.append(read(path/'row.json'))
        attempts.extend(valid_attempts)
        # Preserve every originally planned run, including pending/failure rows.
        rows.append(valid_attempts[0] if valid_attempts else terminal_row(job, 'RUNNING' if directories else 'NOT_RUN'))
    fields = ['id', 'case', 'structure', 'split', 'family', 'model', 'method', 'version', 'seed', 'budget',
              'status', 'valid', 'objective', 'pool', 'demand', 'k', 'delivery_batches', 'recovery_rounds',
              'proven_optimal', 'proven_infeasible', 'best_bound', 'mip_gap', 'solve_wall_seconds',
              'solver_runtime', 'offline_audit_seconds', 'candidate_count', 'visited_count', 'visits',
              'unresolved_count', 'iterations', 'anytime_scope', 'first_observed_feasible_seconds',
              'first_observed_cost', 'best_observed_seconds', 'attempt', 'error']
    for filename, values in [('summary.csv', rows), ('attempts.csv', attempts)]:
        temporary = out/(filename+'.tmp')
        with temporary.open('w', encoding='utf-8', newline='') as handle:
            writer = csv.DictWriter(handle, fieldnames=fields, extrasaction='ignore')
            writer.writeheader()
            writer.writerows(values)
        os.replace(temporary, out/filename)
    counts = {}
    for row in rows:
        counts[row['status']] = counts.get(row['status'], 0)+1
    terminal = sum(r['status'] not in ('NOT_RUN', 'RUNNING') for r in rows)
    summary = dict(planned=len(rows), terminal=terminal, audited_feasible=sum(r['valid'] for r in rows),
                   optimal=sum(r['proven_optimal'] for r in rows), proven_infeasible=sum(r['proven_infeasible'] for r in rows),
                   statuses=counts, attempts=len(attempts), state='COMPLETED' if terminal == len(rows) else 'IN_PROGRESS',
                   updated_at=now(), primary='first_attempt_only')
    write(out/'progress.json', summary)
    lines = ['# 论文补充实验阶段记录', '', f'阶段：{manifest["phase"]}；状态：{summary["state"]}。', '',
             f'计划 {len(rows)} 次，已有终态 {terminal} 次，独立审计可行 {summary["audited_feasible"]} 次，'
             f'最优证明 {summary["optimal"]} 次，已证不可行 {summary["proven_infeasible"]} 次。', '',
             '统计以全部计划首次运行作分母；重试列于 attempts.csv，不替换首次失败。',
             'COMPLETED 只表示全部任务已有终态。未执行和限时未找到可行解不等于不可行。', '',
             f'登记调用预算和：{manifest["total_call_budget"]/3600:.3f} 小时；并发 {manifest["workers"]}，精确单线程。', '',
             '| 状态 | 次数 |', '| --- | ---: |']
    lines += [f'| {status} | {count} |' for status, count in sorted(counts.items())]
    lines += ['', '源码、输入哈希及完整命令见 manifest.json；每次运行与离线双周期审计位于 runs/。',
              '费用与分量位于逐次 row.json，逐 K 建模/搜索诊断位于 candidate_diagnostics.json。',
              '两版费用轨迹由相同外层观察器记录，在下一候选开始或全调用返回时确认，未将局部历史提前记作全局发现。', '',
              '局限：合成机制例；同源变体不是独立样本；证明仅适用于各自登记的 K 域及整数时间域。']
    (out/'report.md').write_text('\n'.join(lines)+'\n', encoding='utf-8')
    return summary


def run(args):
    out = Path(args.output).resolve()
    manifest = read(out/'manifest.json')
    verify(out, manifest)
    with study_lock(out):
        pending = []
        for job in manifest['jobs']:
            directories = attempt_dirs(out, job)
            for path in directories:
                if not complete_attempt(path):
                    # Coordinator crashed. Do not launch over a surviving worker.
                    launch = read(path/'launch.json') if (path/'launch.json').exists() else {}
                    if launch.get('pid') and pid_alive(launch['pid']):
                        raise RuntimeError(f'Old worker may still be alive: {path}')
                    row = terminal_row(job, 'INTERRUPTED', 'Previous attempt did not produce complete records')
                    row['attempt'] = int(path.name.split('_')[-1])
                    finalize_attempt(path, row)
            if not directories or (args.retry_failed and read(directories[-1]/'row.json')['status'] in
                                    ('ERROR', 'OUTER_TIMEOUT', 'INTERRUPTED')):
                pending.append(job)
        summarize(out, manifest)
        with ThreadPoolExecutor(max_workers=manifest['workers']) as executor:
            # Bounded batches avoid prequeueing the whole matrix after STOP.
            for start in range(0, len(pending), manifest['workers']):
                if (out/'STOP').exists():
                    break
                futures = [executor.submit(execute, out, manifest, job)
                           for job in pending[start:start+manifest['workers']]]
                for future in as_completed(futures):
                    row = future.result()
                    with (out/'progress.jsonl').open('a', encoding='utf-8') as handle:
                        handle.write(json.dumps(dict(at=now(), job=row['id'], attempt=row['attempt'],
                                                     status=row['status'], valid=row['valid']), ensure_ascii=False)+'\n')
                    summary = summarize(out, manifest)
                    print(f'{summary["terminal"]}/{summary["planned"]} {row["id"]}: {row["status"]}', flush=True)
        print(json.dumps(summarize(out, manifest), ensure_ascii=False))


def analyze(out):
    """Report individual seed outcomes, without relabelling upper bounds as optima."""
    manifest = read(out/'manifest.json')
    verify(out, manifest)
    rows = []
    for job in manifest['jobs']:
        paths = attempt_dirs(out, job)
        if paths and complete_attempt(paths[0]):
            rows.append(read(paths[0]/'row.json'))
    exact = {(r['case'], r['model'], r['version']): r for r in rows if r['method'] == 'exact'}
    comparisons, contradictions = [], []
    for row in rows:
        if row['method'] != 'alns':
            continue
        reference = exact.get((row['case'], row['model'], row['version']))
        record = dict(id=row['id'], case=row['case'], model=row['model'], seed=row['seed'],
                      version=row['version'], valid=row['valid'], objective=row['objective'],
                      optimal_error=None, relative_feasible_reference_difference=None,
                      certified_gap=None, reference_status=reference['status'] if reference else 'UNAVAILABLE')
        if reference and row['valid']:
            upper = row['objective']
            lower = reference.get('best_bound')
            ref_cost = reference.get('objective')
            if lower is not None:
                if lower > upper+1e-6+1e-7*abs(upper):
                    contradictions.append(dict(id=row['id'], lower=lower, upper=upper))
                elif upper != 0:
                    record['certified_gap'] = max(0., upper-lower)/upper
            if reference.get('proven_infeasible'):
                contradictions.append(dict(id=row['id'], reason='audited feasible vs proven infeasible'))
            if reference.get('valid') and ref_cost != 0:
                key = 'optimal_error' if reference.get('proven_optimal') else 'relative_feasible_reference_difference'
                record[key] = (upper-ref_cost)/ref_cost
                if key == 'optimal_error' and upper < ref_cost-1e-6-1e-7*abs(ref_cost):
                    contradictions.append(dict(id=row['id'], reason='audited cost beats claimed optimum'))
        comparisons.append(record)
    fields = ['id', 'case', 'model', 'seed', 'version', 'valid', 'objective', 'reference_status',
              'optimal_error', 'relative_feasible_reference_difference', 'certified_gap']
    with (out/'tables/reference_comparison.csv').open('w', encoding='utf-8', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(comparisons)
    bad = [r['id'] for r in rows if r['status'] in
           ('ERROR', 'OUTER_TIMEOUT', 'INTERRUPTED', 'AUDIT_FAILED', 'DIAGNOSTIC_FAILURE')]
    gate = dict(planned=len(manifest['jobs']), terminal=len(rows), failures=bad,
                certificate_contradictions=contradictions,
                ready=len(rows) == len(manifest['jobs']) and not bad and not contradictions,
                completed_at=now())
    write(out/'stage_gate.json', gate)
    return gate


def pipeline(args):
    """Run already frozen phases sequentially, respecting correctness gates."""
    root = Path(args.output).resolve()
    root.mkdir(parents=True, exist_ok=True)
    studies = [Path(p).resolve() for p in args.studies]
    with study_lock(root):
        for study in studies:
            if (root/'STOP').exists():
                print('Pipeline STOP requested', flush=True)
                return
            manifest = read(study/'manifest.json')
            verify(study, manifest)
            write(root/'pipeline.json', dict(state='RUNNING', study=str(study), updated_at=now(),
                                            studies=[str(p) for p in studies]))
            command = [sys.executable, str(study/'driver.py'), 'run', '--output', str(study)]
            result = subprocess.run(command, cwd=study)
            if result.returncode:
                raise RuntimeError(f'Phase driver failed: {study}')
            with study_lock(study):
                gate = analyze(study)
            if not gate['ready']:
                write(root/'pipeline.json', dict(state='NEEDS_REVIEW', study=str(study),
                                                gate=gate, updated_at=now()))
                print(f'Phase requires review before continuation: {study}', flush=True)
                return
        write(root/'pipeline.json', dict(state='COMPLETED', studies=[str(p) for p in studies], updated_at=now()))


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('action', choices=['prepare', 'run', 'worker', 'status', 'restore', 'analyze', 'pipeline'])
    parser.add_argument('--output', required=True)
    parser.add_argument('--phase', choices=['e0', 'e1a', 'e1exact', 'smoke'], default='e0')
    parser.add_argument('--suite')
    parser.add_argument('--baseline')
    parser.add_argument('--workers', type=int, default=2)
    parser.add_argument('--timeout-overhead', type=float, default=60.)
    parser.add_argument('--overhead-basis', default='provisional; smoke measurement required before formal registration')
    parser.add_argument('--smoke-seconds', type=float, default=3.)
    parser.add_argument('--full-k-domain', action='store_true')
    parser.add_argument('--retry-failed', action='store_true')
    parser.add_argument('--job')
    parser.add_argument('--attempt', type=int)
    parser.add_argument('--studies', nargs='+')
    args = parser.parse_args()
    if args.action == 'prepare':
        if not args.suite or not args.baseline:
            parser.error('prepare requires --suite and --baseline')
        prepare(args)
    elif args.action == 'run':
        run(args)
    elif args.action == 'worker':
        worker(args)
    elif args.action == 'restore':
        restore_sources(Path(args.output).resolve())
    elif args.action == 'analyze':
        out = Path(args.output).resolve()
        with study_lock(out):
            print(json.dumps(analyze(out), ensure_ascii=False))
    elif args.action == 'pipeline':
        if not args.studies:
            parser.error('pipeline requires --studies')
        pipeline(args)
    else:
        out = Path(args.output).resolve()
        # Status is read-only and does not compete with coordinator writes.
        print(json.dumps(read(out/'progress.json'), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
