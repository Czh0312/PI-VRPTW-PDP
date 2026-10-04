"""Frozen-hash checks and independent re-audit, excluding root-only diagnostics."""
from pathlib import Path
import hashlib
import json
import math
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from configuration.loader import load_routing_case
from models.routing.common.cycle import load_instance
from models.routing.common.cycle.audit import audit_solution


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify():
    out = Path(__file__).resolve().parent
    records, pairs, files, snapshots, diagnostics = [], [], {}, {}, []
    for name in ('root_pilot', 'final_short', 'final_long', 'reuse_long'):
        batch = out/name
        manifest = json.loads((batch/'manifest.json').read_text())
        for item in manifest['input_sha256'].values():
            assert sha(batch/item['snapshot']) == item['sha256']
        for key, expected in manifest['source_sha256'].items():
            assert sha(batch/'source_snapshot'/key) == expected, key
        snapshots[name] = manifest['source_sha256']
        rows = json.loads((batch/'comparison.json').read_text())
        assert len(rows) == manifest['job_count']
        grouped = {}
        for row in rows:
            run = batch/row['case']/f'{row["method"]}_{row["seed"]}'
            job = json.loads((run/'job.json').read_text())
            result = json.loads((run/'result.json').read_text())
            config, fleet = load_routing_case(job['case_path'])
            problem = load_instance(config, fleet)
            initial = json.loads(Path(job['start_path']).read_text())
            assert audit_solution(problem, initial)['valid']
            for filename in ('job.json', 'row.json', 'result.json', 'solver.log'):
                files[str((run/filename).relative_to(ROOT))] = sha(run/filename)
            files[str(Path(job['start_path']).relative_to(ROOT))] = sha(Path(job['start_path']))
            if row.get('root_only'):
                assert not result['proven_optimal'] and result['relaxation_complete']
                diagnostics.append(dict(**row, restricted_objective=result['restricted_objective']))
                continue
            audited = audit_solution(problem, result)
            assert audited['valid'] and audited['periodic_replay']['valid'], audited
            info = result['info']
            assert math.isclose(info['objective'], audited['objective'], abs_tol=1e-6)
            assert info['best_bound'] <= audited['objective']+1e-5
            assert not info['proven_infeasible']
            cert = info['route_bound_certificate']
            priced = [r for h in cert['history'] for r in h.get('pricing', {}).values()]
            record = dict(batch=name, case=row['case'], method=row['method'], seed=row['seed'],
                objective=audited['objective'], bound=info['best_bound'], gap=info['mip_gap'],
                audited=True, replay=True, proven_optimal=info['proven_optimal'],
                root_runtime=cert['runtime'], root_complete=cert['relaxation_complete'],
                columns=cert['columns'], pricing_calls=len(priced),
                pricing_work=sum(r.get('work', 0) for r in priced),
                pool=info.get('route_pool_primal'))
            records.append(record)
            grouped.setdefault((row['case'], row['seed']), {})[row['method']] = record
        for (case, seed), data in grouped.items():
            old = data['columns_dual']
            new = data.get('columns_batch_reuse', data.get('columns_batch'))
            pairs.append(dict(batch=name, case=case, seed=seed,
                objective_before=old['objective'], objective_after=new['objective'],
                bound_before=old['bound'], bound_after=new['bound'],
                gap_before=old['gap'], gap_after=new['gap'],
                gap_improved=new['gap'] < old['gap']-1e-8,
                distance_improved=new['objective'] < old['objective']-1e-8,
                root_faster=new['root_runtime'] < old['root_runtime']))
    assert all(math.isclose(d['restricted_objective'], diagnostics[0]['restricted_objective'],
                           rel_tol=1e-10, abs_tol=1e-8) for d in diagnostics)
    summary = dict(runs=len(records), audits_passed=len(records), replays_passed=len(records),
        global_optimal_proofs=sum(r['proven_optimal'] for r in records),
        records=records, comparisons=pairs, root_diagnostics=diagnostics,
        root_diagnostic_caveat='same complete LP value; no original integer optimality proof',
        source_snapshot_differences={name:[k for k in hashes if hashes[k] != snapshots['root_pilot'].get(k)]
                                     for name, hashes in snapshots.items()},
        default='unchanged; pool pricing and reuse_root_time require explicit opt-in')
    (out/'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2)+'\n')
    (out/'result_manifest.json').write_text(json.dumps(files, indent=2)+'\n')
    print(json.dumps({k:summary[k] for k in ('runs','audits_passed','replays_passed','global_optimal_proofs',
                                           'source_snapshot_differences')}))


if __name__ == '__main__':
    verify()
