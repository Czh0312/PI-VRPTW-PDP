"""All nine M3 medium cases: unchanged solver, seed 42, 1800s each."""
from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time
import zipfile

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[2]
sys.path.insert(0, str(ROOT))


def now():
    return datetime.now(timezone.utc).isoformat()


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def save(path, value):
    path = Path(path)
    temporary = path.with_name(path.name+'.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
    temporary.replace(path)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def verify(plan):
    for relative, expected in plan['hashes'].items():
        assert sha(ROOT/relative) == expected, relative


def prepare():
    import gurobipy as gp
    import psutil
    from tools.configuration.loader import load_routing_case

    assert not (OUT/'plan.json').exists(), 'Refusing to overwrite a frozen study'
    files = {Path(__file__), ROOT/'models/__init__.py', ROOT/'models/routing/__init__.py',
             ROOT/'models/routing/catalog.py', ROOT/'models/routing/model_3/__init__.py',
             ROOT/'tools/__init__.py'}
    for folder in ('models/routing/model_3/exact', 'models/common', 'models/cost', 'tools/configuration'):
        files.update((ROOT/folder).rglob('*.py'))
    jobs = []
    cases = [('medium', f'M{i:02}', 20) for i in range(1, 10)]
    for size, case, count in cases:
        case_path = ROOT/f'data/formal_benchmark27_uniform_7_20_50_v1/model_3/{size}/{case}/case.json'
        config, _ = load_routing_case(case_path, use_pool_config=False)
        files.add(case_path)
        files.update(ROOT/config[key] for key in ('data', 'orders', 'routing_config', 'operating_policy',
                                                  'economic_config', 'dataset_metadata', 'parameter_contract'))
        jobs.append(dict(id=f'{case}_general_s42_1800s', case=case, size=size, orders=count,
                         seed=42, budget=1800, case_path=case_path.relative_to(ROOT).as_posix()))
    hashes = {p.relative_to(ROOT).as_posix(): sha(p) for p in files}
    previous = read(ROOT/'output/experiments/m3_general_acceleration_20261029_v1/plan.json')
    inherited_source_hashes = {relative: expected for relative, expected in previous['hashes'].items()
                               if relative.startswith(('models/', 'tools/')) and relative.endswith('.py')}
    for relative, expected in inherited_source_hashes.items():
        assert hashes[relative] == expected, f'Solver changed since the validated version: {relative}'
    plan = dict(created_at=now(), model=3, jobs=jobs, hashes=hashes,
        interpreter=sys.executable, python=sys.version, gurobi_version=list(gp.gurobi.version()),
        invocation='solve_exact(problem, time_limit=1800, seed=42)',
        formulation='same default compact on every input', threads=1, workers=1, seed=42,
        budget_seconds=1800, historical_information_used=False, independent_process_per_call=True,
        source_matches_previous_validated_version=True,
        previous_source_hashes_checked=len(inherited_source_hashes),
        memory_measurement='peak sampled sum of RSS for the launcher and all live descendants',
        timing='Case loading, fresh preparation, full main solve, extraction and audit; process startup recorded separately.',
        host=dict(physical_cpus=psutil.cpu_count(logical=False), logical_cpus=psutil.cpu_count(),
                  total_memory_mib=psutil.virtual_memory().total/1024**2))
    save(OUT/'plan.json', plan)
    with zipfile.ZipFile(OUT/'source_inputs.zip', 'x', compression=zipfile.ZIP_DEFLATED) as archive:
        for relative in sorted(hashes):
            archive.write(ROOT/relative, relative)
    save(OUT/'progress.json', dict(planned=len(jobs), completed=0, status='PREPARED', updated_at=now()))
    print(json.dumps(dict(planned=len(jobs), seed=42, budget_per_call=1800)), flush=True)


def worker(index):
    import gurobipy as gp
    from tools.configuration.loader import load_routing_case
    from models.common.cycle import load_instance
    from models.common.cycle.audit import audit_solution
    from models.routing.model_3.exact import solve_exact

    plan = read(OUT/'plan.json')
    verify(plan)
    job = plan['jobs'][index]
    target = OUT/'runs'/job['id']
    begun = time.perf_counter()
    cfg, fleet = load_routing_case(ROOT/job['case_path'], use_pool_config=False)
    p = load_instance(cfg, fleet)
    assert p.model_number == 3 and p.cost_objective and len(p.orders) == job['orders']
    phases = []
    original = gp.Model.optimize

    def observed(model, *args, **kwargs):
        assert not args and not kwargs
        model.update()
        phase = {'m3_site_arc_relaxation': 'site_LP',
                 'm3_input_route_partition_lower_bound': 'complete_site_partition',
                 'm3_fresh_partition_schedule': 'internal_schedule'}.get(model.ModelName, 'full_model')
        logfile = f'{len(phases):02}_{phase}.log'
        model.Params.OutputFlag = 1
        model.Params.LogToConsole = 0
        model.Params.LogFile = (target.relative_to(ROOT)/logfile).as_posix()
        record = dict(phase=phase, model_name=model.ModelName, logfile=logfile,
            variables=model.NumVars, binary=model.NumBinVars,
            linear_constraints=model.NumConstrs, general_constraints=model.NumGenConstrs,
            fingerprint=model.Fingerprint, starts=model.NumStart,
            begins_after_seconds=time.perf_counter()-begun)
        phases.append(record)
        try:
            return original(model)
        finally:
            record.update(status=model.Status, optimize_seconds=model.Runtime, work=model.Work,
                          nodes=model.NodeCount, ends_after_seconds=time.perf_counter()-begun)

    gp.Model.optimize = observed
    try:
        result = solve_exact(p, time_limit=job['budget'], seed=job['seed'])
    finally:
        gp.Model.optimize = original
    info = result['info']
    mains = [phase for phase in phases if phase['phase'] == 'full_model']
    audit = audit_solution(p, result) if result.get('audit', {}).get('valid') else None
    if audit:
        assert audit['valid'] and audit['periodic_replay']['valid']
    if info['proven_optimal']:
        assert audit and mains[-1]['status'] == gp.GRB.OPTIMAL
        assert abs(info['best_bound']-audit['objective']) <= 1e-6
    strengthening = info.get('route_strengthening', {})
    config = info.get('solver_config', {})
    assert config['formulation'] == 'compact' and config['threads'] == 1
    row = dict(job, status=info['status'], optimal=info['proven_optimal'], feasible=bool(audit),
        infeasible=info.get('proven_infeasible', False),
        objective=info['objective'], bound=info['best_bound'], gap=info['mip_gap'],
        total_seconds=time.perf_counter()-begun, solver_seconds=info['runtime'],
        proof_seconds=mains[-1]['ends_after_seconds'] if info['proven_optimal'] and mains else None,
        build_seconds=info.get('build_time'), preparation_seconds=info.get('preparation_time'),
        main_mip_seconds=info.get('mip_runtime'), main_work=info.get('mip_work'), main_nodes=info.get('node_count'),
        binary=mains[-1]['binary'] if mains else None, variables=mains[-1]['variables'] if mains else None,
        native_phases=phases, bound_applied=strengthening.get('applied', False),
        complete_library=strengthening.get('complete_library', False),
        completed_sites=strengthening.get('completed_sites', []),
        initial_cost=info.get('internal_start', {}).get('objective'),
        optimized_fresh_start=info.get('mip_start_supplied', False), history_used=False,
        finished_at=now(), economic_profile_hash=p.cost_profile.hash,
        warehouse_orders={site: sum(order.satellite == site for order in p.orders)
                          for site in p.satellites if any(order.satellite == site for order in p.orders)})
    save(target/'result.json', result)
    save(target/'independent_audit.json', audit)
    save(target/'row.json', row)
    verify(plan)
    save(target/'completion.json', dict(files={name: sha(target/name) for name in (
        'result.json', 'independent_audit.json', 'row.json')}, completed_at=now()))
    print(json.dumps(row, ensure_ascii=False), flush=True)


def run():
    import psutil
    plan = read(OUT/'plan.json')
    verify(plan)
    for index, job in enumerate(plan['jobs']):
        target = OUT/'runs'/job['id']
        if (target/'completion.json').exists():
            continue
        assert not target.exists(), f'Incomplete run needs inspection: {target}'
        target.mkdir(parents=True)
        save(OUT/'progress.json', dict(planned=len(plan['jobs']), completed=index,
            active=job['id'], status='RUNNING', updated_at=now()))
        print('START '+job['id'], flush=True)
        begun, peak, root_peak, peak_process_count = time.perf_counter(), 0., 0., 0
        with (target/'stdout.log').open('w', encoding='utf-8') as stdout, (target/'stderr.log').open('w', encoding='utf-8') as stderr:
            process = subprocess.Popen([sys.executable, str(Path(__file__)), 'worker', '--index', str(index)],
                                       cwd=ROOT, stdout=stdout, stderr=stderr)
            monitor = psutil.Process(process.pid)
            while process.poll() is None:
                try:
                    processes = [monitor]+monitor.children(recursive=True)
                    tree_rss = 0.
                    for member in processes:
                        try:
                            tree_rss += member.memory_info().rss/1024**2
                        except psutil.Error:
                            pass
                    peak = max(peak, tree_rss)
                    root_peak = max(root_peak, monitor.memory_info().rss/1024**2)
                    peak_process_count = max(peak_process_count, len(processes))
                except psutil.Error:
                    pass
                time.sleep(1)
            code = process.returncode
        save(target/'process.json', dict(returncode=code, seconds=time.perf_counter()-begun,
            peak_process_tree_rss_mib=peak, launcher_peak_rss_mib=root_peak,
            maximum_process_count=peak_process_count, sampling_interval_seconds=1))
        assert code == 0 and (target/'completion.json').exists(), f'Worker failed: {job["id"]}'
        row = read(target/'row.json')
        print(json.dumps({key: row[key] for key in ('case', 'status', 'objective', 'bound', 'gap', 'total_seconds')}, ensure_ascii=False), flush=True)
        completed = [read(OUT/'runs'/previous['id']/'row.json') for previous in plan['jobs']
                     if (OUT/'runs'/previous['id']/'completion.json').exists()]
        save(OUT/'partial_rows.json', completed)
        save(OUT/'progress.json', dict(planned=len(plan['jobs']), completed=len(completed),
            optimal=sum(r['optimal'] for r in completed), feasible=sum(r['feasible'] for r in completed),
            status='RUNNING', updated_at=now()))
    rows = [read(OUT/'runs'/job['id']/'row.json') for job in plan['jobs']]
    for row in rows:
        row.update(read(OUT/'runs'/row['id']/'process.json'))
    save(OUT/'rows.json', rows)
    fields = ['case', 'orders', 'seed', 'budget', 'status', 'optimal', 'feasible', 'objective', 'bound', 'gap',
              'total_seconds', 'preparation_seconds', 'main_mip_seconds', 'variables', 'binary',
              'proof_seconds', 'infeasible', 'bound_applied', 'complete_library', 'optimized_fresh_start',
              'peak_process_tree_rss_mib']
    with (OUT/'runs.csv').open('w', encoding='utf-8-sig', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction='ignore')
        writer.writeheader()
        writer.writerows(rows)
    progress = dict(planned=len(rows), completed=len(rows), optimal=sum(row['optimal'] for row in rows),
                    feasible=sum(row['feasible'] for row in rows), status='COMPLETE', updated_at=now())
    save(OUT/'progress.json', progress)
    verify(plan)
    print(json.dumps(progress), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['prepare', 'run', 'worker'])
    parser.add_argument('--index', type=int)
    args = parser.parse_args()
    if args.action == 'prepare':
        prepare()
    elif args.action == 'run':
        run()
    else:
        worker(args.index)
