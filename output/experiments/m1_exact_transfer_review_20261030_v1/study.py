"""Frozen six-case, three-expression M1 study; a single seed, 42."""
from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import zipfile

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[2]
sys.path.insert(0, str(ROOT))


def save(path, value):
    path = Path(path)
    temporary = path.with_name(path.name+'.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
    temporary.replace(path)


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def now():
    return datetime.now(timezone.utc).isoformat()


def prepare():
    from tools.configuration.loader import load_routing_case
    import gurobipy as gp
    assert not (OUT/'plan.json').exists(), 'Refusing to replace frozen experiment'
    cases = [('small', 'S01'), ('small', 'S06'), ('small', 'S08'),
             ('medium', 'M01'), ('medium', 'M02'), ('medium', 'M03')]
    variants = ['expanded_baseline', 'compact_only', 'compact_network']
    files = set(OUT.glob('*.py'))
    for folder in ('models/common', 'models/routing/model_1/exact',
                   'models/routing/model_3/exact', 'tools/configuration'):
        files.update((ROOT/folder).rglob('*.py'))
    jobs = []
    for case_index, (size, case) in enumerate(cases):
        case_path = ROOT/f'data/formal_benchmark27_uniform_7_20_50_v1/model_1/{size}/{case}/case.json'
        cfg, _ = load_routing_case(case_path, use_pool_config=False)
        files.add(case_path)
        files.update(ROOT/cfg[key] for key in ('data', 'orders', 'routing_config', 'operating_policy',
                                               'economic_config', 'dataset_metadata', 'parameter_contract'))
        # Rotate expression order across cases; no repeated or additional seeds.
        ordered = variants[case_index % 3:]+variants[:case_index % 3]
        for variant in ordered:
            jobs.append(dict(id=f'{case}_{variant}_s42_30s', case=case, size=size,
                orders=7 if size == 'small' else 20, variant=variant, budget=30, seed=42,
                case_path=case_path.relative_to(ROOT).as_posix()))
    hashes = {p.relative_to(ROOT).as_posix(): sha(p) for p in files}
    with zipfile.ZipFile(OUT/'source_inputs.zip', 'x', compression=zipfile.ZIP_DEFLATED) as archive:
        for relative in sorted(hashes):
            archive.write(ROOT/relative, relative)
    plan = dict(created_at=now(), jobs=jobs, hashes=hashes, archive_sha256=sha(OUT/'source_inputs.zip'),
        model=1, seeds=[42], threads=1, concurrency=1, budget_seconds=30,
        interpreter=sys.executable, python=sys.version, gurobi_version=list(gp.gurobi.version()),
        historical_information_used=False, mip_start=False, production_code_changed=False,
        definitions=dict(expanded_baseline='Unmodified M1 default expanded complete MILP',
            compact_only='Unmodified M1 with explicit compact expression',
            compact_network='Compact + valid activation/trip rows + delivery flows + input LP/subset bounds'),
        timing='Case loading, model construction, all preparation, main optimize, extraction and independent audit; process launch separate.',
        limitations='Six selected cases, one seed; this is a transfer pilot, not a full benchmark or multi-seed claim.')
    save(OUT/'plan.json', plan)
    save(OUT/'progress.json', dict(status='PREPARED', completed=0, planned=len(jobs)))
    print(json.dumps(dict(prepared=len(jobs), seeds=[42], budget=30)), flush=True)


def verify(plan):
    for relative, expected in plan['hashes'].items():
        assert sha(ROOT/relative) == expected, f'Source/input changed: {relative}'
    assert sha(OUT/'source_inputs.zip') == plan['archive_sha256']


def worker(index):
    plan = read(OUT/'plan.json')
    verify(plan)
    job = plan['jobs'][index]
    target = OUT/'runs'/job['id']
    target.mkdir(parents=True, exist_ok=False)
    import gurobipy as gp
    from tools.configuration.loader import load_routing_case
    from models.common.cycle import load_instance
    from models.common.cycle.audit import audit_solution
    from models.routing.model_1.exact import solve_exact, milp_solver
    from candidate import strengthen
    begun = time.perf_counter()
    cfg, fleet = load_routing_case(ROOT/job['case_path'], use_pool_config=False)
    problem = load_instance(cfg, fleet)
    assert problem.model_number == 1 and len(problem.orders) == job['orders']
    assert not problem.reusable and not problem.free_assignment
    phases = []
    original_optimize = gp.Model.optimize
    original_build = milp_solver.build_model
    transfer = None

    def observed(model, *args, **kwargs):
        assert not args and not kwargs, 'No callbacks or alternative optimize arguments'
        model.update()
        phase = 'input_LP' if model.ModelName == 'm1_transfer_delivery_arc_LP' else 'complete_M1'
        logfile = target/f'{len(phases):02}_{phase}.log'
        model.Params.OutputFlag = 1
        model.Params.LogToConsole = 0
        model.Params.LogFile = logfile.relative_to(ROOT).as_posix()
        entry = dict(phase=phase, variables=model.NumVars, binary=model.NumBinVars,
            constraints=model.NumConstrs+model.NumGenConstrs, starts=model.NumStart,
            fingerprint=model.Fingerprint, begins_seconds=time.perf_counter()-begun)
        phases.append(entry)
        try:
            return original_optimize(model)
        finally:
            entry.update(native_status=model.Status, seconds=model.Runtime,
                         work=model.Work, nodes=model.NodeCount)

    def build_candidate(*args, **kwargs):
        nonlocal transfer
        model, context = original_build(*args, **kwargs)
        try:
            transfer = strengthen(problem, model, context, deadline=kwargs['deadline'], seed=42)
        except BaseException:
            model.dispose()
            raise
        return model, context

    if job['variant'] == 'compact_network':
        milp_solver.build_model = build_candidate
    gp.Model.optimize = observed
    try:
        result = solve_exact(problem, time_limit=max(.01, job['budget']-(time.perf_counter()-begun)), seed=42,
            milp_options=dict(threads=1, formulation='expanded' if job['variant'] == 'expanded_baseline' else 'compact'))
    finally:
        gp.Model.optimize = original_optimize
        milp_solver.build_model = original_build
    info = result['info']
    offline = time.perf_counter()
    audit = audit_solution(problem, result) if result.get('audit', {}).get('valid') else None
    if audit:
        assert audit['valid'] and audit['periodic_replay']['valid']
    main = [entry for entry in phases if entry['phase'] == 'complete_M1']
    if info.get('proven_optimal'):
        assert audit and main[-1]['native_status'] == gp.GRB.OPTIMAL
    row = dict(job, status=info['status'], optimal=info['proven_optimal'], feasible=bool(audit),
        objective=info['objective'], bound=info['best_bound'], gap=info['mip_gap'],
        total_seconds=time.perf_counter()-begun, solver_seconds=info['runtime'],
        offline_audit_seconds=time.perf_counter()-offline,
        preparation_seconds=info.get('build_time'), main_mip_seconds=info.get('mip_runtime'),
        nodes=info.get('node_count'), work=info.get('mip_work'), variables=info.get('variables'),
        binary=info.get('binary_variables'), constraints=info.get('constraints'),
        transfer=transfer, phases=phases, finished_at=now(),
        solver_config=info.get('solver_config'), historical_information_used=False)
    save(target/'result.json', result)
    save(target/'independent_audit.json', audit)
    save(target/'row.json', row)
    print(json.dumps({k: row[k] for k in ('id', 'status', 'objective', 'bound', 'gap', 'total_seconds')},
                     ensure_ascii=False), flush=True)


def run():
    plan = read(OUT/'plan.json')
    verify(plan)
    env = dict(os.environ, PYTHONUTF8='1', OMP_NUM_THREADS='1', OPENBLAS_NUM_THREADS='1', MKL_NUM_THREADS='1')
    for index, job in enumerate(plan['jobs']):
        target = OUT/'runs'/job['id']
        if (target/'row.json').exists():
            continue
        with (OUT/f"{job['id']}.launcher.log").open('w', encoding='utf-8') as stream:
            result = subprocess.run([sys.executable, '-X', 'utf8', str(Path(__file__)), 'worker', str(index)],
                cwd=ROOT, env=env, stdout=stream, stderr=subprocess.STDOUT)
        if result.returncode:
            save(OUT/'progress.json', dict(status='FAILED', active=job, returncode=result.returncode))
            raise RuntimeError(f"Worker failed: {job['id']}; inspect its launcher log")
        row = read(target/'row.json')
        completed = sum((OUT/'runs'/other['id']/'row.json').exists() for other in plan['jobs'])
        save(OUT/'progress.json', dict(status='RUNNING', completed=completed, planned=len(plan['jobs']),
                                     last=row['id'], updated_at=now()))
        print(json.dumps({k: row[k] for k in ('id', 'status', 'objective', 'gap', 'total_seconds')},
                         ensure_ascii=False), flush=True)
    summarize()


def summarize():
    plan = read(OUT/'plan.json')
    verify(plan)
    rows = [read(OUT/'runs'/job['id']/'row.json') for job in plan['jobs']]
    fields = ['case', 'size', 'orders', 'variant', 'budget', 'seed', 'status', 'optimal', 'feasible',
              'objective', 'bound', 'gap', 'total_seconds', 'preparation_seconds', 'main_mip_seconds',
              'nodes', 'work', 'variables', 'binary', 'constraints']
    with (OUT/'runs.csv').open('w', newline='', encoding='utf-8-sig') as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction='ignore')
        writer.writeheader()
        writer.writerows(rows)
    save(OUT/'rows.json', rows)
    by_case = {}
    for row in rows:
        by_case.setdefault(row['case'], []).append(row)
    for case_rows in by_case.values():
        optimal_values = [r['objective'] for r in case_rows if r['optimal']]
        if optimal_values:
            optimum = optimal_values[0]
            for row in case_rows:
                if row['optimal']:
                    assert abs(row['objective']-optimum) <= 1e-5
                if row['bound'] is not None:
                    assert row['bound'] <= optimum+1e-5
    artifacts = {p.relative_to(OUT).as_posix(): sha(p) for job in plan['jobs']
                 for p in (OUT/'runs'/job['id']).glob('*.json')}
    save(OUT/'verification.json', dict(passed=True, source_inputs_unchanged=True,
        artifacts=artifacts, completed=len(rows), audited_feasible=sum(r['feasible'] for r in rows),
        optimal=sum(r['optimal'] for r in rows), scope='6 cases, 3 expressions, seed 42, single-thread',
        production_code_changed=False))
    save(OUT/'progress.json', dict(status='COMPLETED', completed=len(rows), planned=len(rows), updated_at=now()))
    print(json.dumps(dict(completed=len(rows), optimal=sum(r['optimal'] for r in rows))), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=['prepare', 'worker', 'run', 'summarize'])
    parser.add_argument('index', nargs='?', type=int)
    args = parser.parse_args()
    if args.command == 'worker':
        worker(args.index)
    else:
        globals()[args.command]()
