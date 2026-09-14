"""Long-running, checkpointed use of the original M7 partition solver.

Three isolated processes, four solver threads each. Completed partitions are
reused only with the same inputs/code and a compatible objective cutoff.
"""
import hashlib
import json
import math
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.append(r'C:\gurobi\win64\python311\lib')
OUT = ROOT / 'output/experiments/m7_partition_completion_20260908'


def read_rows(path):
    return [json.loads(s) for s in path.read_text(encoding='utf-8').splitlines() if s.strip()] if path.exists() else []


def save(path, value):
    temp=path.with_suffix(path.suffix+'.tmp')
    temp.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')
    temp.replace(path)


def reusable(record, cutoff):
    info=record['info']
    if info.get('status')=='INFEASIBLE':
        return True
    if info.get('proven_optimal') and info.get('objective') is not None:
        return True
    return (info.get('status')=='CUTOFF' and cutoff is not None
            and record.get('cutoff') is not None and record['cutoff']>=cutoff)


def run_instance(name, stage):
    from experiments.runs.run_model12346_alns_scaling import load_problem
    from models.routing.common.solution_audit import audit_exact_solution
    from models.routing.variants.model_7 import exact_solver as solver
    manifest=json.loads((OUT/'manifest.json').read_text(encoding='utf-8'))
    assert all(hashlib.sha256((ROOT/p).read_bytes()).hexdigest()==h for p,h in manifest['sha256'].items())
    folder=OUT/name
    folder.mkdir(exist_ok=True)
    rows=read_rows(ROOT/'output/experiments/small_alns_updated_20260908/runs.jsonl')
    warm=min((r for r in rows if r['instance']==name and r['model'] in (4,7)
              and r['valid'] and not r['info'].get('transshipment_count')),key=lambda r:r['objective'])
    fleet=json.loads((ROOT/'config/routing/validation/zhou_small.json').read_text())
    problem=load_problem(ROOT/f'data/zhou_later_pickup/small/standard_same_day/{name}.csv',fleet)
    audit=audit_exact_solution(problem,4,warm['e1_routes'],warm['e2_routes'],
                              assignment=warm['assignment'],objective=warm['objective'])
    assert audit['valid'],audit
    journal=folder/'partitions.jsonl'
    cache={r['key']:r for r in read_rows(journal)}
    original_build=solver.build_assignment_subproblem
    original_partition=solver._solve_partition
    original_fixed=solver._solve_fixed_assignment
    active={}
    counts={'reused':0,'computed':0}
    started=time.time()
    seconds=min(28800,1800*2**stage)
    slice_seconds=min(7200,5*6**stage)

    def build(p,assignment):
        active['assignment']=dict(assignment)
        active['key']=json.dumps(assignment,sort_keys=True)
        return original_build(p,assignment)

    def checkpoint(result,cutoff):
        e1,e2,info=result
        record=dict(key=active['key'],assignment=active['assignment'],
                    e1_routes=e1,e2_routes=e2,info=info,cutoff=cutoff,stage=stage)
        with journal.open('a',encoding='utf-8') as handle:
            handle.write(json.dumps(record,ensure_ascii=False)+'\n')
            handle.flush()
        cache[record['key']]=record
        counts['computed']+=1
        save(folder/'progress.json',dict(instance=name,stage=stage,pid=os.getpid(),
            started=started,updated=time.time(),seconds_budget=seconds,
            partition_seconds=slice_seconds,unique_partitions=len(cache),
            resolved_cached=sum(reusable(r,warm['objective']-1e-9) for r in cache.values()),
            **counts))
        return result

    def partition(*args,**kwargs):
        cutoff=kwargs['incumbent']-1e-9
        record=cache.get(active['key'])
        if record and reusable(record,cutoff):
            counts['reused']+=1
            return record['e1_routes'],record['e2_routes'],record['info']
        return checkpoint(original_partition(*args,**kwargs),cutoff)

    def fixed(*args,**kwargs):
        # Also retain results of the original solver's second-pass retries.
        return checkpoint(original_fixed(*args,**kwargs),kwargs.get('gurobi_params',{}).get('Cutoff'))

    solver.build_assignment_subproblem=build
    solver._solve_partition=partition
    solver._solve_fixed_assignment=fixed
    try:
        e1,e2,assignment,info=solver.solve_exact(problem,time_limit=seconds,
            verbose=False,return_info=True,return_incumbent_on_time_limit=True,
            gurobi_params={'Threads':4},use_global_relaxation_certificate=False,
            partition_sweep_threshold=4096,partition_time_limit=slice_seconds,
            warm_start_routes=(warm['e1_routes'],warm['e2_routes']),
            warm_start_assignment=warm['assignment'],warm_start_objective=warm['objective'])
        result=dict(model=7,instance=name,stage=stage,**info,e1_routes=e1,e2_routes=e2)
        if not info.get('transshipment_count'):
            result['audit']=audit_exact_solution(problem,4,e1,e2,assignment=assignment,objective=info['objective'])
            assert result['audit']['valid'],result['audit']
        save(folder/f'stage_{stage}.json',result)
        save(folder/'latest.json',result)
        return {k:result.get(k) for k in ('instance','status','proven_optimal','objective','best_bound',
            'assignment_partitions_evaluated','assignment_partitions_resolved')}
    finally:
        solver.build_assignment_subproblem=original_build
        solver._solve_partition=original_partition
        solver._solve_fixed_assignment=original_fixed


def main():
    from experiments.runs.run_small_alns_validation import references
    OUT.mkdir(exist_ok=True)
    save(OUT/'supervisor.json',dict(pid=os.getpid(),started=time.time()))
    refs,_=references()
    source=ROOT/'output/experiments/small_alns_updated_20260908/runs.jsonl'
    names=sorted({r['instance'] for r in read_rows(source) if r['model']==7 and (7,r['instance']) not in refs})
    paths=list((ROOT/'models/routing').rglob('*.py'))+[Path(__file__),source,
        ROOT/'config/routing/validation/zhou_small.json']
    paths += [ROOT/f'data/zhou_later_pickup/small/standard_same_day/{n}.csv' for n in names]
    manifest=dict(instances=names,workers=3,threads_per_worker=4,method='original_partition_enumeration',
                  sha256={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in paths})
    if (OUT/'manifest.json').exists():
        assert json.loads((OUT/'manifest.json').read_text(encoding='utf-8'))==manifest,'Inputs changed; do not reuse proofs'
    else:
        save(OUT/'manifest.json',manifest)
    stage=0
    while True:
        pending=[]
        for name in names:
            latest=OUT/name/'latest.json'
            if latest.exists():
                r=json.loads(latest.read_text(encoding='utf-8'))
                if r['proven_optimal'] or r['stage']>=stage:
                    continue
            pending.append(name)
        if pending:
            print('STAGE',stage,'instances',pending,flush=True)
            with ProcessPoolExecutor(max_workers=3) as pool:
                futures={pool.submit(run_instance,n,stage):n for n in pending}
                for future in as_completed(futures):
                    try:
                        result=future.result()
                    except Exception as exc:
                        save(OUT/'failure.json',dict(instance=futures[future],stage=stage,error=repr(exc)))
                        raise
                    print(json.dumps(result),flush=True)
        completed=[n for n in names if (OUT/n/'latest.json').exists()
                   and json.loads((OUT/n/'latest.json').read_text(encoding='utf-8'))['proven_optimal']]
        save(OUT/'status.json',dict(updated=time.time(),stage=stage,completed=completed,
                                  remaining=[n for n in names if n not in completed]))
        if len(completed)==len(names):
            print('ALL_PROVEN_OPTIMAL',flush=True)
            return
        stage+=1


if __name__=='__main__':
    main()
