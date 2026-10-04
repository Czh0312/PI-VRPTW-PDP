"""Final same-catalog comparison: retain count row and add first-route vehicle ordering.

The common CG catalog was generated in a previous experiment. Its generation
is excluded from this supplementary ten-second pool-only comparison.
"""
from pathlib import Path
import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[3]
OUT = Path(__file__).resolve().parent
OLD = ROOT/'output/experiments/single_batch_pool_bounds_20261001/aggregate_fixed_pool'


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)+'\n')


def worker(job_path):
    job = json.loads(Path(job_path).read_text())
    sys.path.insert(0, job['snapshot'])
    from configuration.loader import load_routing_case
    from models.routing.common.cycle import load_instance
    from models.routing.common.cycle.audit import audit_solution
    from models.routing.solvers.exact.single_batch_route_pool import route_pool_incumbent
    config, fleet = load_routing_case(job['case_path'])
    p = load_instance(config, fleet)
    initial = json.loads(Path(job['start_path']).read_text())
    assert audit_solution(p, initial)['valid']
    assert sha(Path(job['pool_path'])) == job['pool_sha256']
    pool = json.loads(Path(job['pool_path']).read_text())
    for ctx in pool['contexts'].values():
        for node in ctx['nodes']:
            node['order'] = p.order_map[node.pop('order_id')]
            node['window'] = tuple(node['window'])
    for tour in pool['tours']:
        tour['sites'] = tuple(tour['sites'])
        tour['permitted'] = set(map(tuple, tour['permitted']))
    for _, col in pool['columns']:
        col['tasks'] = tuple(col['tasks'])
    begun = time.perf_counter()
    result, stats = route_pool_incumbent(p, pool, initial_result=initial,
        deadline=begun+job['budget'], seed=job['seed'], compact=True, vehicle_order=job['ordered'])
    runtime = time.perf_counter()-begun
    result = result or initial
    audit = audit_solution(p, result)
    assert audit['valid'] and audit['periodic_replay']['valid'] and not stats['global_proof']
    write(Path(job['result_path']), result)
    write(Path(job['row_path']), dict(seed=job['seed'], ordered=job['ordered'],
        stage='pool_only', budget=job['budget'], runtime=runtime,
        objective=audit['objective'], audited=True, replay=True,
        original_global_proof=False, pool_sha256=job['pool_sha256'], stats=stats))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--worker')
    args = parser.parse_args()
    if args.worker:
        return worker(args.worker)
    sys.path.insert(0, str(ROOT))
    from experiments.runs.run_multibatch_cycle import INPUT_KEYS, freeze_inputs, source_snapshot
    from configuration.loader import load_routing_case, resolve_project_path
    out = OUT/'final_fixed_pool'
    assert not out.exists(), 'Never overwrite or retry a batch'
    out.mkdir()
    old_case = OLD/'case.json'
    config, _ = load_routing_case(old_case)
    sources = {resolve_project_path(config[k]).resolve() for k in INPUT_KEYS if isinstance(config.get(k), str)}
    variants = [dict(config=config, _source_hashes={str(p):sha(p) for p in sources})]
    inputs = freeze_inputs(variants, out)
    source = source_snapshot(out)
    case, start = out/'case.json', out/'start.json'
    write(case, variants[0]['config'])
    shutil.copy2(old_case.with_name('start.json'), start)
    pools = {}
    for seed in (42,43):
        origin = OLD/f'pool_{seed}.json'
        path = out/origin.name
        shutil.copy2(origin, path)
        pools[str(seed)] = dict(path=str(path), sha256=sha(path), origin=str(origin), origin_sha256=sha(origin))
    write(out/'manifest.json', dict(input_sha256=inputs, source_sha256=source,
        source_snapshot=str(out/'source_snapshot'), case_path=str(case), start_path=str(start),
        start_sha256=sha(start), script_sha256=sha(Path(__file__)), pools=pools,
        seeds=[42,43], pool_budget=10, compact=True, canonical=False, aggregate_time=False, tight=False, vehicle_order='same as ordered flag', threads=1,
        execution='sequential isolated processes; alternate order; no retries',
        scope='supplementary pool-only; cached CG generation excluded; no original integer proof'))
    rows = []
    for seed in (42,43):
        for ordered in ([False,True] if seed == 42 else [True,False]):
            label = f'{"ordered" if ordered else "counts"}_{seed}'
            run = out/label
            run.mkdir()
            job = dict(seed=seed, ordered=ordered, budget=10, snapshot=str(out/'source_snapshot'),
                case_path=str(case), start_path=str(start), pool_path=pools[str(seed)]['path'],
                pool_sha256=pools[str(seed)]['sha256'], result_path=str(run/'result.json'), row_path=str(run/'row.json'))
            job_path = run/'job.json'
            write(job_path, job)
            proc = subprocess.run([sys.executable, str(Path(__file__)), '--worker', str(job_path)],
                cwd=out/'source_snapshot', capture_output=True, text=True, timeout=40)
            (run/'solver.log').write_text(proc.stdout+'\n'+proc.stderr)
            if proc.returncode:
                write(out/'failure.json', dict(job=job, returncode=proc.returncode, stderr=proc.stderr))
                raise RuntimeError('Worker failed; retained, no retry')
            row = json.loads((run/'row.json').read_text())
            rows.append(row)
            write(out/'comparison.json', rows)
            print(label, row['objective'], row['runtime'], row['stats'].get('pool_status'), flush=True)


if __name__ == '__main__':
    main()
