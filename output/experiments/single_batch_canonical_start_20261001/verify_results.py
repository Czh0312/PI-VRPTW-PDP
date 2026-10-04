"""Verify every frozen version, common start/certificate and independent audit."""
from pathlib import Path
import hashlib
import json
import math
import re
import sys

ROOT=Path(__file__).resolve().parents[3]
OUT=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT))
from configuration.loader import load_routing_case
from models.routing.common.cycle import load_instance
from models.routing.common.cycle.audit import audit_solution


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write(path,value):
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')


def verify():
    records,pairs,files,snapshots=[],[],{},{}
    batches=['final_long','stable_long']
    if (OUT/'stable_holdout').exists():
        batches.append('stable_holdout')
    for name in batches:
        batch=OUT/name
        manifest=json.loads((batch/'manifest.json').read_text())
        for item in manifest['input_sha256'].values():
            assert sha(batch/item['snapshot'])==item['sha256']
        for path,expected in manifest['source_sha256'].items():
            assert sha(batch/'source_snapshot'/path)==expected
        snapshots[name]=manifest['source_sha256']
        rows=json.loads((batch/'comparison.json').read_text())
        assert len(rows)==manifest['job_count']
        grouped={}
        for row in rows:
            run=batch/row['case']/f'{row["method"]}_{row["seed"]}'
            job=json.loads((run/'job.json').read_text())
            result=json.loads((run/'result.json').read_text())
            config,fleet=load_routing_case(job['case_path'])
            problem=load_instance(config,fleet)
            initial=json.loads(Path(job['start_path']).read_text())
            assert audit_solution(problem,initial)['valid']
            audited=audit_solution(problem,result)
            assert audited['valid'] and audited['periodic_replay']['valid']
            info=result['info']
            assert math.isclose(audited['objective'],info['objective'],abs_tol=1e-6,rel_tol=0)
            assert info['best_bound']<=audited['objective']+1e-5
            assert info['objective']<=audit_solution(problem,initial)['objective']+1e-6
            assert not info['proven_infeasible']
            assert not info['route_pool_primal']['global_proof']
            assert '_route_pool' not in info['route_bound_certificate']
            for field in ('input_normalization','output_normalization'):
                if field in info['route_pool_primal']:
                    assert not info['route_pool_primal'][field]['global_proof']
            for filename in ('job.json','row.json','result.json','solver.log'):
                files[str((run/filename).relative_to(ROOT))]=sha(run/filename)
            start_sha=sha(Path(job['start_path']))
            files[str(Path(job['start_path']).relative_to(ROOT))]=start_sha
            record=dict(batch=name,case=row['case'],seed=row['seed'],method=row['method'],
                objective=audited['objective'],bound=info['best_bound'],gap=info['mip_gap'],
                audited=True,replay=True,proven_optimal=info['proven_optimal'],
                start_sha256=start_sha,root_runtime=row['root_time'],pool=info['route_pool_primal'],
                start_normalization=info.get('delivery_start_normalization'))
            records.append(record)
            grouped.setdefault((row['case'],row['seed']),{})[row['method']]=record
        for (case,seed),group in grouped.items():
            old,new=group['columns_compact'],group['columns_canonical']
            assert old['start_sha256']==new['start_sha256']
            pairs.append(dict(batch=name,case=case,seed=seed,
                objective_before=old['objective'],objective_after=new['objective'],
                bound_before=old['bound'],bound_after=new['bound'],gap_before=old['gap'],gap_after=new['gap'],
                distance_improved=new['objective']<old['objective']-1e-6,
                distance_regressed=new['objective']>old['objective']+1e-6,
                gap_improved=new['gap']<old['gap']-1e-6,
                gap_regressed=new['gap']>old['gap']+1e-6))
    fixed=OUT/'fixed_handoff'
    manifest=json.loads((fixed/'manifest.json').read_text())
    snapshot=Path(manifest['source_snapshot'])
    for path,expected in manifest['source_sha256'].items():
        assert sha(snapshot/path)==expected
    assert sha(OUT/'run_fixed_handoff.py')==manifest['script_sha256']
    fixed_rows=json.loads((fixed/'comparison.json').read_text())
    assert len(fixed_rows)==4
    fixed_records=[]
    for row in fixed_rows:
        run=fixed/f'{row["method"]}_{row["seed"]}'
        job=json.loads((run/'job.json').read_text())
        assert sha(Path(job['start_path']))==job['start_sha256']==row['start_sha256']
        assert sha(Path(job['certificate_path']))==job['certificate_sha256']==row['certificate_sha256']
        config,fleet=load_routing_case(job['case_path'])
        problem=load_instance(config,fleet)
        result=json.loads((run/'result.json').read_text())
        audited=audit_solution(problem,result)
        assert audited['valid'] and audited['periodic_replay']['valid']
        assert math.isclose(audited['objective'],row['objective'],abs_tol=1e-6,rel_tol=0)
        assert row['bound']<=audited['objective']+1e-5
        if row['normalization']:
            assert row['normalization']['vehicle_labels_changed']==row['normalization']['trip_slots_changed']==0
            assert not row['normalization']['global_proof']
        log=(run/'solver.log').read_text()
        acceptance=[line for line in log.splitlines() if 'MIP start' in line]
        fixed_records.append(dict(**row,mip_start_log=acceptance))
        for filename in ('job.json','row.json','result.json','solver.log'):
            files[str((run/filename).relative_to(ROOT))]=sha(run/filename)
        for path in (Path(job['start_path']),Path(job['certificate_path'])):
            files[str(path.relative_to(ROOT))]=sha(path)
    fixed_pairs=[]
    for seed in (42,43):
        old,new=(next(r for r in fixed_records if r['seed']==seed and r['method']==method) for method in ('raw','canonical'))
        assert old['start_sha256']==new['start_sha256'] and old['certificate_sha256']==new['certificate_sha256']
        fixed_pairs.append(dict(seed=seed,objective_before=old['objective'],objective_after=new['objective'],
            gap_before=old['gap'],gap_after=new['gap'],bound_before=old['bound'],bound_after=new['bound']))
    summary=dict(integer_runs=len(records),fixed_handoff_runs=len(fixed_records),
        audits_passed=len(records)+len(fixed_records),replays_passed=len(records)+len(fixed_records),
        global_optimal_proofs=sum(r['proven_optimal'] for r in records+fixed_records),
        records=records,comparisons=pairs,fixed_handoff_records=fixed_records,fixed_handoff_comparisons=fixed_pairs,
        first_version='route-identity ties and E1 route-order labels; final_long regressions preserved',
        final_version='stable original vehicle ID ties; earliest audited fixed routes',
        source_snapshot_differences={name:[path for path,value in source.items() if value!=snapshots['stable_long'].get(path)]
                                     for name,source in snapshots.items()},
        validation=json.loads((OUT/'validation.json').read_text()) if (OUT/'validation.json').exists() else None,
        default='unchanged; pool_canonical=True explicit opt-in; M1/M2 only')
    write(OUT/'summary.json',summary)
    write(OUT/'result_manifest.json',files)
    print(json.dumps({k:summary[k] for k in ('integer_runs','fixed_handoff_runs','audits_passed','replays_passed','global_optimal_proofs','source_snapshot_differences')},ensure_ascii=False))


if __name__=='__main__':
    verify()
