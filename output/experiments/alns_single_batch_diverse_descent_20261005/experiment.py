"""Serial, resumable-evidence driver; no automatic retry of a solver run."""
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
import csv
import hashlib
import json
import os
import statistics
import subprocess
import sys
import time

OUT = Path(__file__).resolve().parent

def write(path, obj):
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + '\n')

def now():
    return datetime.now(ZoneInfo('Asia/Shanghai')).isoformat()

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def verify():
    for filename in ('registration_hashes.json', 'execution_hashes.json'):
        for rel, digest in json.loads((OUT / filename).read_text()).items():
            assert sha(OUT / rel) == digest, rel
    for source in ('before_source', 'after_source', 'disabled_source'):
        for rel, digest in json.loads((OUT / (source + '_manifest.json')).read_text()).items():
            assert sha(OUT / source / rel) == digest, (source, rel)
    for rel, digest in json.loads((OUT / 'input_hashes.json').read_text()).items():
        assert sha(OUT / 'benchmark_inputs' / rel) == digest, rel

def worker(source, job, dest):
    sys.path.insert(0, str(OUT / source))
    from configuration.loader import load_routing_case
    from models.routing.common.cycle import load_instance
    from models.routing.common.cycle.audit import audit_solution
    from models.routing.service import solve_routing
    from models.routing.solvers.alns import single_batch
    assert Path(single_batch.__file__).resolve().is_relative_to(OUT / source)
    config, fleet = load_routing_case(job['case'])
    problem = load_instance({**config, 'model': job['model']}, fleet)
    cpu, wall = time.process_time(), time.perf_counter()
    result = solve_routing(model=job['model'], method='alns', problem=problem,
                           alns_time=job['budget'], alns_iter=job['iterations'], seed=job['seed'])
    cpu, wall = time.process_time() - cpu, time.perf_counter() - wall
    audit = audit_solution(problem, result)
    write(dest, dict(job=job, result=result, independent_audit=audit,
                     cpu=cpu, solve_wall_seconds=wall, source_module=single_batch.__file__,
                     customers=len({o.customer for o in problem.orders})))

def run(phase):
    verify()
    manifest = json.loads((OUT / (phase + '_manifest.json')).read_text())
    jobs = manifest['jobs']
    sources = ['before_source', 'disabled_source' if phase == 'verification' else 'after_source']
    output = OUT / phase
    output.mkdir()
    lock = OUT / 'running.lock'
    handle = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    os.write(handle, str(os.getpid()).encode())
    os.close(handle)
    rows = []
    try:
        for index, job in enumerate(jobs):
            for source in (sources if index % 2 == 0 else sources[::-1]):
                name = '%03d_%s' % (index, source)
                dest = output / (name + '.json')
                row = dict(job, index=index, source=source, started=now(), load_before=os.getloadavg())
                write(OUT / 'checkpoint.json', dict(status='running', phase=phase, index=index,
                      source=source, pid=os.getpid(), finished_runs=len(rows), time=now()))
                command = [sys.executable, str(Path(__file__).resolve()), 'worker', source,
                           json.dumps(job), str(dest)]
                started = time.perf_counter()
                try:
                    completed = subprocess.run(command, cwd=OUT / source, capture_output=True,
                        text=True, timeout=job['budget'] + 30,
                        env={**os.environ, 'PYTHONHASHSEED': '0', 'PYTHONDONTWRITEBYTECODE': '1'})
                    (output / (name + '.log')).write_text(completed.stdout + completed.stderr)
                    row['returncode'] = completed.returncode
                    if completed.returncode:
                        row.update(valid=False, error=completed.stderr[-2000:])
                    else:
                        raw = json.loads(dest.read_text())
                        info, audit = raw['result']['info'], raw['independent_audit']
                        row.update(valid=audit['valid'], periodic=audit.get('periodic_replay', {}).get('valid'),
                            objective=info.get('objective'), iterations=info.get('iterations'),
                            cpu=raw['cpu'], wall=raw['solve_wall_seconds'],
                            diversity_enumerations=info.get('descent_diversity_enumerations', 0),
                            descent_improvements=info.get('operator_improvements', {}).get('descent', 0),
                            descent_noops=info.get('operator_noops', {}).get('descent', 0),
                            late_pickup_vetoes=info.get('late_pickup_vetoes'),
                            source_module=raw['source_module'])
                except subprocess.TimeoutExpired as exc:
                    row.update(valid=False, error='OUTER_TIMEOUT')
                    captured = (exc.stdout or b'') + (exc.stderr or b'')
                    (output / (name + '.log')).write_bytes(captured)
                row.update(process_wall=time.perf_counter() - started, finished=now(), load_after=os.getloadavg())
                rows.append(row)
                write(output / 'summary.json', rows)
                print(index, source, job['model'], job['size'], job['budget'], job['seed'],
                      row.get('objective'), row.get('valid'), flush=True)
        write(OUT / 'checkpoint.json', dict(status='completed', phase=phase, finished_runs=len(rows),
                                           active_process=None, time=now()))
    finally:
        lock.unlink()
    verify()

def preflight():
    sys.path.insert(0, str(OUT / 'before_source'))
    from configuration.loader import load_routing_case
    from models.routing.common.cycle import load_instance
    from models.routing.common.cycle.precheck import check_necessary_conditions, travel_lower_bounds
    rows = []
    jobs = json.loads((OUT / 'pilot_manifest.json').read_text())['jobs']
    seen = set()
    for job in jobs:
        key = job['family'], job['model']
        if key in seen:
            continue
        seen.add(key)
        config, fleet = load_routing_case(job['case'])
        p = load_instance({**config, 'model': job['model']}, fleet)
        paths = {k: v for k, v in config.items() if k in ('data', 'orders', 'routing_config',
                  'pool_config', 'container_pool_config', 'algorithm_config', 'dataset_metadata') and isinstance(v, str)}
        closed = all(Path(v).is_file() and Path(v).resolve().is_relative_to(OUT) for v in paths.values())
        count = len({o.customer for o in p.orders})
        pre = check_necessary_conditions(p, travel_lower_bounds(p))
        rows.append(dict(family=key[0], model=key[1], size=count, paths=paths, precheck=pre,
                         valid=closed and count == job['size'] and pre['passed']))
    write(OUT / 'input_preflight.json', rows)
    assert all(row['valid'] for row in rows)
    print('Preflight:', len(rows), 'passed')

def compare_fixed():
    rows = json.loads((OUT / 'verification/summary.json').read_text())
    checks = []
    for index in range(8):
        by_source = {r['source']: r for r in rows if r['index'] == index}
        assert set(by_source) == {'before_source', 'disabled_source'}
        results = [json.loads((OUT / 'verification' / ('%03d_%s.json' % (index, source))).read_text())
                   for source in ('before_source', 'disabled_source')]
        a, b = (r['result'] for r in results)
        equal = all(a.get(key) == b.get(key) for key in ('e1_trips', 'e2_trips', 'assignment'))
        equal &= all(a['info'].get(key) == b['info'].get(key) for key in ('objective', 'iterations',
                     'operator_attempts', 'operator_improvements', 'operator_noops', 'late_pickup_checks', 'late_pickup_vetoes'))
        valid = all(r['valid'] and r['periodic'] and r['iterations'] == 500 for r in by_source.values())
        checks.append(dict(index=index, job=results[0]['job'], equal=equal, valid=valid))
    write(OUT / 'disabled_equivalence.json', checks)
    assert all(r['equal'] and r['valid'] for r in checks)
    print('Disabled exact equivalence:', len(checks), 'passed')

def fresh(phase):
    sys.path.insert(0, str(OUT / 'after_source'))
    from configuration.loader import load_routing_case
    from models.routing.common.cycle import load_instance
    from models.routing.common.cycle.assignment import assigned_problem, lift_result
    from models.routing.common.cycle.audit import audit_solution
    from models.routing.solvers.alns.evaluator import evaluate
    from models.routing.solvers.alns.neighborhoods import state_from_result
    output = OUT / (phase + '_fresh')
    output.mkdir()
    rows = json.loads((OUT / phase / 'summary.json').read_text())
    checks = []
    for row in rows:
        name = '%03d_%s.json' % (row['index'], row['source'])
        path = OUT / phase / name
        record = dict(name=name, valid=False, equal=False)
        if path.exists() and row.get('valid') and row.get('objective') is not None:
            raw = json.loads(path.read_text())
            config, fleet = load_routing_case(row['case'])
            base = load_instance({**config, 'model': row['model']}, fleet)
            problem = assigned_problem(base, raw['result']['assignment']) if base.free_assignment else base
            _, decoded, issues = evaluate(problem, state_from_result(problem, raw['result']))
            fixed_audit = audit_solution(problem, decoded)
            if base.free_assignment:
                decoded = lift_result(base, decoded, raw['result']['assignment'])
            audit = audit_solution(base, decoded)
            record.update(valid=not issues and fixed_audit['valid'] and fixed_audit['periodic_replay']['valid']
                                and audit['valid'] and audit['periodic_replay']['valid'],
                          equal=abs(decoded['info']['objective'] - row['objective']) <= 1e-8,
                          issues=issues, original=row['objective'], decoded=decoded['info']['objective'])
            write(output / name, dict(check=record, decoded=decoded, audit=audit, fixed_audit=fixed_audit))
        checks.append(record)
    write(OUT / (phase + '_fresh_decode.json'), checks)
    print('Fresh decode:', sum(r['valid'] and r['equal'] for r in checks), '/', len(checks))

def summarize(phase):
    manifest = json.loads((OUT / (phase + '_manifest.json')).read_text())
    jobs, short, long = manifest['jobs'], manifest['short_budget'], manifest['long_budget']
    rows = json.loads((OUT / phase / 'summary.json').read_text())
    decoded = json.loads((OUT / (phase + '_fresh_decode.json')).read_text())
    fresh_checks = {r['name']: r for r in decoded}
    assert len(rows) == len(jobs) * 2 == 16
    pairs = []
    for index, job in enumerate(jobs):
        versions = {r['source']: r for r in rows if r['index'] == index}
        a, b = versions['before_source'], versions['after_source']
        valid = all(r.get('valid') and r.get('periodic') and r.get('objective') is not None for r in (a, b))
        valid &= all(fresh_checks['%03d_%s.json' % (index, s)]['valid'] and
                     fresh_checks['%03d_%s.json' % (index, s)]['equal'] for s in versions)
        pairs.append(dict(index=index, model=job['model'], family=job['family'], size=job['size'],
                          seed=job['seed'], budget=job['budget'], valid=valid,
                          before=a.get('objective'), after=b.get('objective'),
                          change_percent=100 * (b['objective'] / a['objective'] - 1) if valid else None,
                          diversity_enumerations=b.get('diversity_enumerations', 0), descent_improvements=b.get('descent_improvements', 0)))
    summary = []
    for model in (3,):
        group = [p for p in pairs if p['model'] == model]
        for label, subset in [('short', [p for p in group if p['budget'] == short]),
                              ('long', [p for p in group if p['budget'] == long]),
                              ('short50', [p for p in group if p['budget'] == short and p['size'] == 50]),
                              ('short100', [p for p in group if p['budget'] == short and p['size'] == 100])]:
            ds = [p['change_percent'] for p in subset if p['valid']]
            valid = len(ds) == len(subset)
            mean, maximum = (statistics.mean(ds), max(ds)) if valid else (None, None)
            wins = sum(d < -1e-7 for d in ds)
            required = {'short': 3, 'long': 1, 'short50': 0, 'short100': 0}[label]
            triggered = any(p['diversity_enumerations'] for p in subset)
            eligible = (valid and mean <= 0 and maximum <= 5 and wins >= required
                        and (triggered or label not in ('short', 'long')))
            summary.append(dict(model=model, stratum=label, mean=mean, worst=maximum,
                                wins=wins, pairs=len(subset), triggered=triggered, eligible=eligible))
    with (OUT / (phase + '_pairs.csv')).open('w') as f:
        writer = csv.DictWriter(f, fieldnames=list(pairs[0]))
        writer.writeheader()
        writer.writerows(pairs)
    result = dict(phase=phase, eligible=all(r['eligible'] for r in summary),
                  valid_pairs=sum(p['valid'] for p in pairs), summary=summary, pairs=pairs)
    write(OUT / (phase + '_comparison.json'), result)
    print(json.dumps({k: v for k, v in result.items() if k != 'pairs'}, indent=2))

if __name__ == '__main__':
    command = sys.argv[1]
    if command == 'worker':
        worker(sys.argv[2], json.loads(sys.argv[3]), Path(sys.argv[4]))
    elif command == 'run':
        run(sys.argv[2])
    elif command == 'preflight':
        preflight()
    elif command == 'compare-fixed':
        compare_fixed()
    elif command == 'fresh':
        fresh(sys.argv[2])
    elif command == 'summarize':
        summarize(sys.argv[2])
