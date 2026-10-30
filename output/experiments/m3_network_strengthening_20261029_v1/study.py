"""Frozen M03 before/after and cross-size validation, seed 42 only."""
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
    assert sha(OUT/'source_before.zip') == plan['before_archive_sha256']
    for relative, expected in plan['before_hashes'].items():
        assert sha(OUT/'before'/relative) == expected, relative


def prepare():
    import gurobipy as gp
    import psutil
    from tools.configuration.loader import load_routing_case

    assert not (OUT/'plan.json').exists(), 'Refusing to overwrite a frozen study'
    before_root = OUT/'before'
    before_root.mkdir(exist_ok=True)
    with zipfile.ZipFile(OUT/'source_before.zip') as archive:
        for member in archive.infolist():
            destination = (before_root/member.filename).resolve()
            assert before_root.resolve() in destination.parents
            archive.extract(member, before_root)
    previous = read(ROOT/'output/experiments/m3_all_medium_exact_1800s_20261029_v1/plan.json')
    before_hashes = previous['hashes']
    for relative, expected in before_hashes.items():
        assert sha(before_root/relative) == expected, relative
    files = {Path(__file__), ROOT/'models/__init__.py', ROOT/'models/routing/__init__.py',
             ROOT/'models/routing/catalog.py', ROOT/'models/routing/model_3/__init__.py',
             ROOT/'tools/__init__.py'}
    for folder in ('models/routing/model_3/exact', 'models/common', 'models/cost', 'tools/configuration'):
        files.update((ROOT/folder).rglob('*.py'))
    jobs = []
    cases = [('medium', 'M03', 20, 'before', 300), ('medium', 'M03', 20, 'after', 300)]
    cases += [('small', f'S{i:02}', 7, 'after', 300) for i in range(1, 10)]
    cases += [('medium', f'M{i:02}', 20, 'after', 300) for i in range(1, 10) if i != 3]
    cases += [('large', 'L01', 50, 'after', 300)]
    cases += [('medium', 'M03', 20, 'after', 1800)]
    for size, case, count, version, budget in cases:
        case_path = ROOT/f'data/formal_benchmark27_uniform_7_20_50_v1/model_3/{size}/{case}/case.json'
        config, _ = load_routing_case(case_path, use_pool_config=False)
        files.add(case_path)
        files.update(ROOT/config[key] for key in ('data', 'orders', 'routing_config', 'operating_policy',
                                                  'economic_config', 'dataset_metadata', 'parameter_contract'))
        job = dict(id=f'{case}_{version}_s42_{budget}s', case=case, size=size, orders=count,
                   version=version, seed=42, budget=budget, case_path=case_path.relative_to(ROOT).as_posix())
        if budget == 1800:
            job['only_if_unproven'] = 'M03_after_s42_300s'
        jobs.append(job)
    for test in ('test_model3_resource_flows.py', 'test_model3_network_cuts.py',
                 'test_model3_route_partition.py', 'test_model3_cost_objective.py', 'test_milp_baseline.py'):
        files.add(ROOT/'tests/routing'/test)
    files.add(ROOT/'docs/model3_network_strengthening.md')
    hashes = {p.relative_to(ROOT).as_posix(): sha(p) for p in files}
    plan = dict(created_at=now(), model=3, jobs=jobs, hashes=hashes,
        interpreter=sys.executable, python=sys.version, gurobi_version=list(gp.gurobi.version()),
        invocation='solve_exact(problem, time_limit=job.budget, seed=42)',
        formulation='same default compact on every input', threads=1, workers=1, seed=42,
        budget_seconds=300, historical_information_used=False, independent_process_per_call=True,
        optional_confirmation_budget_seconds=1800,
        before_archive_sha256=sha(OUT/'source_before.zip'), before_hashes=before_hashes,
        before_definition='Frozen prior general optimizer, compact, input-only bound/start; not raw expanded.',
        after_definition='Current default: arc load flows, subset/causal/time rows, fresh candidate improvement.',
        memory_measurement='peak sampled sum of RSS for the launcher and all live descendants',
        timing='Case loading, fresh preparation, full main solve, extraction and audit; process startup recorded separately.',
        host=dict(physical_cpus=psutil.cpu_count(logical=False), logical_cpus=psutil.cpu_count(),
                  total_memory_mib=psutil.virtual_memory().total/1024**2))
    save(OUT/'plan.json', plan)
    with zipfile.ZipFile(OUT/'source_after.zip', 'x', compression=zipfile.ZIP_DEFLATED) as archive:
        for relative in sorted(hashes):
            archive.write(ROOT/relative, relative)
    save(OUT/'progress.json', dict(planned=len(jobs), completed=0, status='PREPARED', updated_at=now()))
    print(json.dumps(dict(planned=len(jobs), seed=42, regular_budget=300, optional_confirmation=1800)), flush=True)


def worker(index):
    plan = read(OUT/'plan.json')
    verify(plan)
    job = plan['jobs'][index]
    source_root = OUT/'before' if job['version'] == 'before' else ROOT
    sys.path.insert(0, str(source_root))
    import gurobipy as gp
    from tools.configuration.loader import load_routing_case
    from models.common.cycle import load_instance
    from models.common.cycle.audit import audit_solution
    from models.routing.model_3.exact import solve_exact
    from models.routing.model_3.exact import milp_solver
    assert Path(milp_solver.__file__).resolve().is_relative_to(source_root.resolve())
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
        loaded_solver_source=str(Path(milp_solver.__file__).resolve()),
        resource_flows=config.get('resource_flows', {}).get('applied', False),
        precedence_rows=config.get('precedence_links', {}).get('rows', 0),
        lifted_time_rows=config.get('precedence_links', {}).get('time_lifts', 0),
        subset_cuts=sum(site.get('main_capacity_cuts', 0) for site in strengthening.get('sites', {}).values()),
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
        if job.get('only_if_unproven'):
            prior = read(OUT/'runs'/job['only_if_unproven']/'row.json')
            if prior['optimal']:
                target.mkdir(parents=True)
                decision = dict(job, status='NOT_NEEDED_ALREADY_OPTIMAL', optimal=False, feasible=False,
                                elapsed_seconds=0, conditional_job_not_run=True)
                save(target/'row.json', decision)
                save(target/'completion.json', dict(files={'row.json': sha(target/'row.json')}, completed_at=now()))
                print('NOT NEEDED '+job['id'], flush=True)
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
        process_path = OUT/'runs'/row['id']/'process.json'
        if process_path.exists():
            row.update(read(process_path))
    save(OUT/'rows.json', rows)
    fields = ['case', 'orders', 'seed', 'budget', 'status', 'optimal', 'feasible', 'objective', 'bound', 'gap',
              'total_seconds', 'preparation_seconds', 'main_mip_seconds', 'variables', 'binary',
              'version', 'proof_seconds', 'infeasible', 'bound_applied', 'complete_library', 'optimized_fresh_start',
              'resource_flows', 'precedence_rows', 'lifted_time_rows', 'subset_cuts',
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
