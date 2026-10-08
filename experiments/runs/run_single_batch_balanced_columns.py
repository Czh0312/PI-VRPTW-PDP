"""Frozen, serial M4 root comparisons. Never alter the stopped overnight run."""
from pathlib import Path
from datetime import datetime, timezone
import argparse
import hashlib
import json
import math
import os
import shutil
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / 'output/experiments/single_batch_balanced_columns_20261005'
OLD = ROOT / 'output/experiments/single_batch_exact_overnight_20261002/m4_price_balance01'
OWNED = ['models/routing/solvers/exact/single_batch_pickup_bound.py',
         'tests/routing/test_single_batch_balanced_columns.py']


def read(p):
    return json.loads(Path(p).read_text())


def write(p, value):
    Path(p).write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False)+'\n')


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def observed_load():
    names = ['alns_single_batch_overnight_20261001', 'm5_overnight_20261001',
             'model5_alns_overnight_20261001']
    states = {name: read(ROOT/'output/experiments'/name/'checkpoint.json') for name in names}
    return dict(utc=datetime.now(timezone.utc).isoformat(), loadavg=os.getloadavg(),
        cpu_count=os.cpu_count(), checkpoints=states,
        recorded_active={n: {k: v.get(k) for k in ('active_process', 'running', 'active_worker')
                            if v.get(k)} for n, v in states.items()},
        limitation='Process inventory denied by sandbox; recent Model6 activity has no checkpoint. No CPU exclusivity.')


def load_case(path):
    from tools.configuration.loader import load_routing_case
    from models.routing.common.cycle import load_instance
    config, fleet = load_routing_case(path)
    return load_instance(config, fleet)


def legacy_business_fingerprint(problem):
    """Verify the complete old payload, allowing only two new NULL metadata keys."""
    orders = []
    for order in problem.orders:
        values = dict(vars(order))
        for field in ('release_time', 'allowed_batch_ids'):
            assert values.pop(field, None) is None, 'Business metadata changed'
        orders.append(values)
    payload = dict(model=problem.model_number, depot=problem.depot, orders=orders,
        policy=vars(problem.operating_policy), fleet={k: getattr(problem.fleet, k) for k in (
            'e1_count', 'e2_per_sat_count', 'e1_weight_cap', 'e1_volume_cap', 'e2_weight_cap',
            'e2_volume_cap', 'container_weight', 'container_volume')},
        satellites=[(s, problem.service(s)) for s in problem.satellites],
        arcs=[(a, b, problem.travel(a, b), problem.distance(a, b))
              for a in problem.network.nodes for b in problem.network.nodes])
    raw = json.dumps(payload, sort_keys=True, default=lambda x: vars(x), separators=(',', ':')).encode()
    return hashlib.sha256(raw).hexdigest()


def prepare():
    sys.path.insert(0, str(ROOT))
    from models.routing.solvers.exact.single_batch_columns import problem_fingerprint
    from models.routing.solvers.exact.single_batch_pickup_bound import pickup_contexts, project_original_schedule
    from models.routing.common.cycle.audit import audit_solution
    assert '102 passed' in (OUT/'targeted_attempt2.log').read_text()
    assert not (OUT/'jobs.json').exists()
    # Baseline and candidate differ ONLY in explicitly owned files.
    shutil.copytree(OUT/'baseline_source', OUT/'candidate_source')
    for rel in OWNED:
        dest = OUT/'candidate_source'/rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT/rel, dest)
    shutil.copy2(__file__, OUT/'driver_frozen.py')
    candidate = {str(p.relative_to(OUT/'candidate_source')): sha(p)
                 for p in (OUT/'candidate_source').rglob('*') if p.is_file() and '__pycache__' not in str(p)}
    write(OUT/'candidate_manifest.json', candidate)
    baseline = read(OUT/'baseline_manifest.json')
    changes = [n for n in set(baseline)|set(candidate) if baseline.get(n) != candidate.get(n)]
    assert set(changes) == set(OWNED), changes
    old_jobs = [read(p) for p in read(OLD/'prices5/jobs.json')]
    sources = {}
    for job in old_jobs:
        sources.setdefault(job['case'], job)
    assert len(sources) == 2
    cases, projections, jobs = [], [], []
    for source_index, (name, origin) in enumerate(sources.items()):
        folder = OUT/'inputs'/name
        folder.mkdir(parents=True)
        cfg = read(origin['case_path'])
        shutil.copy2(origin['case_path'], folder/'original_case.json')
        origin_hashes = {}
        for key in ('data', 'orders', 'routing_config', 'operating_policy', 'dataset_metadata', 'pool_config'):
            old = Path(cfg[key]); dest = folder/(key+old.suffix)
            shutil.copy2(old, dest); assert sha(old) == sha(dest)
            origin_hashes[key] = dict(path=str(old), sha256=sha(old), copy=str(dest))
            cfg[key] = str(dest)
        write(folder/'case.json', cfg)
        shutil.copy2(origin['start_path'], folder/'initial_result.json')
        p = load_case(folder/'case.json'); start = read(folder/'initial_result.json')
        audit = audit_solution(p, start)
        assert audit['valid'] and audit['periodic_replay']['valid']
        assert legacy_business_fingerprint(p) == origin['problem_sha256']
        _, contexts, check = pickup_contexts(p)
        assert check['passed']
        cols = project_original_schedule(p, start, contexts)
        assert sum(len(c['tasks']) for _, c in cols) == 40
        projection = dict(case=name, problem_sha256=problem_fingerprint(p), audit=audit,
                          columns=cols, original_sources=origin_hashes)
        write(folder/'original_projection.json', projection); projections.append(projection)
        cases.append(dict(case=name, original_fp=problem_fingerprint(p), legacy_fp=origin['problem_sha256'],
                          input=str(folder/'case.json'),
                          start=str(folder/'initial_result.json'), start_sha256=sha(folder/'initial_result.json')))
        for si, seed in enumerate((42, 43, 44)):
            for bi, budget in enumerate((15, 30)):
                order = ('baseline', 'balanced') if (source_index+si+bi)%2 == 0 else ('balanced', 'baseline')
                for method in order:
                    run = OUT/'runs'/name/f'{budget}_{seed}_{method}'; run.mkdir(parents=True)
                    job = dict(**cases[-1], seed=seed, budget=budget, method=method,
                               source=str(OUT/('baseline_source' if method == 'baseline' else 'candidate_source')),
                               input_sha256=sha(folder/'case.json'), result=str(run/'result.json'),
                               row=str(run/'row.json'), run=str(run), threads=1,
                               pricing_time_limit=1.0, hard_timeout=budget+30,
                               source_manifest_sha256=sha(OUT/('baseline_manifest.json' if method=='baseline'
                                                              else 'candidate_manifest.json')))
                    write(run/'job.json', job); jobs.append(str(run/'job.json'))
    assert len(jobs) == 24
    write(OUT/'input_manifest.json', {str(p.relative_to(OUT)): sha(p)
        for p in (OUT/'inputs').rglob('*') if p.is_file()})
    write(OUT/'preparation.json', dict(cases=cases, jobs=24, original_large40task_projections=2,
        source_differences=changes, driver_sha256=sha(__file__), plan_sha256=sha(OUT/'plan.json'),
        logging='All master/pricer Gurobi logs enabled to separate files for both methods; overhead charged equally.',
        protocol='No before/after source substitution; every run uses its immutable source snapshot.'))
    write(OUT/'jobs.json', jobs)
    write(OUT/'checkpoint.json', dict(status='READY_VALIDATED', active_process=None,
        completed=0, total=24, next_action='Run registered serial root matrix, then independent verification'))
    print('Prepared 24 jobs; 2 original 40-task schedules audited/projected', flush=True)


def worker(job_path):
    job = read(job_path)
    sys.path[:] = [job['source']] + [s for s in sys.path if s not in ('', str(ROOT))]
    import models.routing.solvers.exact.single_batch_pickup_bound as bound
    from models.routing.solvers.exact.single_batch_columns import problem_fingerprint
    from models.routing.common.cycle.audit import audit_solution
    assert Path(bound.__file__).is_relative_to(Path(job['source']))
    run = Path(job['run'])
    real_pricer, real_master = bound.RoutePricer, bound.PickupCoverMaster
    def logged_pricer(problem, context, **options):
        obj = real_pricer(problem, context, **options)
        for i, (_, sub) in enumerate(obj.groups):
            sub.model.Params.OutputFlag = 1
            sub.model.Params.LogToConsole = 0
            sub.model.Params.LogFile = str(run/f"pricing_{context['satellite']}_{i}.log")
        return obj
    def logged_master(*args, **kwargs):
        obj = real_master(*args, **kwargs)
        obj.model.Params.OutputFlag = 1; obj.model.Params.LogToConsole = 0
        obj.model.Params.LogFile = str(run/'master.log')
        return obj
    bound.RoutePricer, bound.PickupCoverMaster = logged_pricer, logged_master
    begun = time.perf_counter()
    p = load_case(job['input']); start = read(job['start'])
    assert sha(job['input']) == job['input_sha256'] and sha(job['start']) == job['start_sha256']
    assert problem_fingerprint(p) == job['original_fp']
    audit = audit_solution(p, start)
    assert audit['valid'] and audit['periodic_replay']['valid']
    _, contexts, _ = bound.pickup_contexts(p)
    assert sum(len(c['tasks']) for _, c in bound.project_original_schedule(p, start, contexts)) == 40
    options = dict(seed=job['seed'], initial_result=start, pricing_time_limit=1.0)
    if job['method'] == 'balanced':
        options['balanced_columns'] = True
    remaining = job['budget'] - (time.perf_counter()-begun)
    assert remaining > 0
    result = bound.root_pickup_route_bound(p, time_limit=remaining, **options)
    elapsed = time.perf_counter()-begun
    assert not result['proven_optimal'] and not result['proven_infeasible']
    assert result['e2_bound'] <= audit['distances']['e2']+1e-5
    work = sum(r.get('work', 0) for h in result['history'] for r in h['pricing'].values())
    work += sum(r['pricing'].get('work', 0) for h in result.get('balanced_history', []) for r in h['sites'].values())
    result.update(total_runtime=elapsed, root_allocation=remaining, original_audit=audit,
                  original_start_sha256=job['start_sha256'], original_case_sha256=job['input_sha256'])
    row = dict(case=job['case'], seed=job['seed'], budget=job['budget'], method=job['method'],
        status=result['status'], e2_bound=result['e2_bound'], bound=result['bound'],
        complete=result['relaxation_complete'], runtime=elapsed, pricing_work=work,
        root_allocation=remaining, iterations=result['iterations'], columns=result.get('columns', 0),
        guided_runtime=result.get('balanced_runtime', 0),
        guided_added=sum(h['added'] for h in result.get('balanced_history', [])),
        guided_returned=sum(h['returned'] for h in result.get('balanced_history', [])),
        guided_original_negative=sum(h['original_negative'] for h in result.get('balanced_history', [])),
        best_raw_correction=max((h['corrected_e2_bound'] for h in result['history'] if h['phase']==2), default=None),
        original_upper=audit['objective'], original_fp=job['original_fp'],
        start_sha256=job['start_sha256'], source_manifest_sha256=job['source_manifest_sha256'],
        result_sha256=None, original_integer_proof=False)
    assert not Path(job['result']).exists()
    write(job['result'], result); row['result_sha256']=sha(job['result']); write(job['row'], row)


def run_all():
    preparation = read(OUT/'preparation.json')
    assert sha(__file__) == preparation['driver_sha256'], 'Driver changed after freezing'
    rows = []
    for i, path in enumerate(read(OUT/'jobs.json'), 1):
        job = read(path); run = Path(job['run'])
        if (run/'row.json').exists():
            rows.append(read(run/'row.json')); continue
        assert not (run/'failed.json').exists(), 'Retain failed job; no automatic retries'
        assert not (run/'started.json').exists(), 'Unfinished job needs explicit recovery, not duplicate launch'
        state = observed_load()
        assert not any(state['recorded_active'].values()), 'Other checkpoint records active work'
        write(run/'load_before.json', state)
        write(run/'started.json', dict(utc=state['utc'], driver_pid=os.getpid()))
        write(OUT/'checkpoint.json', dict(status='RUNNING', active_process=dict(pid=os.getpid(), job=job),
                                        completed=len(rows), total=24))
        begun = time.perf_counter()
        try:
            process = subprocess.run([sys.executable, str(Path(__file__).resolve()), '--worker', path],
                cwd=ROOT, capture_output=True, text=True, timeout=job['hard_timeout'])
            (run/'stdout_stderr.log').write_text(process.stdout+'\n'+process.stderr)
            if process.returncode:
                write(run/'failed.json', dict(returncode=process.returncode, job=job))
                raise RuntimeError(f'Failed job {i}; retained, no retry')
        except subprocess.TimeoutExpired as error:
            (run/'stdout_stderr.log').write_bytes((error.stdout or b'')+(error.stderr or b''))
            write(run/'failed.json', dict(reason='hard timeout', job=job)); raise
        state = observed_load(); state['process_wall'] = time.perf_counter()-begun
        write(run/'load_after.json', state)
        row = read(run/'row.json'); rows.append(row); write(OUT/'comparison.json', rows)
        print(i, '/24', row['case'], row['budget'], row['seed'], row['method'],
              'E2', round(row['e2_bound'], 6), 'guided', row['guided_added'], 'status', row['status'], flush=True)
        with (OUT/'progress.md').open('a') as f:
            f.write(f"\n{i}/24 {row['case']} budget={row['budget']} seed={row['seed']} {row['method']}: "
                    f"E2={row['e2_bound']}, guided_added={row['guided_added']}, {row['status']}\n")
    write(OUT/'checkpoint.json', dict(status='COMPARISONS_COMPLETE_NEEDS_VERIFICATION',
                                    active_process=None, completed=len(rows), total=24))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--prepare', action='store_true'); parser.add_argument('--worker')
    args = parser.parse_args()
    if args.worker:
        worker(args.worker)
    elif args.prepare:
        prepare()
    else:
        try:
            run_all()
        except Exception as error:
            write(OUT/'checkpoint.json', dict(status='FAILED_RETAINED', active_process=None,
                reason=repr(error), next_action='Inspect retained failure; no automatic solver retry'))
            raise
