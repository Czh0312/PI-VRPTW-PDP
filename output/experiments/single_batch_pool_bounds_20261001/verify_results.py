"""Recheck frozen data, shared starts, every returned schedule and negatives."""
from pathlib import Path
import hashlib
import json
import math
import sys

ROOT = Path(__file__).resolve().parents[3]
OUT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
from configuration.loader import load_routing_case
from models.routing.common.cycle import load_instance
from models.routing.common.cycle.audit import audit_solution


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)+'\n')


def verify_frozen(batch):
    manifest = json.loads((batch/'manifest.json').read_text())
    for item in manifest['input_sha256'].values():
        assert sha(batch/item['snapshot']) == item['sha256']
    for path, expected in manifest['source_sha256'].items():
        assert sha(batch/'source_snapshot'/path) == expected
    return manifest


def audited(job, result):
    config, fleet = load_routing_case(job['case_path'])
    p = load_instance(config, fleet)
    initial = json.loads(Path(job['start_path']).read_text())
    start = audit_solution(p, initial)
    audit = audit_solution(p, result)
    assert start['valid'] and audit['valid'] and audit['periodic_replay']['valid']
    assert audit['objective'] <= start['objective']+1e-6
    assert math.isclose(audit['objective'], result['info']['objective'], rel_tol=0, abs_tol=1e-6)
    return audit


def main():
    files, snapshots, records, pairs = {}, {}, [], []
    for name in ('final_long', 'aggregate_long', 'aggregate_short', 'aggregate_holdout'):
        batch = OUT/name
        if not batch.exists():
            continue
        manifest = verify_frozen(batch)
        snapshots[name] = manifest['source_sha256']
        rows = json.loads((batch/'comparison.json').read_text())
        assert len(rows) == manifest['job_count']
        grouped = {}
        for row in rows:
            run = batch/row['case']/f'{row["method"]}_{row["seed"]}'
            job = json.loads((run/'job.json').read_text())
            result = json.loads((run/'result.json').read_text())
            audit = audited(job, result)
            info = result['info']
            assert info['best_bound'] <= audit['objective']+1e-5
            assert not info['proven_infeasible'] and not info['route_pool_primal']['global_proof']
            assert '_route_pool' not in info['route_bound_certificate']
            assert info['route_pool_primal']['slot_bounds']['enabled'] == (row['method'] in ('columns_tight','columns_timing'))
            assert info['route_pool_primal']['slot_bounds'].get('aggregate_time',False) == (row['method']=='columns_timing')
            record = {**row, 'batch':name, 'start_sha256':sha(Path(job['start_path'])), 'audited':True, 'replay':True}
            records.append(record)
            grouped.setdefault((row['case'], row['seed']), {})[row['method']] = record
            for filename in ('job.json','row.json','result.json','solver.log'):
                files[str((run/filename).relative_to(ROOT))] = sha(run/filename)
            files[str(Path(job['start_path']).relative_to(ROOT))] = sha(Path(job['start_path']))
        for (case, seed), group in grouped.items():
            old, new = group['columns_compact'], group['columns_tight' if name=='final_long' else 'columns_timing']
            assert old['start_sha256'] == new['start_sha256']
            pairs.append(dict(batch=name, case=case, seed=seed,
                objective_before=old['objective'], objective_after=new['objective'],
                bound_before=old['bound'], bound_after=new['bound'], gap_before=old['gap'], gap_after=new['gap'],
                distance_improved=new['objective']<old['objective']-1e-6,
                distance_regressed=new['objective']>old['objective']+1e-6))
    all_fixed_rows = []
    for fixed_name, script in (('fixed_pool','run_fixed_pool.py'), ('aggregate_fixed_pool','run_aggregate_pool.py')):
        fixed = OUT/fixed_name
        manifest = verify_frozen(fixed)
        snapshots[fixed_name] = manifest['source_sha256']
        assert sha(OUT/script) == manifest['script_sha256']
        assert sha(Path(manifest['start_path'])) == manifest['start_sha256']
        for pool in manifest['pools'].values():
            assert sha(Path(pool['path'])) == pool['sha256'] == pool['origin_sha256']
        fixed_rows = json.loads((fixed/'comparison.json').read_text())
        assert len(fixed_rows) == 4
        for row in fixed_rows:
            run = fixed/f'{"tight" if row["tight"] else "loose"}_{row["seed"]}'
            job = json.loads((run/'job.json').read_text())
            assert sha(Path(job['pool_path'])) == job['pool_sha256'] == row['pool_sha256']
            audit = audited(job, json.loads((run/'result.json').read_text()))
            assert math.isclose(audit['objective'], row['objective'], rel_tol=0, abs_tol=1e-6)
            assert row['stats']['global_proof'] is False and row['original_global_proof'] is False
            assert row['stats']['slot_bounds'].get('aggregate_time',False) == (fixed_name=='aggregate_fixed_pool' and row['tight'])
            for filename in ('job.json','row.json','result.json','solver.log'):
                files[str((run/filename).relative_to(ROOT))] = sha(run/filename)
            all_fixed_rows.append(dict(batch=fixed_name,**row))
        for pool in manifest['pools'].values():
            files[str(Path(pool['path']).relative_to(ROOT))] = pool['sha256']
    same_runtime = ('models/routing/solvers/exact/single_batch_columns.py',
                    'models/routing/solvers/exact/single_batch_route_pool.py',
                    'models/routing/solvers/exact/single_batch_pool_master.py',
                    'experiments/runs/run_single_batch_columns.py')
    for path in same_runtime:
        final = {name:snapshot for name,snapshot in snapshots.items() if name not in ('final_long','fixed_pool')}
        assert len({snapshot[path] for snapshot in final.values()}) == 1
        assert sha(ROOT/path) == snapshots['aggregate_fixed_pool'][path]
        assert snapshots['final_long'][path] == snapshots['fixed_pool'][path]
    summary = dict(integer_runs=len(records), fixed_pool_runs=len(all_fixed_rows),
        audits_passed=len(records)+len(all_fixed_rows), replays_passed=len(records)+len(all_fixed_rows),
        global_optimal_proofs=sum(r['proven_optimal'] for r in records),
        records=records, comparisons=pairs, fixed_pool_records=all_fixed_rows,
        default='unchanged; pool_tight / pool_aggregate_time explicit opt-ins; M1/M2 only',
        timing_scope='all formal stages share total wall clock; fixed pool excludes cached CG generation',
        source_snapshot_differences={name:[p for p,v in snap.items() if v != snapshots['fixed_pool'].get(p)]
                                     for name,snap in snapshots.items()},
        validation=json.loads((OUT/'validation.json').read_text()) if (OUT/'validation.json').exists() else None)
    write(OUT/'summary.json', summary)
    write(OUT/'result_manifest.json', files)
    print(json.dumps({k:summary[k] for k in ('integer_runs','fixed_pool_runs','audits_passed','replays_passed','global_optimal_proofs','source_snapshot_differences')},ensure_ascii=False))


if __name__ == '__main__':
    main()
