"""Supplementary same-pool diagnosis; generation is excluded from pool budget.

Uses final_long's immutable runtime and inputs. Root pool is generated ONCE
per seed, saved as JSON, hashed, and identically replayed by both formulations.
No restricted-pool bound or optimality claim is exported to the original model.
"""
from pathlib import Path
import argparse
import hashlib
import json
import subprocess
import sys
import time

OUT = Path(__file__).resolve().parent
FROZEN = OUT/'aggregate_long'
SNAPSHOT = FROZEN/'source_snapshot'
sys.path.insert(0, str(SNAPSHOT))
from configuration.loader import load_routing_case
from models.routing.common.cycle import load_instance
from models.routing.common.cycle.audit import audit_solution


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2)+'\n')


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def worker(job_path):
    job = json.loads(Path(job_path).read_text())
    config, fleet = load_routing_case(job['case_path'])
    problem = load_instance(config, fleet)
    initial = json.loads(Path(job['start_path']).read_text())
    assert audit_solution(problem, initial)['valid']
    begun = time.perf_counter()
    if job['stage'] == 'root':
        from models.routing.solvers.exact.single_batch_columns import root_route_bound
        cert = root_route_bound(problem, time_limit=job['budget'], seed=job['seed'],
            initial_result=initial, pricing_method='mip', mip_strategy='pool', dual_cuts=True, _capture_pool=True)
        pool = cert.pop('_route_pool')
        # All identities and directed raw resources retained; no executable pickle.
        contexts = {s: {**ctx, 'nodes': [{**{k:v for k,v in n.items() if k != 'order'},
            'order_id': n['order'].id} for n in ctx['nodes']]} for s, ctx in pool['contexts'].items()}
        packed = {**pool, 'contexts': contexts,
                  'tours': [{**t, 'permitted': sorted(t['permitted'])} for t in pool['tours']]}
        write(Path(job['pool_path']), packed)
        write(Path(job['result_path']), cert)
        row = dict(stage='root', seed=job['seed'], runtime=cert['runtime'], columns=cert['columns'],
                   bound=cert['bound'], complete=cert['relaxation_complete'], pool_sha256=sha(Path(job['pool_path'])))
    else:
        from models.routing.solvers.exact.single_batch_route_pool import route_pool_incumbent
        assert sha(Path(job['pool_path'])) == job['pool_sha256']
        pool = json.loads(Path(job['pool_path']).read_text())
        for context in pool['contexts'].values():
            for node in context['nodes']:
                node['order'] = problem.order_map[node.pop('order_id')]
                node['window'] = tuple(node['window'])
        for tour in pool['tours']:
            tour['sites'] = tuple(tour['sites'])
            tour['permitted'] = set(map(tuple, tour['permitted']))
        for site, column in pool['columns']:
            column['tasks'] = tuple(column['tasks'])
        candidate, stats = route_pool_incumbent(problem, pool, initial_result=initial,
            deadline=time.perf_counter()+job['budget'], seed=job['seed'], compact=job['compact'])
        result = candidate or initial
        audited = audit_solution(problem, result)
        assert audited['valid'] and audited['periodic_replay']['valid']
        assert not stats['global_proof']
        write(Path(job['result_path']), result)
        row = dict(stage='pool', seed=job['seed'], compact=job['compact'],
            budget=job['budget'], runtime=time.perf_counter()-begun,
            objective=audited['objective'], audited=True, replay=True,
            original_global_proof=False, pool_sha256=job['pool_sha256'], stats=stats)
    write(Path(job['row_path']), row)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--worker')
    args = parser.parse_args()
    if args.worker:
        return worker(args.worker)
    out = OUT/'fixed_pool'
    assert not out.exists(), 'Never overwrite or retry an existing batch'
    out.mkdir()
    case = next(FROZEN.glob('*/case.json'))
    start = case.with_name('start.json')
    manifest = json.loads((FROZEN/'manifest.json').read_text())
    for key, expected in manifest['source_sha256'].items():
        assert sha(SNAPSHOT/key) == expected
    for item in manifest['input_sha256'].values():
        assert sha(FROZEN/item['snapshot']) == item['sha256']
    write(out/'manifest.json', dict(source_snapshot=str(SNAPSHOT),
        source_sha256=manifest['source_sha256'], input_sha256=manifest['input_sha256'],
        case_path=str(case), start_path=str(start), start_sha256=sha(start),
        script_sha256=sha(Path(__file__)), seeds=[42,43], root_budget=30, pool_budget=10,
        execution='sequential isolated processes; one shared pool per seed; no retries',
        scope='supplementary pool-only; common CG generation excluded from 10-second pool budget; no original integer proof'))
    rows = []
    for seed in (42,43):
        common = dict(seed=seed, case_path=str(case), start_path=str(start), pool_path=str(out/f'pool_{seed}.json'))
        stages = [('root', None)]+[('pool', c) for c in ([False, True] if seed == 42 else [True, False])]
        for stage, compact in stages:
            label = f'root_{seed}' if stage == 'root' else f'{"compact" if compact else "legacy"}_{seed}'
            run = out/label
            run.mkdir()
            job = dict(**common, stage=stage, budget=30 if stage=='root' else 10, compact=compact,
                       result_path=str(run/'result.json'), row_path=str(run/'row.json'))
            if stage == 'pool':
                job['pool_sha256'] = sha(Path(common['pool_path']))
            job_path = run/'job.json'
            write(job_path, job)
            proc = subprocess.run([sys.executable, str(Path(__file__)), '--worker', str(job_path)],
                cwd=SNAPSHOT, capture_output=True, text=True, timeout=job['budget']+30)
            (run/'solver.log').write_text(proc.stdout+'\n'+proc.stderr)
            if proc.returncode:
                write(out/'failure.json', dict(job=job, returncode=proc.returncode, stderr=proc.stderr))
                raise RuntimeError('Fixed-pool worker failed; artifacts retained, no retry')
            row = json.loads((run/'row.json').read_text())
            rows.append(row)
            write(out/'comparison.json', rows)
            print(label, row.get('objective'), row.get('runtime'),
                  row.get('stats', {}).get('pool_status'), flush=True)


if __name__ == '__main__':
    main()
