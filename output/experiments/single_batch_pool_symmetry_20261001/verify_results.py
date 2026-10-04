"""Recheck all versions, frozen catalogs and every returned integer schedule."""
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


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read(path):
    return json.loads(path.read_text())


def write(path,value):
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False)+'\n')


def verify_snapshot(batch):
    manifest = read(batch/'manifest.json')
    for item in manifest['input_sha256'].values():
        assert sha(batch/item['snapshot']) == item['sha256']
    for path,expected in manifest['source_sha256'].items():
        assert sha(batch/'source_snapshot'/path) == expected
    return manifest


def audit(job,result):
    config,fleet = load_routing_case(job['case_path'])
    problem = load_instance(config,fleet)
    start = audit_solution(problem,read(Path(job['start_path'])))
    checked = audit_solution(problem,result)
    assert start['valid'] and start['periodic_replay']['valid']
    assert checked['valid'] and checked['periodic_replay']['valid']
    assert checked['objective'] <= start['objective']+1e-6
    return problem,checked


def main():
    files,snapshots,records,pairs = {},{},[],[]
    fixed_records,fixed_pairs = [],[]
    for name in ('full_long','final_long','final_short'):
        batch = OUT/name
        manifest = verify_snapshot(batch)
        snapshots[name] = manifest['source_sha256']
        rows = read(batch/'comparison.json')
        assert len(rows)==manifest['job_count']==4 and manifest['threads']==1
        grouped = {}
        for row in rows:
            run = batch/row['case']/f'{row["method"]}_{row["seed"]}'
            assert read(run/'row.json')==row
            job,result = read(run/'job.json'),read(run/'result.json')
            problem,checked = audit(job,result)
            info = result['info']
            certificate,pool = info['route_bound_certificate'],info['route_pool_primal']
            assert math.isclose(checked['objective'],row['objective'],rel_tol=0,abs_tol=1e-6)
            assert math.isclose(checked['objective'],info['objective'],rel_tol=0,abs_tol=1e-6)
            assert info['best_bound'] <= checked['objective']+1e-6
            assert not info['proven_infeasible'] and not pool['global_proof']
            assert certificate['problem_sha256']==problem_fingerprint(problem)
            assert '_route_pool' not in certificate and not certificate['proven_optimal']
            assert not certificate['pricing_continuous_time'] and not certificate['pricing_lazy_time']
            assert not certificate['time_cuts'] and pool['reused_root_budget']==0
            assert pool['formulation']=='compact_integer_cargo' and pool['rejected_schedules']==0
            assert not pool['slot_bounds']['enabled'] and not pool['slot_bounds']['aggregate_time']
            assert pool['slot_bounds']['vehicle_ordering']==(row['method']=='columns_symmetry')
            assert row['wall_time'] < row['budget']+1
            record = {**row,'batch':name,'start_sha256':sha(Path(job['start_path'])),
                'audited':True,'replay':True,'problem_sha256':certificate['problem_sha256'],
                'columns':certificate['columns'],'original_work':info['mip_work'],
                'original_mip_runtime':info['mip_runtime']}
            records.append(record)
            grouped.setdefault((row['case'],row['seed']),{})[row['method']] = record
            for path in (run/'job.json',run/'row.json',run/'result.json',run/'solver.log',
                         Path(job['start_path']),Path(job['case_path'])):
                files[str(path.relative_to(ROOT))] = sha(path)
        for (case,seed),group in grouped.items():
            old,new = group['columns_compact'],group['columns_symmetry']
            assert old['start_sha256']==new['start_sha256']
            assert old['problem_sha256']==new['problem_sha256']
            pairs.append(dict(batch=name,case=case,seed=seed,
                objective_before=old['objective'],objective_after=new['objective'],
                bound_before=old['bound'],bound_after=new['bound'],gap_before=old['gap'],gap_after=new['gap'],
                pool_objective_before=old['pool']['objective'],pool_objective_after=new['pool']['objective'],
                columns_before=old['columns'],columns_after=new['columns'],
                distance_improved=new['objective']<old['objective']-1e-6,
                distance_regressed=new['objective']>old['objective']+1e-6))
        for path in (batch/'manifest.json',batch/'comparison.json'):
            files[str(path.relative_to(ROOT))] = sha(path)
    for name,script in (('fixed_pool','run_fixed_pool.py'),('final_fixed_pool','run_final_fixed_pool.py')):
        batch = OUT/name
        manifest = verify_snapshot(batch)
        snapshots[name] = manifest['source_sha256']
        assert sha(OUT/script)==manifest['script_sha256']
        assert sha(Path(manifest['start_path']))==manifest['start_sha256']
        rows = read(batch/'comparison.json')
        assert len(rows)==4 and manifest['threads']==1
        grouped = {}
        for row in rows:
            run = batch/f'{"ordered" if row["ordered"] else "counts"}_{row["seed"]}'
            assert read(run/'row.json')==row
            job,result = read(run/'job.json'),read(run/'result.json')
            pool_path = Path(job['pool_path'])
            origin = Path(manifest['pools'][str(row['seed'])]['origin'])
            assert sha(pool_path)==job['pool_sha256']==row['pool_sha256']==sha(origin)
            problem,checked = audit(job,result)
            assert math.isclose(checked['objective'],row['objective'],rel_tol=0,abs_tol=1e-6)
            assert not row['original_global_proof'] and not row['stats']['global_proof']
            assert row['stats']['pool_status']==2
            assert row['stats']['slot_bounds']['vehicle_ordering']==row['ordered']
            assert not row['stats']['slot_bounds']['enabled'] and not row['stats']['slot_bounds']['aggregate_time']
            record = {**row,'batch':name,'audited':True,'replay':True}
            fixed_records.append(record)
            grouped.setdefault(row['seed'],{})[row['ordered']] = record
            for path in (run/'job.json',run/'row.json',run/'result.json',run/'solver.log',pool_path,
                         Path(job['start_path']),Path(job['case_path'])):
                files[str(path.relative_to(ROOT))] = sha(path)
        for seed,group in grouped.items():
            old,new = group[False],group[True]
            assert old['pool_sha256']==new['pool_sha256']
            assert math.isclose(old['objective'],new['objective'],rel_tol=0,abs_tol=1e-6)
            assert old['stats']['variables']==new['stats']['variables']
            assert old['stats']['placement_variables']==new['stats']['placement_variables']
            assert new['stats']['constraints']-old['stats']['constraints']==(0 if name=='fixed_pool' else 6)
            fixed_pairs.append(dict(batch=name,seed=seed,runtime_before=old['runtime'],runtime_after=new['runtime'],
                work_before=old['stats']['solve_work'],work_after=new['stats']['solve_work'],
                objective=old['objective'],runtime_reduction=1-new['runtime']/old['runtime'],
                work_reduction=1-new['stats']['solve_work']/old['stats']['solve_work']))
        for path in (batch/'manifest.json',batch/'comparison.json'):
            files[str(path.relative_to(ROOT))] = sha(path)
    owned = ('models/routing/solvers/exact/single_batch_route_pool.py',
             'models/routing/solvers/exact/single_batch_columns.py',
             'experiments/runs/run_single_batch_columns.py')
    for path in owned:
        assert snapshots['fixed_pool'][path]==snapshots['full_long'][path]
        assert snapshots['final_fixed_pool'][path]==snapshots['final_long'][path]==snapshots['final_short'][path]==sha(ROOT/path)
    summary = dict(integer_runs=len(records),fixed_pool_runs=len(fixed_records),
        audits_passed=len(records)+len(fixed_records),replays_passed=len(records)+len(fixed_records),
        original_global_optimal_proofs=sum(row['proven_optimal'] for row in records),
        records=records,comparisons=pairs,fixed_records=fixed_records,fixed_comparisons=fixed_pairs,
        validation=read(OUT/'validation.json'),
        default='unchanged; pool_vehicle_order=False; M1/M2 optional primal-only symmetry',
        final_improvements=sum(p['distance_improved'] for p in pairs if p['batch'].startswith('final_')),
        final_regressions=sum(p['distance_regressed'] for p in pairs if p['batch'].startswith('final_')),
        decision='No default promotion: first version mixed; final four full comparisons are two improvements and two regressions, with mixed same-catalog work.',
        scope='Within each batch same frozen inputs/start, budgets and runtime; fixed-pool timings exclude cached CG and JSON loading; no proof exported from pools.',
        source_snapshot_differences={name:[p for p in sorted(set(snapshot)|set(snapshots['final_long']))
            if snapshot.get(p)!=snapshots['final_long'].get(p)] for name,snapshot in snapshots.items()})
    write(OUT/'summary.json',summary)
    write(OUT/'result_manifest.json',files)
    print(json.dumps({k:summary[k] for k in ('integer_runs','fixed_pool_runs','audits_passed','replays_passed',
        'original_global_optimal_proofs','source_snapshot_differences')},ensure_ascii=False))


if __name__=='__main__':
    main()
