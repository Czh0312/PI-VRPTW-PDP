"""Nine independent-process M3 before/after exact pairs, 300 seconds each."""
from __future__ import annotations

import argparse
from collections import Counter
import csv
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
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
    temporary = path.with_name(path.name + '.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
    temporary.replace(path)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def verify(plan):
    for relative, expected in plan['hashes'].items():
        assert sha(ROOT / relative) == expected, relative


def prepare():
    from tools.configuration.loader import load_routing_case
    import gurobipy as gp
    import psutil
    assert not (OUT / 'plan.json').exists(), 'Refusing to overwrite a study'
    files = {Path(__file__), ROOT / 'models/__init__.py', ROOT / 'models/routing/__init__.py',
             ROOT / 'models/routing/catalog.py', ROOT / 'models/routing/model_3/__init__.py', ROOT / 'tools/__init__.py'}
    for folder in ('models/routing/model_3/exact', 'models/common', 'models/cost', 'tools/configuration'):
        files.update((ROOT / folder).rglob('*.py'))
    jobs = []
    for i in range(1, 10):
        case = f'S{i:02}'
        case_path = ROOT / f'data/formal_benchmark27_uniform_7_20_50_v1/model_3/small/{case}/case.json'
        config, _ = load_routing_case(case_path, use_pool_config=False)
        files.add(case_path)
        files.update(ROOT / config[key] for key in ('data', 'orders', 'routing_config', 'operating_policy',
                                                  'economic_config', 'dataset_metadata', 'parameter_contract'))
        for version in (['before', 'after'] if i % 2 else ['after', 'before']):
            jobs.append(dict(id=f'{case}_{version}_s42_300s', case=case, version=version,
                             seed=42, budget=300, case_path=case_path.relative_to(ROOT).as_posix()))
    hashes = {p.relative_to(ROOT).as_posix(): sha(p) for p in files}
    plan = dict(created_at=now(), model=3, small_cases=9, jobs=jobs, hashes=hashes,
        interpreter=sys.executable, python=sys.version, gurobi_version=list(gp.gurobi.version()),
        before='route_strengthening=False: original complete cold MILP, no supplied start or route bound',
        after='route_strengthening=True: current input-only bound, fresh internal start, complete unrestricted main MILP',
        formulation='expanded', threads=1, workers=1, seed=42, budget_seconds=300,
        historical_information_used=False, independent_process_per_call=True,
        timing='Case loading, all fresh preparation, complete main optimize, extraction, independent audit; process startup recorded separately.',
        before_after_order='Alternate before/after ordering between cases.',
        host=dict(physical_cpus=psutil.cpu_count(logical=False), logical_cpus=psutil.cpu_count(),
                  total_memory_mib=psutil.virtual_memory().total/1024**2,
                  available_memory_mib=psutil.virtual_memory().available/1024**2))
    save(OUT / 'plan.json', plan)
    with zipfile.ZipFile(OUT / 'source_inputs.zip', 'x', compression=zipfile.ZIP_DEFLATED) as archive:
        for relative in sorted(hashes):
            archive.write(ROOT / relative, relative)
    save(OUT / 'progress.json', dict(planned=18, completed=0, optimal=0, status='PREPARED', updated_at=now()))
    print(json.dumps(dict(planned=18, cases=9, budget_per_call=300), ensure_ascii=False), flush=True)


def worker(index):
    import gurobipy as gp
    from tools.configuration.loader import load_routing_case
    from models.common.cycle import load_instance
    from models.common.cycle.audit import audit_solution
    from models.routing.model_3.exact import solve_exact
    plan = read(OUT / 'plan.json')
    verify(plan)
    job = plan['jobs'][index]
    target = OUT / 'runs' / job['id']
    begun = time.perf_counter()
    cfg, fleet = load_routing_case(ROOT / job['case_path'], use_pool_config=False)
    p = load_instance(cfg, fleet)
    assert p.model_number == 3 and p.cost_objective and len(p.orders) == p.total == 7
    phases = []
    original = gp.Model.optimize

    def observed(model, *args, **kwargs):
        assert not args and not kwargs
        model.update()  # Metadata such as Fingerprint needs pending variables flushed.
        phase = {'m3_input_route_partition_lower_bound': 'route_bound',
                 'm3_fresh_partition_schedule': 'internal_schedule'}.get(model.ModelName, 'full_model')
        model.Params.OutputFlag = 1
        model.Params.LogToConsole = 0
        model.Params.LogFile = (target.relative_to(ROOT) / (phase + '.log')).as_posix()
        record = dict(phase=phase, variables=model.NumVars, binary=model.NumBinVars,
            linear_constraints=model.NumConstrs, general_constraints=model.NumGenConstrs,
            fingerprint=model.Fingerprint, starts=model.NumStart,
            begins_after_seconds=time.perf_counter()-begun)
        try:
            return original(model)
        finally:
            record.update(status=model.Status, optimize_seconds=model.Runtime, work=model.Work,
                          nodes=model.NodeCount, ends_after_seconds=time.perf_counter()-begun)
            phases.append(record)

    gp.Model.optimize = observed
    try:
        result = solve_exact(p, time_limit=job['budget'], seed=job['seed'],
            milp_options=dict(threads=1, formulation='expanded'), route_strengthening=job['version'] == 'after')
    finally:
        gp.Model.optimize = original
    main = [record for record in phases if record['phase'] == 'full_model']
    info = result['info']
    audit = audit_solution(p, result) if result.get('audit', {}).get('valid') else None
    if audit:
        assert audit['valid'] and audit['periodic_replay']['valid']
    if info['proven_optimal']:
        assert audit and main[-1]['status'] == gp.GRB.OPTIMAL
        assert abs(info['best_bound']-audit['objective']) <= 1e-6
    if job['version'] == 'before':
        assert len(phases) == 1 and phases[0]['starts'] == 0
        assert not info['route_strengthening']['applied'] and not info['mip_start_supplied']
    row = dict(job, status=info['status'], optimal=info['proven_optimal'],
        feasible=bool(audit), objective=info['objective'], bound=info['best_bound'], gap=info['mip_gap'],
        total_seconds=time.perf_counter()-begun,
        proof_seconds=main[-1]['ends_after_seconds'] if info['proven_optimal'] and main else None,
        build_seconds=info.get('build_time'), preparation_seconds=info.get('preparation_time'),
        main_mip_seconds=info.get('mip_runtime'), main_work=info.get('mip_work'),
        main_nodes=info.get('node_count'), native_phases=phases,
        optimized_fresh_start=info.get('mip_start_supplied', False), history_used=False,
        finished_at=now(), economic_profile_hash=p.cost_profile.hash)
    save(target / 'result.json', result)
    save(target / 'independent_audit.json', audit)
    save(target / 'row.json', row)
    verify(plan)
    save(target / 'completion.json', dict(files={name: sha(target / name) for name in (
        'result.json', 'independent_audit.json', 'row.json')}, finished_at=now()))
    print(json.dumps({key: row[key] for key in ('case','version','status','optimal','objective','total_seconds','main_work')}, ensure_ascii=False), flush=True)


def run():
    import psutil
    plan = read(OUT / 'plan.json')
    verify(plan)
    rows = []
    for index, job in enumerate(plan['jobs']):
        target = OUT / 'runs' / job['id']
        if (target / 'completion.json').exists():
            for name, digest in read(target / 'completion.json')['files'].items():
                assert sha(target / name) == digest, str(target / name)
            row = read(target / 'row.json')
            process_info = read(target / 'process.json')
            row.update(process_wall_seconds=process_info['wall_seconds'], peak_rss_mib=process_info['peak_rss_mib'])
            rows.append(row)
            print('KEEP COMPLETED ' + job['id'], flush=True)
            continue
        target.mkdir(parents=True)
        print('START ' + job['id'], flush=True)
        env = dict(os.environ, OPENBLAS_NUM_THREADS='1', MKL_NUM_THREADS='1', OMP_NUM_THREADS='1')
        command = [sys.executable, str(Path(__file__).resolve()), 'worker', '--index', str(index)]
        start = time.perf_counter()
        peak_rss = 0
        with (target / 'worker.log').open('w', encoding='utf-8') as stream:
            child = subprocess.Popen(command, cwd=ROOT, env=env, stdout=stream, stderr=subprocess.STDOUT)
            process = psutil.Process(child.pid)
            while child.poll() is None:
                try:
                    peak_rss = max(peak_rss, process.memory_info().rss)
                except psutil.NoSuchProcess:
                    pass
                if time.perf_counter()-start > job['budget']+60:
                    child.kill()
                    child.wait()
                    raise RuntimeError('Worker exceeded total outer budget: ' + job['id'])
                time.sleep(.5)
        if child.returncode:
            raise RuntimeError('Worker failed; inspect ' + str(target / 'worker.log'))
        row = read(target / 'row.json')
        row.update(process_wall_seconds=time.perf_counter()-start, peak_rss_mib=peak_rss/1024**2)
        save(target / 'process.json', dict(wall_seconds=row['process_wall_seconds'], peak_rss_mib=row['peak_rss_mib']))
        rows.append(row)
        save(OUT / 'rows.json', rows)
        progress = dict(planned=18, completed=len(rows), optimal=sum(r['optimal'] for r in rows),
                        statuses=dict(Counter(r['status'] for r in rows)), status='COMPLETED' if len(rows) == 18 else 'RUNNING', updated_at=now())
        save(OUT / 'progress.json', progress)
        print(json.dumps({key: row[key] for key in ('case','version','status','optimal','total_seconds')}, ensure_ascii=False), flush=True)
    verify(plan)
    fields = ['case','version','seed','budget','status','optimal','feasible','objective','bound','gap',
              'total_seconds','proof_seconds','build_seconds','preparation_seconds','main_mip_seconds',
              'main_work','main_nodes','optimized_fresh_start','process_wall_seconds','peak_rss_mib']
    with (OUT / 'runs.csv').open('w', encoding='utf-8-sig', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction='ignore')
        writer.writeheader()
        writer.writerows(rows)
    print(json.dumps(progress, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['prepare','run','worker'])
    parser.add_argument('--index', type=int)
    args = parser.parse_args()
    if args.action == 'prepare':
        prepare()
    elif args.action == 'worker':
        worker(args.index)
    else:
        run()
