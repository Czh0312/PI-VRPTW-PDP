"""Check frozen inputs/source/shared starts, then independently re-audit results."""
from pathlib import Path
import hashlib
import json
import math
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT))
from configuration.loader import load_routing_case
from models.routing.common.cycle import load_instance
from models.routing.common.cycle.audit import audit_solution

OUT = Path(__file__).resolve().parent


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write(path, value):
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')


def verify():
    records, pairs, hashes, snapshots = [], [], {}, {}
    for name in ('final_long', 'aggregate_long', 'aggregate_short'):
        batch = OUT/name
        manifest = json.loads((batch/'manifest.json').read_text())
        for item in manifest['input_sha256'].values():
            assert sha(batch/item['snapshot']) == item['sha256']
        for key, expected in manifest['source_sha256'].items():
            assert sha(batch/'source_snapshot'/key) == expected
        snapshots[name] = manifest['source_sha256']
        rows = json.loads((batch/'comparison.json').read_text())
        assert len(rows) == manifest['job_count']
        grouped = {}
        for row in rows:
            run = batch/row['case']/f'{row["method"]}_{row["seed"]}'
            job = json.loads((run/'job.json').read_text())
            result = json.loads((run/'result.json').read_text())
            config, fleet = load_routing_case(job['case_path'])
            problem = load_instance(config,fleet)
            start = json.loads(Path(job['start_path']).read_text())
            assert audit_solution(problem,start)['valid']
            for filename in ('job.json','row.json','result.json','solver.log'):
                hashes[str((run/filename).relative_to(ROOT))] = sha(run/filename)
            hashes[str(Path(job['start_path']).relative_to(ROOT))] = sha(Path(job['start_path']))
            audited = audit_solution(problem,result)
            assert audited['valid'] and audited['periodic_replay']['valid']
            info = result['info']
            assert math.isclose(audited['objective'],info['objective'],abs_tol=1e-6)
            assert info['best_bound'] <= audited['objective']+1e-5
            assert info['objective'] <= audit_solution(problem,start)['objective']+1e-6
            assert not info['proven_infeasible']
            assert not info['route_pool_primal']['global_proof']
            assert '_route_pool' not in info['route_bound_certificate']
            record = dict(batch=name,case=row['case'],seed=row['seed'],method=row['method'],
                objective=audited['objective'],bound=info['best_bound'],gap=info['mip_gap'],
                audited=True,replay=True,proven_optimal=info['proven_optimal'],
                root_runtime=row['root_time'],root_complete=info['route_bound_certificate']['relaxation_complete'],
                columns=info['route_bound_certificate']['columns'],pool=info['route_pool_primal'])
            records.append(record)
            grouped.setdefault((row['case'],row['seed']),{})[row['method']]=record
        for (case,seed), data in grouped.items():
            old,new = data['columns_batch'],data['columns_compact']
            pairs.append(dict(batch=name,case=case,seed=seed,
                objective_before=old['objective'],objective_after=new['objective'],
                gap_before=old['gap'],gap_after=new['gap'],
                gap_improved=new['gap']<old['gap']-1e-6,
                distance_improved=new['objective']<old['objective']-1e-6,
                pool_objective_before=old['pool']['objective'],pool_objective_after=new['pool']['objective'],
                pool_runtime_before=old['pool']['runtime'],pool_runtime_after=new['pool']['runtime']))
    fixed = OUT/'fixed_pool'
    manifest=json.loads((fixed/'manifest.json').read_text())
    snapshot=Path(manifest['source_snapshot'])
    for key,expected in manifest['source_sha256'].items():
        assert sha(snapshot/key)==expected
    assert sha(OUT/'run_fixed_pool.py')==manifest['script_sha256']
    assert sha(Path(manifest['start_path']))==manifest['start_sha256']
    fixed_rows=json.loads((fixed/'comparison.json').read_text())
    assert len(fixed_rows)==6
    fixed_records,fixed_pairs,root_diagnostics=[],[],[]
    for row in fixed_rows:
        if row['stage']=='root':
            run=fixed/f'root_{row["seed"]}'
            result=json.loads((run/'result.json').read_text())
            assert not result['proven_optimal'] and result['relaxation_complete']
            assert sha(fixed/f'pool_{row["seed"]}.json')==row['pool_sha256']
            for filename in ('job.json','row.json','result.json','solver.log'):
                hashes[str((run/filename).relative_to(ROOT))]=sha(run/filename)
            hashes[str((fixed/f'pool_{row["seed"]}.json').relative_to(ROOT))]=row['pool_sha256']
            root_diagnostics.append(dict(**row,restricted_objective=result['restricted_objective']))
            continue
        seed,compact=row['seed'],row['compact']
        run=fixed/f'{"compact" if compact else "legacy"}_{seed}'
        job=json.loads((run/'job.json').read_text())
        assert sha(Path(job['pool_path']))==row['pool_sha256']==job['pool_sha256']
        config,fleet=load_routing_case(job['case_path'])
        problem=load_instance(config,fleet)
        result=json.loads((run/'result.json').read_text())
        audited=audit_solution(problem,result)
        assert audited['valid'] and audited['periodic_replay']['valid']
        assert not row['stats']['global_proof'] and not result['info']['proven_optimal']
        assert math.isclose(audited['objective'],row['objective'],abs_tol=1e-6)
        for filename in ('job.json','row.json','result.json','solver.log'):
            hashes[str((run/filename).relative_to(ROOT))]=sha(run/filename)
        fixed_records.append(row)
    for seed in (42,43):
        old,new=(next(r for r in fixed_records if r['seed']==seed and r['compact']==flag) for flag in (False,True))
        assert old['pool_sha256']==new['pool_sha256']
        fixed_pairs.append(dict(seed=seed,pool_sha256=old['pool_sha256'],
            objective_before=old['objective'],objective_after=new['objective'],
            runtime_before=old['stats']['runtime'],runtime_after=new['stats']['runtime'],
            variables_before=old['stats']['variables'],variables_after=new['stats']['variables'],
            work_before=old['stats']['solve_work'],work_after=new['stats']['solve_work'],
            status_before=old['stats']['pool_status'],status_after=new['stats']['pool_status']))
    summary=dict(integer_runs=len(records),pool_only_runs=len(fixed_records),
        audits_passed=len(records)+len(fixed_records),replays_passed=len(records)+len(fixed_records),
        global_optimal_proofs=sum(r['proven_optimal'] for r in records),
        first_version='conditional site-set capacity (final_long); two regressions preserved',
        final_version='aggregate warehouse cargo by unordered E1 site set',
        records=records,comparisons=pairs,fixed_pool_records=fixed_records,fixed_pool_comparisons=fixed_pairs,
        root_diagnostics=root_diagnostics,
        validation=json.loads((OUT/'validation.json').read_text()) if (OUT/'validation.json').exists() else None,
        source_snapshot_differences={name:[key for key,value in hashes_.items() if value!=snapshots['aggregate_long'].get(key)]
                                     for name,hashes_ in snapshots.items()},
        default='unchanged; pool_compact=True explicitly required; M1/M2 only')
    write(OUT/'summary.json',summary)
    write(OUT/'result_manifest.json',hashes)
    print(json.dumps({k:summary[k] for k in ('integer_runs','pool_only_runs','audits_passed','replays_passed','global_optimal_proofs','source_snapshot_differences')},ensure_ascii=False))


if __name__=='__main__':
    verify()
