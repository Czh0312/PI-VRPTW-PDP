"""Durable, resource-limited exact/ALNS study of all spacetime-v2 models.

Use tools/run.ps1 on Windows. Inputs, sources, primary attempts and retries
are immutable. An existing study resumes using its own frozen driver.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from importlib import import_module
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
import traceback


ROOT = Path(__file__).resolve().parents[2]
_COMPLETED_ROWS = {}


def rental_profile(original):
    """User's M5--M8 rental: one per stocked box per complete cycle."""
    from copy import deepcopy
    profile = deepcopy(original)
    profile.update(container_pool_cost_per_period=1, pool_cost_basis='rental',
        container_price=0, container_lifetime_uses=None, rental_includes=[],
        evaluation_period='one complete 780-minute operating cycle (not one delivery batch)',
        pool_charge_definition='Recurring rental 1*N per complete cycle; no purchase investment, deposit or setup charge.',
        description='User instruction 2026-10-21: M5-M8 container stock rental 1 per box per cycle; transport, cleaning and explicit wear rates unchanged.',
        price_basis='User-defined synthetic rental coefficient 1 per stocked container per complete cycle; other benchmark coefficients retained')
    return profile


def driver(out=None):
    source = out/'source' if out is not None else ROOT
    sys.path.insert(0, str(source))
    from experiments.runs import run_thesis_completion as d
    return d


def make_jobs(records, pilot=False):
    jobs = []
    def add(record, method, budget, seed=42, phase=0, prerequisite=None):
        name = f'{record["base_case_id"]}_m{record["model"]}_{method}_{budget:g}s_s{seed}'
        jobs.append(dict(id=name, case=record['base_case_id'], model=record['model'],
            method=method, budget=budget, seed=seed, phase=phase, prerequisite=prerequisite,
            version='current', iterations=10**9, size=record['size'], structure=record['size'],
            family=record['family_id'], source_block=record['source_block'], split='formal',
            customer_count=record['customer_count'],
            case_path=f'cases/{record["base_case_id"]}_m{record["model"]}.json'))
        return name
    for record in records:
        if pilot:
            if record['base_case_id'] == 'S01':
                add(record, 'exact', 8)
                add(record, 'alns', 2)
            elif record['model'] == 8 and record['base_case_id'] in ('M01', 'L05'):
                add(record, 'alns', 5)
            continue
        if record['size'] == 'small':
            initial = add(record, 'exact', 300, phase=0)
            extension = add(record, 'exact', 3600, phase=1, prerequisite=initial)
            add(record, 'exact', 10800, phase=3, prerequisite=extension)
        elif record['size'] == 'medium':
            add(record, 'exact', 600, phase=2)
        for phase, budget in enumerate((10, 60, 300)):
            for seed in (42, 43, 44):
                primary = add(record, 'alns', budget, seed, phase)
                if budget == 300:
                    add(record, 'alns', 900, seed, phase=3, prerequisite=primary)
    sizes = dict(small=0, medium=1, large=2)
    return sorted(jobs, key=lambda j: (j['phase'], j['budget'], sizes[j['size']], j['case'], j['model'], j['seed']))


def prepare(args):
    d = driver()
    from experiments.runs.run_model6_comparison import freeze_case
    from tools.configuration.snapshots import freeze_input
    from tools.audit.audit_benchmark27_spacetime import audit_design
    from tools.audit.certify_formal_benchmark27 import verify_witnesses
    import psutil
    out, dataset = Path(args.output).resolve(), (ROOT/args.dataset).resolve()
    if out.exists() and any(out.iterdir()):
        raise ValueError('Output must be a new or empty directory')
    audit, witnesses = audit_design(dataset), verify_witnesses(dataset)
    if not audit['valid'] or not witnesses['valid']:
        raise ValueError('Input or independent feasibility verification failed')
    suite = d.read(dataset/'manifest.json')
    records = suite['cases']
    jobs = make_jobs(records, args.pilot)
    selected = {(j['case'], j['model']) for j in jobs}
    for name in ('source', 'inputs', 'cases', 'runs', 'tables'):
        (out/name).mkdir(parents=True, exist_ok=True)
    evidence = {}
    for record in records:
        key = record['base_case_id'], record['model']
        if key not in selected:
            continue
        prefix = f'{key[0]}_m{key[1]}'
        cfg, hashes = freeze_case(record['case_path'], out, prefix)
        evidence.update(hashes)
        if record['model'] >= 5:
            economic_path = Path(cfg['economic_config'])
            rental_path = out/'inputs'/f'{prefix}_economic_rental1.json'
            d.write(rental_path, rental_profile(d.read(economic_path)))
            cfg['economic_config'] = str(rental_path)
        for field in ('parameter_contract', 'batch_count_certificate'):
            if isinstance(cfg.get(field), str):
                original = (ROOT/cfg[field]).resolve()
                target = out/'inputs'/f'{prefix}_{field}.json'
                evidence.update(freeze_input(original, target, out))
                if field == 'parameter_contract' and record['model'] >= 5:
                    contract = d.read(target)
                    contract.update(economics_status='Study override: container rental coefficient 1 per box per complete cycle',
                        container_rental_per_cycle=1, pool_cost_basis='rental')
                    target = target.with_name(target.stem+'_rental1.json')
                    d.write(target, contract)
                cfg[field] = str(target)
        cfg.pop('initial_joint_result', None)
        d.write(out/'cases'/f'{prefix}.json', cfg)
    d.write(out/'input_audit.json', audit)
    d.write(out/'witness_verification.json', witnesses)
    d.write(out/'dataset_manifest.json', suite)
    d.write(out/'comparison_protocol.json', d.read(dataset/'comparison_protocol.json'))
    d.write(out/'source_input_evidence.json', evidence)
    from importlib import metadata
    import platform
    import gurobipy as gp
    d.write(out/'environment.json', dict(interpreter=sys.executable, python=sys.version,
        platform=platform.platform(), gurobi_version=list(gp.gurobi.version()),
        dependencies={name:metadata.version(name) for name in ('gurobipy','numpy','pandas','pytest','psutil')}))
    sources = {f'source/{name}': digest for name, digest in d.snapshot(ROOT, out/'source').items()}
    shutil.copyfile(__file__, out/'driver.py')
    sources['driver.py'] = d.sha(out/'driver.py')
    inputs = {p.relative_to(out).as_posix(): d.sha(p) for folder in ('inputs', 'cases')
              for p in (out/folder).iterdir() if p.is_file()}
    for name in ('input_audit.json', 'witness_verification.json', 'dataset_manifest.json',
                 'comparison_protocol.json', 'source_input_evidence.json', 'environment.json'):
        inputs[name] = d.sha(out/name)
    manifest = dict(schema_version=1, phase='spacetime_quality', registered_at=d.now(),
        pilot=args.pilot, dataset=str(dataset), dataset_sha256=d.sha(dataset/'manifest.json'),
        commit=subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
        source_hashes=sources, input_hashes=inputs, jobs=jobs, workers=2,
        exact_threads=1, exact_formulation='compact', external_starts=False,
        maximum_exact_concurrency=1, maximum_alns_concurrency=1,
        timeout_overhead=180, worker_memory_mib=1536, minimum_available_mib=192,
        memory_pressure_grace_seconds=5, interpreter=sys.executable,
        physical_memory_bytes=psutil.virtual_memory().total,
        physical_cpus=psutil.cpu_count(logical=False), logical_cpus=os.cpu_count(),
        total_call_budget=sum(j['budget'] for j in jobs),
        conditional_jobs=sum(j['prerequisite'] is not None for j in jobs),
        timing='Concurrent separate processes, one thread each; wall timings include contention. '
               'Independent cold budget runs, not prefixes of a longer ALNS run.',
        reference='Only independently audited, proven exact optima give optimality gaps. '
                  'Otherwise show incumbent differences and lower-bound certificates separately.',
        economics='M5-M8 container pool rental is 1 per stocked box per full 780-minute cycle; '
                  'container purchase price 0, no purchase investment; transport, cleaning and explicit wear unchanged.',
        economic_override=dict(models=[5,6,7,8],container_pool_cost_per_period=1,
            pool_cost_basis='rental',container_price=0,cycle_minutes=780))
    d.write(out/'manifest.json', manifest)
    d.write(out/'manifest.sha256.json', dict(sha256=d.sha(out/'manifest.json')))
    (out/'.gitignore').write_text('/source/\n/inputs/\n/cases/\n/runs/\n/driver.lock\n'
        '/controller*.log\n/controller.local.ps1\n', encoding='utf-8')
    summarize(out)
    print(json.dumps(dict(output=str(out), planned=len(jobs), conditional=manifest['conditional_jobs']), ensure_ascii=False))


def parse_gurobi_log(text, build_seconds=0):
    """Read rounded solver log times; never infer a proof from a log row."""
    rows = []
    def numeric(value):
        try:
            number = float(value)
            return number if math.isfinite(number) and abs(number) < 1e90 else None
        except ValueError:
            return None
    for line in text.splitlines():
        tokens = line.split()
        if len(tokens) < 7 or not re.fullmatch(r'\d+(?:\.\d+)?s', tokens[-1]):
            continue
        if not (tokens[-3] == '-' or tokens[-3].endswith('%')):
            continue
        objective, bound = numeric(tokens[-5]), numeric(tokens[-4])
        if objective is None and bound is None:
            continue
        rows.append(dict(elapsed=float(tokens[-1][:-1])+build_seconds,
            objective=objective, best_bound=bound, source='gurobi_rounded_progress_log',
            independently_audited=False))
    return rows


def worker(args):
    out = Path(args.output).resolve()
    d = driver(out)
    manifest = d.read(out/'manifest.json')
    job = next(j for j in manifest['jobs'] if j['id'] == args.job)
    path = out/'runs'/job['id']/f'attempt_{args.attempt:03d}'
    # Reuse the repository's independent objective check and two-cycle audit.
    service = import_module('models.routing.service')
    original_solve = service.solve_routing
    def solve(**kwargs):
        if kwargs['method'] == 'exact':
            kwargs.update(exact_strategy='milp', milp_options=dict(threads=1, formulation='compact'))
        return original_solve(**kwargs)
    service.solve_routing = solve
    if job['method'] == 'exact':
        module = import_module('models.common.cycle.unified_milp' if job['model'] >= 6 else
                               f'models.routing.model_{job["model"]}.exact.milp_solver')
        name = 'build_unified_model' if job['model'] >= 6 else 'build_model'
        build = getattr(module, name)
        def logged_build(*values, **kwargs):
            model, context = build(*values, **kwargs)
            model.Params.LogToConsole = 0
            # Gurobi 11's native file writer needs an ASCII relative filename
            # when the Windows workspace has non-ASCII characters.
            model.Params.LogFile = (path/'gurobi.log').relative_to(out).as_posix()
            model.Params.OutputFlag = 1
            return model, context
        setattr(module, name, logged_build)
    original_finalize, provisional_extras = d.finalize_attempt, []
    def provisional_finalize(target, row, extra=()):
        # Publish exactly one final completion marker, after enrichment. A
        # transient base-worker marker races with the Windows coordinator.
        d.write(target/'row.json', row)
        provisional_extras.extend(extra)
    d.finalize_attempt = provisional_finalize
    try:
        d.worker(args)
    finally:
        d.finalize_attempt = original_finalize
    extras = list(provisional_extras)
    row, result = d.read(path/'row.json'), d.read(path/'result.json')
    info = result['info']
    row.update(build_seconds=info.get('build_time'), mip_seconds=info.get('mip_runtime'),
        variables=info.get('variables'), constraints=info.get('constraints'),
        complete_count_domain=info.get('complete_count_domain'), proof_scope=info.get('proof_scope'),
        objective_mode=result.get('objective_mode', 'distance' if job['model'] <= 4 else 'cost'),
        solver_config=info.get('solver_config'),
        iterations=info.get('iterations'))
    if row.get('proven_infeasible'):
        row.update(status='WITNESS_CONTRADICTION', valid=False, proven_optimal=False)
    history = []
    if job['method'] == 'exact' and (path/'gurobi.log').exists():
        history = parse_gurobi_log((path/'gurobi.log').read_text(encoding='utf-8', errors='replace'),
                                  info.get('build_time', 0))
    elif job['method'] == 'alns' and job['model'] <= 5:
        history = [dict(elapsed=h['time'], objective=h['objective'], source='solver_incumbent_history',
                        independently_audited=False) for h in info.get('incumbent_history', [])]
    elif job['method'] == 'alns':
        history = d.read(path/'candidate_diagnostics.json')
        # Existing anytime.csv records only globally confirmed candidate returns.
        import csv
        with (path/'anytime.csv').open(encoding='utf-8') as handle:
            history = [dict(elapsed=float(h['elapsed']),
                            objective=float(h['objective']) if h['objective'] else None,
                            source=h['source'], independently_audited=False) for h in csv.DictReader(handle)]
    history.append(dict(elapsed=row['solve_wall_seconds'], objective=row.get('objective'),
                        best_bound=row.get('best_bound'), source='final_independent_audit',
                        independently_audited=bool(row['valid'])))
    d.write(path/'trajectory.json', history)
    if (path/'gurobi.log').exists():
        extras.append('gurobi.log')
    extras.append('trajectory.json')
    d.finalize_attempt(path, row, extras)


def latest_rows(out, manifest, d):
    rows = {}
    for job in manifest['jobs']:
        cache_key = str(out), job['id']
        if cache_key in _COMPLETED_ROWS:
            rows[job['id']] = _COMPLETED_ROWS[cache_key]
            continue
        # First completed attempt is primary, later attempts remain separately visible.
        for path in d.attempt_dirs(out, job):
            try:
                complete = d.complete_attempt(path)
            except (PermissionError, FileNotFoundError):
                # A Windows file handle can briefly remain unavailable just
                # after atomic publication. The next summary verifies it.
                complete = False
            if complete:
                rows[job['id']] = d.read(path/'row.json')
                _COMPLETED_ROWS[cache_key] = rows[job['id']]
                break
    return rows


def conditional_skip(job, rows):
    if not job['prerequisite']:
        return None
    prior = rows.get(job['prerequisite'])
    if prior is None:
        raise RuntimeError('Conditional job reached before prerequisite completed')
    if job['method'] == 'alns' and prior['valid']:
        return 'SKIPPED_ALREADY_FEASIBLE'
    if job['method'] == 'exact':
        if prior.get('proven_optimal') or prior['status'] == 'SKIPPED_PROVEN_OPTIMAL':
            return 'SKIPPED_PROVEN_OPTIMAL'
        if prior['status'] in ('RESOURCE_LIMIT', 'SKIPPED_RESOURCE_LIMIT'):
            return 'SKIPPED_RESOURCE_LIMIT'
    return None


def process_family_memory(process):
    """The Windows venv launcher may keep the interpreter in a child process."""
    import psutil
    family = [process, *process.children(recursive=True)]
    alive, rss = [], 0
    for member in family:
        try:
            rss += member.memory_info().rss
            alive.append(dict(pid=member.pid, created_at=member.create_time()))
        except psutil.NoSuchProcess:
            pass
    return rss, alive


def kill_process_family(process):
    import psutil
    try:
        family = process.children(recursive=True)
    except psutil.NoSuchProcess:
        family = []
    for member in [*reversed(family), process]:
        try:
            member.kill()
        except psutil.NoSuchProcess:
            pass


def execute(out, manifest, job):
    d = driver(out)
    import psutil
    number = max((int(p.name.split('_')[-1]) for p in d.attempt_dirs(out, job)), default=0)+1
    path = out/'runs'/job['id']/f'attempt_{number:03d}'
    path.mkdir(parents=True)
    env = {**os.environ, 'PYTHONUTF8': '1', 'PYTHONIOENCODING': 'utf-8', 'PYTHONHASHSEED': '0',
           **{key:'1' for key in ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS', 'NUMEXPR_NUM_THREADS')}}
    command = [sys.executable, str(out/'driver.py'), 'worker', '--output', str(out),
               '--job', job['id'], '--attempt', str(number)]
    begun, process, reason, pressure_since = time.perf_counter(), None, None, None
    peak = 0
    try:
        with (path/'worker.log').open('w', encoding='utf-8') as log:
            process = subprocess.Popen(command, cwd=out, env=env, stdout=log, stderr=subprocess.STDOUT)
            d.write(path/'launch.json', dict(pid=process.pid, command=command, started_at=d.now(), attempt=number))
            monitor = psutil.Process(process.pid)
            while process.poll() is None:
                elapsed = time.perf_counter()-begun
                try:
                    rss, family = process_family_memory(monitor)
                except psutil.NoSuchProcess:
                    break
                peak = max(peak, rss)
                available = psutil.virtual_memory().available
                d.write(path/'live.json', dict(pid=process.pid, process_family=family, elapsed=elapsed, rss_bytes=rss,
                    available_bytes=available, updated_at=d.now(), state='RUNNING'))
                if rss > manifest['worker_memory_mib']*1024**2:
                    reason = 'RESOURCE_LIMIT: worker exceeded registered memory ceiling'
                elif available < manifest['minimum_available_mib']*1024**2:
                    pressure_since = pressure_since or time.perf_counter()
                    if time.perf_counter()-pressure_since > manifest['memory_pressure_grace_seconds']:
                        reason = 'RESOURCE_LIMIT: sustained system memory pressure'
                else:
                    pressure_since = None
                if elapsed > job['budget']+manifest['timeout_overhead']:
                    reason = 'OUTER_TIMEOUT: budget plus 180 seconds exceeded'
                if reason:
                    kill_process_family(monitor)
                    break
                time.sleep(1)
            process.wait()
        if reason or process.returncode or not d.complete_attempt(path):
            if not d.complete_attempt(path):
                status = reason.split(':')[0] if reason else 'ERROR'
                row = d.terminal_row(job, status, reason or f'Worker exited {process.returncode}; see worker.log')
                row['attempt'] = number
                d.finalize_attempt(path, row)
    except BaseException:
        if process is not None and process.poll() is None:
            kill_process_family(psutil.Process(process.pid))
            process.wait()
        raise
    d.write(path/'process.json', dict(exit_code=process.returncode, peak_rss_bytes=peak,
        outer_wall_seconds=time.perf_counter()-begun, finished_at=d.now()))
    return d.read(path/'row.json')


def reference_metrics(alns, exact_rows):
    refs = [r for r in exact_rows if r['case'] == alns['case'] and r['model'] == alns['model']]
    feasible = [r for r in refs if r['valid'] and r.get('objective') is not None]
    optima = [r for r in feasible if r.get('proven_optimal')]
    incumbent = min(feasible, key=lambda r:r['objective'], default=None)
    optimum = min(optima, key=lambda r:r['objective'], default=None)
    bounds = [r['best_bound'] for r in refs if r.get('best_bound') is not None]
    bound = max(bounds, default=None)
    objective = alns.get('objective') if alns['valid'] else None
    tolerance = max(1e-5, abs(objective or 0)*1e-7)
    conflict = bool(objective is not None and (
        (optimum and objective < optimum['objective']-tolerance) or
        (bound is not None and bound > objective+tolerance)))
    def difference(reference):
        return 100*(objective-reference)/max(1e-9, abs(reference))
    return dict(exact_reference_status=optimum['status'] if optimum else incumbent['status'] if incumbent else None,
        exact_proven_optimal=bool(optimum), exact_objective=incumbent['objective'] if incumbent else None,
        exact_reference_run=optimum['id'] if optimum else incumbent['id'] if incumbent else None,
        exact_wall_seconds=(optimum or incumbent or {}).get('solve_wall_seconds'),
        exact_lower_bound=bound, reference_conflict=conflict,
        optimal_gap_percent=max(0., difference(optimum['objective']))
            if objective is not None and optimum and not conflict else None,
        feasible_reference_difference_percent=difference(incumbent['objective'])
            if objective is not None and incumbent and not conflict else None,
        certified_gap_upper_percent=max(0., difference(bound))
            if objective is not None and bound is not None and bound > 0 and not conflict else None)


def summarize(out):
    d = driver(out)
    manifest = d.read(out/'manifest.json')
    rows_by_id = latest_rows(out, manifest, d)
    rows = [rows_by_id.get(j['id'], d.terminal_row(j, 'NOT_RUN')) for j in manifest['jobs']]
    exact = [r for r in rows if r['method'] == 'exact']
    comparison = [{**r, **reference_metrics(r, exact)} for r in rows if r['method'] == 'alns']
    from experiments.runs.run_formal_benchmark27 import write_csv
    write_csv(out/'tables/runs.csv', rows)
    write_csv(out/'tables/alns_gaps.csv', comparison)
    d.write(out/'rows.json', rows)
    groups = defaultdict(list)
    for row in comparison:
        if row['budget'] <= 300:
            groups[(row['size'], row['model'], row['budget'])].append(row)
    aggregate = []
    for (size, model, budget), group in sorted(groups.items()):
        valid = [r for r in group if r['valid']]
        gaps = [r['optimal_gap_percent'] for r in valid if r['optimal_gap_percent'] is not None]
        aggregate.append(dict(size=size, model=model, budget=budget, planned=len(group), valid=len(valid),
            proven_reference_pairs=len(gaps), mean_gap_percent=sum(gaps)/len(gaps) if gaps else None,
            max_gap_percent=max(gaps) if gaps else None,
            mean_wall_seconds=sum(r['solve_wall_seconds'] for r in valid)/len(valid) if valid else None))
    write_csv(out/'tables/efficiency.csv', aggregate)
    coverage = []
    for size in ('small', 'medium', 'large'):
        for model in range(1,9):
            selected = [r for r in rows if r['size'] == size and r['model'] == model]
            e = [r for r in selected if r['method'] == 'exact']
            a = [r for r in selected if r['method'] == 'alns']
            coverage.append(dict(size=size, model=model,
                exact_feasible_cases=len({r['case'] for r in e if r['valid']}),
                exact_proven_optimal_cases=len({r['case'] for r in e if r.get('proven_optimal')}),
                alns_feasible_cases=len({r['case'] for r in a if r['valid']}),
                alns_300s_feasible_cases=len({r['case'] for r in a if r['valid'] and r['budget']==300})))
    write_csv(out/'tables/coverage.csv', coverage)
    active = []
    for path in (out/'runs').glob('*/attempt_*/live.json'):
        if not (path.parent/'completion.json').exists():
            active.append(dict(job=path.parent.parent.name, **d.read(path)))
    state = dict(updated_at=d.now(), planned=len(rows), terminal=len(rows_by_id), running=active,
        feasible_runs=sum(r['valid'] for r in rows),
        exact_optimal_cases=len({(r['case'],r['model']) for r in exact if r.get('proven_optimal')}),
        alns_feasible_cases=len({(r['case'],r['model']) for r in comparison if r['valid']}),
        statuses=dict(Counter(r['status'] for r in rows)),
        reference_conflicts=sum(r['reference_conflict'] for r in comparison),
        state='COMPLETED' if len(rows_by_id)==len(rows) else 'IN_PROGRESS')
    d.write(out/'progress.json', state)
    lines = ['# 新版27例：精确算法与ALNS时间效率', '',
        f'状态：{state["state"]}；任务终态 {state["terminal"]}/{state["planned"]}（包含条件跳过）。',
        f'精确已证最优 {state["exact_optimal_cases"]}/144 个小中规模模型算例；'
        f'ALNS至少一次独立审计可行 {state["alns_feasible_cases"]}/216 个模型算例。', '',
        '小规模精确：先300秒，未证最优从零重跑3600秒，仍未证最优再从零重跑10800秒；'
        '各次耗时独立列出，不能把重跑当作连续搜索。中规模精确600秒，大规模不跑精确。',
        'ALNS：独立冷启动10/60/300秒，种子42/43/44；300秒未找到可行解的种子追加900秒。'
        '追加不能覆盖或隐藏原始失败。精确全K域、compact等价表达、单线程、无外部初解。',
        '同时最多一个精确和一个ALNS进程；墙钟时间包含并发竞争。RESOURCE_LIMIT是内存保护中断，'
        '不能当作不可行。STOP文件停止派发，等待当前任务保存。', '',
        '[逐次运行](tables/runs.csv) · [ALNS GAP](tables/alns_gaps.csv) · '
        '[时间效率](tables/efficiency.csv) · [覆盖率](tables/coverage.csv)', '',
        '| 规模 | 模型 | 精确可行算例 | 精确已证最优 | ALNS可行算例 | 300秒ALNS可行算例 |',
        '|---|---|---:|---:|---:|---:|']
    for r in coverage:
        lines.append(f'| {r["size"]} | M{r["model"]} | {r["exact_feasible_cases"]}/9 | '
            f'{r["exact_proven_optimal_cases"]}/9 | {r["alns_feasible_cases"]}/9 | {r["alns_300s_feasible_cases"]}/9 |')
    lines += ['', '最优GAP = 100×(ALNS目标−已证最优目标)/|已证最优目标|。精确未证最优时，'
        '只报告与可行参照的差异、正下界给出的GAP上界；不填虚构的最优GAP。'
        'M1—M4距离目标、M5—M8费用目标分别比较，不直接平均不同模型的目标值。',
        '轨迹原始文件保存在各运行目录。Gurobi日志时间四舍五入到秒，包含建模时间偏移；'
        '中间解只由求解器报告，最终解另经独立双周期审计。M6—M8 ALNS全局轨迹在子问题返回时确认。',
        'M5—M8容器费用按完整780分钟周期租用：1×N，购置价格与初始购置投资为0；'
        '运输、清洗和显式磨损系数保持不变。每批次不重复计租。输入见证只用于核验输入可行，未用作初解。', '',
        f'参照冲突：{state["reference_conflicts"]}；状态分布：`{json.dumps(state["statuses"],ensure_ascii=False)}`。', '']
    (out/'report.md').write_text('\n'.join(lines), encoding='utf-8')
    return state


def run(args):
    out = Path(args.output).resolve()
    d = driver(out)
    manifest = d.read(out/'manifest.json')
    d.verify(out, manifest)
    with d.study_lock(out):
        rows = latest_rows(out, manifest, d)
        for job in manifest['jobs']:
            for path in d.attempt_dirs(out, job):
                if not d.complete_attempt(path):
                    launch = d.read(path/'launch.json') if (path/'launch.json').exists() else {}
                    if launch.get('pid') and d.pid_alive(launch['pid']):
                        raise RuntimeError(f'Prior worker still alive: {path}')
                    if (path/'live.json').exists():
                        import psutil
                        for member in d.read(path/'live.json').get('process_family', []):
                            try:
                                process = psutil.Process(member['pid'])
                                if abs(process.create_time()-member['created_at']) < .01:
                                    raise RuntimeError(f'Prior interpreter child still alive: {path}')
                            except psutil.NoSuchProcess:
                                pass
                    row = d.terminal_row(job, 'INTERRUPTED', 'Previous coordinator ended before completion')
                    row['attempt'] = int(path.name.split('_')[-1])
                    d.finalize_attempt(path, row)
                    rows[job['id']] = row
        queues = {method: [j for j in manifest['jobs'] if j['method']==method and j['id'] not in rows]
                  for method in ('exact', 'alns')}
        state = summarize(out)
        active = {}
        last_summary = time.monotonic()
        with ThreadPoolExecutor(max_workers=2) as pool:
            while active or any(queues.values()):
                if (out/'STOP').exists() and not active:
                    break
                for method in ('exact', 'alns'):
                    if method in active.values() or (out/'STOP').exists():
                        continue
                    while queues[method]:
                        job = queues[method][0]
                        if job['prerequisite'] and job['prerequisite'] not in rows:
                            break
                        queues[method].pop(0)
                        skip = conditional_skip(job, rows)
                        if skip:
                            path = out/'runs'/job['id']/'attempt_001'
                            path.mkdir(parents=True, exist_ok=False)
                            row = d.terminal_row(job, skip)
                            row['attempt'] = 1
                            d.finalize_attempt(path, row)
                            rows[job['id']] = row
                            continue
                        active[pool.submit(execute, out, manifest, job)] = method
                        break
                if not active:
                    if not (out/'STOP').exists() and any(queues.values()):
                        raise RuntimeError('Queue has unresolved prerequisites')
                    break
                done, _ = wait(active, timeout=10, return_when=FIRST_COMPLETED)
                for future in done:
                    active.pop(future)
                    row = future.result()
                    rows[row['id']] = row
                    print(f'{len(rows)}/{len(manifest["jobs"])} {row["id"]} {row["status"]} '
                          f'valid={row["valid"]} objective={row.get("objective")}', flush=True)
                if done or time.monotonic()-last_summary >= 60:
                    state = summarize(out)
                    last_summary = time.monotonic()
                    if state['reference_conflicts']:
                        (out/'STOP').write_text('Exact/ALNS reference contradiction; inspect before resuming.\n')
        state = summarize(out)
        print(json.dumps(state, ensure_ascii=False), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('prepare', 'run', 'worker', 'status'))
    parser.add_argument('--output', required=True)
    parser.add_argument('--dataset', default='data/formal_benchmark27_spacetime_v4_1')
    parser.add_argument('--pilot', action='store_true')
    parser.add_argument('--job')
    parser.add_argument('--attempt', type=int)
    args = parser.parse_args()
    if args.action == 'prepare':
        prepare(args)
    elif args.action == 'worker':
        worker(args)
    elif args.action == 'run':
        run(args)
    else:
        print(json.dumps(summarize(Path(args.output).resolve()), ensure_ascii=False))


if __name__ == '__main__':
    try:
        main()
    except Exception:
        traceback.print_exc()
        sys.exit(1)
