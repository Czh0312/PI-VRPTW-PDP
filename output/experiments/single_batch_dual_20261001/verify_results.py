"""Re-audit every result and check frozen input/source/result integrity."""
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
    folder = Path(__file__).resolve().parent
    batches = [ROOT/'output/experiments/single_batch_time_20261001'/name for name in ('final_short', 'enriched_short')]
    batches += [folder/name for name in ('final_short', 'final_long', 'fixed_handoff_long')]
    records, files, mismatches, comparisons = [], {}, [], []
    for batch in batches:
        manifest = json.loads((batch/'manifest.json').read_text())
        for key, item in manifest['input_sha256'].items():
            path = batch/item['snapshot']
            if sha(path) != item['sha256']:
                mismatches.append(str(path))
        for key, expected in manifest['source_sha256'].items():
            if sha(batch/'source_snapshot'/key) != expected:
                mismatches.append(str(batch/'source_snapshot'/key))
        rows = json.loads((batch/'comparison.json').read_text())
        grouped = {}
        for row in rows:
            assert row['valid'], row
            path = batch/row['case']/f'{row["method"]}_{row["seed"]}'
            job = json.loads((path/'job.json').read_text())
            config, fleet = load_routing_case(job['case_path'])
            p = load_instance(config, fleet)
            result = json.loads((path/'result.json').read_text())
            audited = audit_solution(p, result)
            assert audited['valid'] and audited['periodic_replay']['valid'], audited
            info = result['info']
            assert math.isclose(audited['objective'], info['objective'], abs_tol=1e-6)
            assert info['best_bound'] <= audited['objective']+1e-5
            assert not info['proven_infeasible']
            records.append(dict(batch=batch.name, case=row['case'], method=row['method'], seed=row['seed'],
                objective=audited['objective'], bound=info['best_bound'], gap=info['mip_gap'],
                valid=True, replay=True, proven_optimal=info['proven_optimal']))
            for name in ('result.json', 'row.json', 'job.json', 'solver.log'):
                files[str((path/name).relative_to(ROOT))] = sha(path/name)
            files[str(Path(job['start_path']).relative_to(ROOT))] = sha(Path(job['start_path']))
            if job.get('handoff_path'):
                files[str(Path(job['handoff_path']).relative_to(ROOT))] = sha(Path(job['handoff_path']))
            grouped.setdefault((row['case'], row['seed']), {})[row['method']] = row
        assert len(rows) == manifest['job_count']
        for (case, seed), methods in grouped.items():
            old = methods['columns_pool']
            new = methods.get('columns_dual', methods.get('columns_time'))
            comparisons.append(dict(batch=str(batch.relative_to(ROOT)), case=case, seed=seed,
                objective_before=old['objective'], objective_after=new['objective'],
                bound_before=old['bound'], bound_after=new['bound'],
                gap_before=old['gap'], gap_after=new['gap'],
                gap_improved=new['gap'] < old['gap']-1e-8,
                distance_improved=new['objective'] < old['objective']-1e-8))
    assert not mismatches, mismatches
    keys = list(json.loads((folder/'verification_source_before.json').read_text()))
    current = {key: sha(ROOT/key) for key in keys}
    (folder/'verification_source_after.json').write_text(json.dumps(current, indent=2)+'\n')
    short = json.loads((folder/'final_short/manifest.json').read_text())['source_sha256']
    long = json.loads((folder/'final_long/manifest.json').read_text())['source_sha256']
    handoff = json.loads((folder/'fixed_handoff_long/manifest.json').read_text())['source_sha256']
    source_differences = [key for key in short if short[key] != long.get(key)]
    handoff_differences = [key for key in long if long[key] != handoff.get(key)]
    summary = dict(runs=len(records), audits_passed=len(records), replays_passed=len(records),
        global_optimal_proofs=sum(r['proven_optimal'] for r in records), records=records,
        comparisons=comparisons, snapshot_hash_mismatches=mismatches,
        formal_short_long_source_differences=source_differences,
        formal_long_handoff_source_differences=handoff_differences,
        default='unchanged; time and assignment dual cuts require explicit options',
        budget_caveat='fixed_handoff_long measures only the complete MILP stage; its shared certificate/start generation is excluded')
    (folder/'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2)+'\n')
    (folder/'result_manifest.json').write_text(json.dumps(files, indent=2)+'\n')
    print(json.dumps({k: summary[k] for k in ('runs', 'audits_passed', 'replays_passed', 'global_optimal_proofs',
        'formal_short_long_source_differences', 'formal_long_handoff_source_differences')}))


if __name__ == '__main__':
    verify()
