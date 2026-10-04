"""Independently audit every full-kernel run and its frozen provenance."""
from pathlib import Path
import hashlib
import json
import math
import sys

ROOT = Path(__file__).resolve().parents[3]
OUT = Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT))
from configuration.loader import load_routing_case
from models.routing.common.cycle import load_instance
from models.routing.common.cycle.audit import audit_solution
from models.routing.solvers.exact.single_batch_columns import problem_fingerprint

OWNED = ('models/routing/solvers/exact/single_batch_short_cycles.py',
         'models/routing/solvers/exact/single_batch_formulation.py',
         'models/routing/solvers/exact/formulation.py',
         'models/routing/solvers/exact/solver.py')


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read(path):
    return json.loads(path.read_text())


def write(path,value):
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False)+'\n')


def main():
    snapshots,files,records,pairs = {},{},[],[]
    batches = [('full20','run_compare_cycles.py'),('causal20','run_compare.py')]
    if (OUT/'causal30/manifest.json').exists():
        batches.append(('causal30','run_compare.py'))
    for name,script in batches:
        batch = OUT/name
        manifest = read(batch/'manifest.json')
        assert sha(OUT/script)==manifest['script_sha256']
        for item in manifest['input_sha256'].values():
            assert sha(batch/item['snapshot'])==item['sha256']
        for path,expected in manifest['source_sha256'].items():
            assert sha(batch/'source_snapshot'/path)==expected
        snapshots[name] = manifest['source_sha256']
        rows = read(batch/'comparison.json')
        assert len(rows)==manifest['job_count'] and manifest['threads']==1
        assert manifest['methods'][0]=='baseline' and len(manifest['methods'])==2
        new_method = manifest['methods'][1]
        expected = [(model,seed,method) for model in manifest['arguments']['models']
            for seed in (42,43) for method in (manifest['methods'] if seed==42 else reversed(manifest['methods']))]
        assert [(r['model'],r['seed'],r['method']) for r in rows]==expected
        grouped = {}
        for row in rows:
            run = batch/row['case']/f'{row["method"]}_{row["seed"]}'
            assert read(run/'row.json')==row
            job,result = read(run/'job.json'),read(run/'result.json')
            config,fleet = load_routing_case(job['case_path'])
            problem = load_instance(config,fleet)
            start = audit_solution(problem,read(Path(job['start_path'])))
            checked = audit_solution(problem,result)
            assert problem.model_number==row['model'] and len(problem.operating_policy.batches)==1
            assert start['valid'] and start['periodic_replay']['valid']
            assert checked['valid'] and checked['periodic_replay']['valid']
            assert checked['objective']<=start['objective']+1e-6
            info = result['info']
            assert math.isclose(checked['objective'],row['objective'],rel_tol=0,abs_tol=1e-6)
            assert math.isclose(checked['objective'],info['objective'],rel_tol=0,abs_tol=1e-6)
            assert info['best_bound']<=checked['objective']+1e-6
            assert not info['proven_infeasible'] and info['mip_start_supplied']
            assert info['proven_optimal']==(info['status']=='OPTIMAL')
            assert not info.get('route_bound_certificate') and not info.get('route_pool_primal')
            assert info['e2_short_cycle_cuts']==(row['method']=='cycles')
            assert info.get('e2_causal_step_bounds',False)==(row['method']=='causal')
            assert row['wall_time']<row['budget']+1
            tasks = [(e['order_id'],e['kind']) for t in result['e2_trips'] for e in t['events']]
            assert len(tasks)==len(set(tasks))==len(problem.orders)*len(problem.task_kinds)
            assert set(tasks)=={(o.id,k) for o in problem.orders for k in problem.task_kinds}
            record = {**row,'batch':name,'start_sha256':sha(Path(job['start_path'])),
                'problem_sha256':problem_fingerprint(problem),'audited':True,'replay':True,
                'proof_scope':info['proof_scope']}
            records.append(record)
            grouped.setdefault((row['case'],row['seed']),{})[row['method']] = record
            for path in (run/'job.json',run/'row.json',run/'result.json',run/'solver.log',
                         Path(job['start_path']),Path(job['case_path'])):
                files[str(path.relative_to(ROOT))] = sha(path)
        for (case,seed),group in grouped.items():
            old,new = group['baseline'],group[new_method]
            assert old['start_sha256']==new['start_sha256']
            assert old['problem_sha256']==new['problem_sha256']
            assert old['variables']==new['variables'] and old['general_constraints']==new['general_constraints']
            assert new['constraints']-old['constraints']==new['cycle_rows']
            if new_method=='causal':
                assert new['causal_largest_m']<=old['causal_largest_m']
            pairs.append(dict(batch=name,case=case,model=old['model'],seed=seed,method=new_method,
                objective_before=old['objective'],objective_after=new['objective'],
                bound_before=old['bound'],bound_after=new['bound'],gap_before=old['gap'],gap_after=new['gap'],
                work_before=old['work'],work_after=new['work'],
                wall_before=old['wall_time'],wall_after=new['wall_time'],
                optimal_before=old['proven_optimal'],optimal_after=new['proven_optimal'],
                distance_improved=new['objective']<old['objective']-1e-6,
                distance_regressed=new['objective']>old['objective']+1e-6))
        for path in (batch/'manifest.json',batch/'comparison.json',OUT/script):
            files[str(path.relative_to(ROOT))] = sha(path)
    final = batches[-1][0]
    assert all(snapshots[final][path]==sha(ROOT/path) for path in OWNED)
    summary = dict(integer_runs=len(records),audits_passed=len(records),replays_passed=len(records),
        original_global_optimal_proofs=sum(r['proven_optimal'] for r in records),
        records=records,comparisons=pairs,validation=read(OUT/'validation.json'),
        default='Both candidates remain False pending stable performance evidence.',
        scope='Complete original integer-time MILP, developmental same-budget paired runs; no root CG, route pool, inherited proof or held-out generalization claim.',
        source_snapshot_differences={name:[path for path in sorted(set(snapshot)|set(snapshots[final]))
            if snapshot.get(path)!=snapshots[final].get(path)] for name,snapshot in snapshots.items()})
    write(OUT/'summary.json',summary)
    write(OUT/'result_manifest.json',files)
    print(json.dumps({k:summary[k] for k in ('integer_runs','audits_passed','replays_passed',
        'original_global_optimal_proofs','source_snapshot_differences')},ensure_ascii=False))


if __name__=='__main__':
    main()
