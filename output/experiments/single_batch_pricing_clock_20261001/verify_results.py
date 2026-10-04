"""Audit frozen root certificates separately from complete integer schedules."""
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
from models.routing.solvers.exact.single_batch_columns import problem_fingerprint


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read(path):
    return json.loads(path.read_text())


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)+'\n')


def main():
    hashes, snapshots, inputs = {}, {}, {}
    records, pairs = [], []
    shared_starts, shared_problems = set(), set()
    certificates = schedules = replays = global_proofs = 0
    for name in ('root_only', 'lazy_root_only', 'final_long'):
        batch = OUT/name
        manifest = read(batch/'manifest.json')
        assert manifest['threads'] == 1
        assert manifest['execution'] == 'sequential isolated subprocesses; no retries'
        snapshots[name] = manifest['source_sha256']
        inputs[name] = {key:item['sha256'] for key,item in manifest['input_sha256'].items()}
        for item in manifest['input_sha256'].values():
            path = batch/item['snapshot']
            assert sha(path) == item['sha256']
            hashes[str(path.relative_to(ROOT))] = sha(path)
        for path, expected in manifest['source_sha256'].items():
            assert sha(batch/'source_snapshot'/path) == expected
        rows = read(batch/'comparison.json')
        assert len(rows) == manifest['job_count'] == 4
        grouped = {}
        for row in rows:
            run = batch/row['case']/f'{row["method"]}_{row["seed"]}'
            assert read(run/'row.json') == row
            job, result = read(run/'job.json'), read(run/'result.json')
            config, fleet = load_routing_case(job['case_path'])
            problem = load_instance(config, fleet)
            start = read(Path(job['start_path']))
            start_audit = audit_solution(problem, start)
            assert start_audit['valid'] and start_audit['periodic_replay']['valid']
            shared_starts.add(sha(Path(job['start_path'])))
            shared_problems.add(problem_fingerprint(problem))
            root_only = name != 'final_long'
            certificate = result if root_only else result['info']['route_bound_certificate']
            assert certificate['proof_scope'] == 'B1_delivery_route_relaxation'
            assert certificate['problem_sha256'] == problem_fingerprint(problem)
            assert certificate['relaxation_complete'] and certificate['status'] == 'LP_OPTIMAL'
            assert not certificate['proven_optimal'] and not certificate['proven_infeasible']
            assert '_route_pool' not in certificate
            assert 0 <= certificate['bound'] <= start_audit['objective']+1e-6
            assert certificate['bound'] <= certificate['restricted_objective']+1e-6
            assert certificate['pricing_continuous_time'] == (row['method']=='columns_clock')
            assert certificate.get('pricing_lazy_time', False) == (row['method']=='columns_lazy')
            domain = ('continuous' if row['method']=='columns_clock' else
                      'lazy' if row['method']=='columns_lazy' else 'integer')
            pricing = [price for history in certificate['history']
                       for price in history.get('pricing', {}).values()]
            assert pricing and all(price['time_domain'] == domain for price in pricing)
            assert all(price['filtered_window_nodes'] == 0 for price in pricing)
            if domain != 'integer':
                assert all(price['integer_time_variables'] == 0 for price in pricing)
            record = {**row, 'batch':name, 'start_sha256':sha(Path(job['start_path'])),
                'problem_sha256':certificate['problem_sha256'],
                'restricted_objective':certificate['restricted_objective'],
                'lazy_time_callbacks':sum(p.get('lazy_time_callbacks',0) for p in pricing),
                'lazy_time_cuts':sum(p.get('lazy_time_cuts',0) for p in pricing)}
            if root_only:
                assert row['root_only'] and row['objective'] is None and row['gap'] is None
                record['audit_scope'] = 'shared integer start and root certificate metadata/bounds; no integer result'
                certificates += 1
            else:
                audit = audit_solution(problem, result)
                info, pool = result['info'], result['info']['route_pool_primal']
                assert audit['valid'] and audit['periodic_replay']['valid']
                assert math.isclose(audit['objective'], row['objective'], rel_tol=0, abs_tol=1e-6)
                assert math.isclose(audit['objective'], info['objective'], rel_tol=0, abs_tol=1e-6)
                assert audit['objective'] <= start_audit['objective']+1e-6
                assert info['best_bound'] <= audit['objective']+1e-6
                assert not info['proven_infeasible'] and not pool['global_proof']
                assert pool['formulation'] == 'compact_integer_cargo'
                assert not pool['slot_bounds']['enabled'] and not pool['slot_bounds']['aggregate_time']
                assert pool['reused_root_budget'] == 0 and pool['rejected_schedules'] == 0
                assert info['mip_start_supplied'] and info['single_batch_route_dual_cuts'] > 0
                assert row['budget'] == 60 and row['wall_time'] < 61
                record.update(audit_scope='complete original integer schedule and two-cycle replay',
                    audited=True, replay=True, original_mip_runtime=info['mip_runtime'],
                    original_mip_work=info['mip_work'])
                schedules += 1
                replays += 1
                global_proofs += int(info['proven_optimal'])
            records.append(record)
            grouped.setdefault((row['case'], row['seed']), {})[row['method']] = record
            for path in (run/'job.json', run/'row.json', run/'result.json', run/'solver.log',
                         Path(job['start_path']), Path(job['case_path'])):
                hashes[str(path.relative_to(ROOT))] = sha(path)
        for (case, seed), group in grouped.items():
            old = group['columns_compact' if not root_only else 'columns_batch']
            new = group['columns_clock' if name=='root_only' else 'columns_lazy']
            assert old['start_sha256'] == new['start_sha256']
            assert math.isclose(old['restricted_objective'], new['restricted_objective'], rel_tol=0, abs_tol=1e-6)
            pair = dict(batch=name, case=case, seed=seed,
                bound_before=old['bound'], bound_after=new['bound'])
            if root_only:
                pair.update(runtime_before=old['runtime'], runtime_after=new['runtime'],
                    work_before=old['pricing_work'], work_after=new['pricing_work'],
                    runtime_reduction=1-new['runtime']/old['runtime'],
                    work_reduction=1-new['pricing_work']/old['pricing_work'])
            else:
                pair.update(objective_before=old['objective'], objective_after=new['objective'],
                    gap_before=old['gap'], gap_after=new['gap'],
                    pool_objective_before=old['pool']['objective'], pool_objective_after=new['pool']['objective'],
                    distance_improved=new['objective']<old['objective']-1e-6,
                    distance_regressed=new['objective']>old['objective']+1e-6)
            pairs.append(pair)
        for path in (batch/'manifest.json', batch/'comparison.json'):
            hashes[str(path.relative_to(ROOT))] = sha(path)
    assert len(shared_starts) == len(shared_problems) == 1
    assert inputs['root_only'] == inputs['lazy_root_only'] == inputs['final_long']
    owned_runtime = ('models/routing/solvers/exact/single_batch_columns.py',
        'models/routing/solvers/exact/single_batch_delivery_pricer.py',
        'models/routing/solvers/exact/single_batch_pricing_schedule.py',
        'models/routing/solvers/exact/single_batch_route_pool.py',
        'experiments/runs/run_single_batch_columns.py')
    for path in owned_runtime:
        assert snapshots['lazy_root_only'][path] == snapshots['final_long'][path] == sha(ROOT/path)
    summary = dict(root_only_certificates=certificates, integer_runs=schedules,
        integer_audits_passed=schedules, integer_replays_passed=replays,
        original_global_optimal_proofs=global_proofs, shared_start_content_count=len(shared_starts),
        records=records, comparisons=pairs,
        decision='No default change: continuous pricing slower; lazy pricing root gain did not improve either full solve. Retain explicit experimental opt-ins; keep invalid-window fix.',
        validation=read(OUT/'validation.json'),
        source_snapshot_differences={name:[p for p in sorted(set(snap)|set(snapshots['final_long']))
                if snap.get(p) != snapshots['final_long'].get(p)] for name,snap in snapshots.items()},
        audit_scope='Root-only outputs are certificates, not integer schedules. Exact cut validity independently tested against all tiny feasible routes; no global proof from pool or RMP.')
    write(OUT/'summary.json', summary)
    write(OUT/'result_manifest.json', hashes)
    print(json.dumps({k:summary[k] for k in ('root_only_certificates','integer_runs',
        'integer_audits_passed','integer_replays_passed','original_global_optimal_proofs',
        'shared_start_content_count','source_snapshot_differences')}, ensure_ascii=False))


if __name__ == '__main__':
    main()
