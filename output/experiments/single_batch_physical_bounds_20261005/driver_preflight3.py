"""Registered serial M3 physical-floor experiment; never rerun sealed records."""
from datetime import datetime, timezone
from pathlib import Path
import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT/'output/experiments/single_batch_physical_bounds_20261005'
OLD = ROOT/'output/experiments/single_batch_exact_overnight_20261002/e1_clocked_route_cover01/inputs_snapshot'
OWNED = ['models/routing/solvers/exact/single_batch_physical_bounds.py',
         'models/routing/solvers/exact/solver.py', 'tests/routing/test_single_batch_physical_bounds.py']


def read(p):
    return json.loads(Path(p).read_text())


def write(p, value):
    Path(p).write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)+'\n')


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def load_case(path):
    from configuration.loader import load_routing_case
    from models.routing.common.cycle import load_instance
    config, fleet = load_routing_case(path)
    return load_instance(config, fleet)


def load_record():
    names = ['alns_single_batch_overnight_20261001', 'm5_overnight_20261001',
        'model5_alns_overnight_20261001', 'model5_hybrid_round8_20261005',
        'm5_logic_benders_20261005/group_first', 'alns_single_batch_diverse_descent_20261005']
    states = {}
    for name in names:
        path = ROOT/'output/experiments'/name/'checkpoint.json'
        if path.exists():
            value = read(path)
            states[name] = {k: value.get(k) for k in ('status', 'active_process', 'running', 'active_worker')}
    return dict(utc=datetime.now(timezone.utc).isoformat(), cpu_count=os.cpu_count(),
                loadavg=os.getloadavg(), checkpoints=states, exclusive=False,
                limitation='Process inventory unavailable; checkpoints cannot rule out other work.')


def prepare():
    sys.path.insert(0, str(ROOT))
    from models.routing.common.cycle.audit import audit_solution
    from models.routing.solvers.exact.single_batch_columns import problem_fingerprint
    from models.routing.solvers.exact.single_batch_physical_bounds import build_physical_site, project_original_site
    assert '38 passed' in (OUT/'targeted_attempt1.log').read_text()
    assert not (OUT/'jobs.json').exists()
    shutil.copytree(OUT/'baseline_source', OUT/'candidate_source')
    for rel in OWNED:
        target = OUT/'candidate_source'/rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT/rel, target)
    manifest = {str(p.relative_to(OUT/'candidate_source')): sha(p)
        for p in (OUT/'candidate_source').rglob('*') if p.is_file() and '__pycache__' not in p.parts}
    write(OUT/'candidate_manifest.json', manifest)
    old_manifest = read(OUT/'baseline_manifest.json')
    changed = {k for k in set(old_manifest)|set(manifest) if old_manifest.get(k) != manifest.get(k)}
    assert changed == set(OWNED), changed
    shutil.copy2(__file__, OUT/'driver_frozen.py')
    jobs, cases = [], []
    for source_index, name in enumerate(['zhou_Set2_100-5-1b_subset20', 'zhou_Set2_100-5-2_subset20']):
        folder = OUT/'inputs'/name
        folder.mkdir(parents=True)
        cfg = read(OLD/name/'case.json')
        shutil.copy2(OLD/name/'case.json', folder/'original_case.json')
        origins = {}
        for key in ('data', 'orders', 'routing_config', 'operating_policy', 'dataset_metadata', 'pool_config'):
            source = Path(cfg[key]); target = folder/(key+source.suffix)
            shutil.copy2(source, target)
            assert sha(source) == sha(target)
            origins[key] = dict(path=str(source), sha256=sha(source))
            cfg[key] = str(target)
        write(folder/'case.json', cfg)
        shutil.copy2(OLD/name/'start.json', folder/'initial_result.json')
        problem = load_case(folder/'case.json')
        initial = read(folder/'initial_result.json')
        audited = audit_solution(problem, initial)
        assert audited['valid'] and audited['periodic_replay']['valid']
        assert problem.model_number == 3 and len(problem.orders) == 20
        projections = []
        for site in sorted({o.satellite for o in problem.orders}):
            model, context = build_physical_site(problem, site)
            try:
                record = project_original_site(problem, context, initial, fix=True)
                model.Params.TimeLimit = 5
                model.optimize()
                assert model.Status == 2 and abs(model.ObjVal-record['cost']) < 1e-6
                projections.append(record)
            finally:
                model.dispose()
        assert sum(r['tasks'] for r in projections) == 40
        write(folder/'preparation_audit.json', dict(audit=audited, projections=projections, original_sources=origins))
        case = dict(case=name, input=str(folder/'case.json'), start=str(folder/'initial_result.json'),
                    input_sha256=sha(folder/'case.json'), start_sha256=sha(folder/'initial_result.json'),
                    original_fp=problem_fingerprint(problem))
        cases.append(case)
        for si, seed in enumerate((42, 43, 44)):
            for bi, budget in enumerate((15, 30)):
                methods = ('baseline', 'physical') if (source_index+si+bi)%2 == 0 else ('physical', 'baseline')
                for method in methods:
                    run = OUT/'runs'/name/f'{budget}_{seed}_{method}'
                    run.mkdir(parents=True)
                    source = OUT/('baseline_source' if method == 'baseline' else 'candidate_source')
                    job = dict(**case, seed=seed, budget=budget, method=method, run=str(run), source=str(source),
                        result=str(run/'result.json'), row=str(run/'row.json'), threads=1,
                        source_manifest_sha256=sha(OUT/('baseline_manifest.json' if method == 'baseline' else 'candidate_manifest.json')),
                        hard_timeout=budget+30)
                    write(run/'job.json', job)
                    jobs.append(str(run/'job.json'))
    write(OUT/'input_manifest.json', {str(p.relative_to(OUT)):sha(p) for p in (OUT/'inputs').rglob('*') if p.is_file()})
    write(OUT/'preparation.json', dict(cases=cases, driver_sha256=sha(__file__), plan_sha256=sha(OUT/'plan.json'),
        jobs=24, source_differences=sorted(changed), all_original_task_projections=80,
        note='Frozen baseline and candidate differ only in registered owned files. No changes to original business inputs.'))
    write(OUT/'jobs.json', jobs)
    write(OUT/'checkpoint.json', dict(status='PREPARED', active_process=None, completed=0, total=24))
    print('Prepared 24 jobs, both original 40-task schedules physically projected', flush=True)


def worker(path):
    job = read(path)
    assert os.environ.get('PYTHONHASHSEED') == '0'
    sys.path[:] = [job['source']]+[p for p in sys.path if p not in ('', str(ROOT))]
    from models.routing.common.cycle.audit import audit_solution
    from models.routing.solvers.exact.single_batch_columns import problem_fingerprint
    from models.routing.solvers.exact import solver
    assert Path(solver.__file__).is_relative_to(Path(job['source']))
    method = solver.solve_exact
    if job['method'] == 'physical':
        from models.routing.solvers.exact.single_batch_physical_bounds import solve_exact_with_physical_bounds
        method = solve_exact_with_physical_bounds
    real_build = solver.build_model

    def logged_build(*args, **kwargs):
        model, context = real_build(*args, **kwargs)
        model.Params.OutputFlag = 1
        model.Params.LogToConsole = 0
        model.Params.LogFile = str(Path(job['run'])/'kernel.log')
        return model, context
    solver.build_model = logged_build
    begun = time.perf_counter()
    p = load_case(job['input']); initial = read(job['start'])
    assert sha(job['input']) == job['input_sha256'] and sha(job['start']) == job['start_sha256']
    assert problem_fingerprint(p) == job['original_fp']
    audit = audit_solution(p, initial)
    assert audit['valid'] and audit['periodic_replay']['valid']
    options = dict(seed=job['seed'], initial_result=initial)
    if job['method'] == 'physical':
        options['diagnostics_dir'] = str(Path(job['run'])/'sites')
    allocation = job['budget']-(time.perf_counter()-begun)
    assert allocation > 0
    result = method(p, time_limit=allocation, **options)
    elapsed = time.perf_counter()-begun
    final_audit = audit_solution(p, result)
    assert final_audit['valid'] and final_audit['periodic_replay']['valid']
    info = result['info']
    assert info['objective'] <= initial['info']['objective']+1e-6
    assert info['best_bound'] <= info['objective']+1e-5
    cert = info.get('physical_site_certificate', {})
    row = dict(case=job['case'], seed=job['seed'], budget=job['budget'], method=job['method'],
        status=info['status'], upper=info['objective'], lower=info['best_bound'], gap=info['mip_gap'],
        proven_optimal=info['proven_optimal'], runtime=elapsed, allocation=allocation,
        kernel_work=info.get('mip_work'), component_runtime=info.get('physical_stage_runtime', 0),
        component_bound=cert.get('bound', 0), component_work=sum(r['work'] for r in cert.get('sites', {}).values()),
        component_sites={s:r['bound'] for s,r in cert.get('sites', {}).items()},
        original_fp=job['original_fp'], start_sha256=job['start_sha256'], input_sha256=job['input_sha256'],
        source_manifest_sha256=job['source_manifest_sha256'], python_hash_seed=0,
        audit=final_audit, result_sha256=None)
    result.update(experiment_total_runtime=elapsed, experiment_allocation=allocation)
    assert not Path(job['result']).exists()
    write(job['result'], result)
    row['result_sha256'] = sha(job['result'])
    write(job['row'], row)


def run_all():
    assert sha(__file__) == read(OUT/'preparation.json')['driver_sha256']
    rows = []
    for path in read(OUT/'jobs.json'):
        job = read(path); run = Path(job['run'])
        if (run/'row.json').exists():
            rows.append(read(run/'row.json'))
            continue
        assert not (run/'started.json').exists(), 'Inspect unfinished run; never duplicate or retry automatically'
        state = load_record()
        write(run/'load_before.json', state)
        write(run/'started.json', dict(utc=state['utc'], driver_pid=os.getpid()))
        write(OUT/'checkpoint.json', dict(status='RUNNING', active_process=dict(pid=os.getpid(), job=job),
                                        completed=len(rows), total=24))
        begun = time.perf_counter()
        try:
            process = subprocess.run([sys.executable, str(Path(__file__).resolve()), '--worker', path],
                cwd=ROOT, capture_output=True, text=True, timeout=job['hard_timeout'],
                env={**os.environ, 'PYTHONHASHSEED': '0'})
            (run/'stdout_stderr.log').write_text(process.stdout+'\n'+process.stderr)
            if process.returncode:
                write(run/'failed.json', dict(returncode=process.returncode, job=job))
                raise RuntimeError('Failed job preserved; no automatic retry')
        except subprocess.TimeoutExpired as error:
            (run/'stdout_stderr.log').write_bytes((error.stdout or b'')+(error.stderr or b''))
            write(run/'failed.json', dict(reason='hard timeout', job=job))
            raise
        state = load_record(); state['process_wall'] = time.perf_counter()-begun
        write(run/'load_after.json', state)
        row = read(run/'row.json'); rows.append(row)
        write(OUT/'comparison.json', rows)
        message = (f"{len(rows)}/24 {row['case']} seed={row['seed']} budget={row['budget']} {row['method']}: "
                   f"UB={row['upper']:.6f}, LB={row['lower']:.6f}, gap={row['gap']:.6f}, physical={row['component_bound']:.6f}")
        print(message, flush=True)
        with (OUT/'progress.md').open('a') as f:
            f.write('\n'+message+'\n')
    write(OUT/'checkpoint.json', dict(status='COMPARISONS_COMPLETE_NEEDS_VERIFICATION', active_process=None,
                                    completed=len(rows), total=24))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--prepare', action='store_true'); parser.add_argument('--worker')
    args = parser.parse_args()
    if args.prepare:
        prepare()
    elif args.worker:
        worker(args.worker)
    else:
        try:
            run_all()
        except Exception as error:
            write(OUT/'checkpoint.json', dict(status='FAILED_RETAINED', active_process=None, reason=repr(error)))
            raise
