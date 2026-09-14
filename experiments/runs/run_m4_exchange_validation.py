"""Paired 5-second M4 trials with and without atomic exchange polishing."""
import hashlib
import argparse
import json
import sys
import time

from experiments.runs.run_model12346_alns_scaling import ROOT, load_problem, route_cost
from models.routing.alns.variants.model_4 import solve_alns
from models.routing.common.solution_audit import audit_exact_solution

OUT = ROOT/'output/experiments/m4_exchange_20260908'


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--output',type=type(OUT),default=OUT)
    parser.add_argument('--seeds',type=int,nargs='+',default=[11,23,67])
    args=parser.parse_args()
    output=args.output
    output.mkdir(parents=True,exist_ok=True)
    paths=sorted((ROOT/'data/zhou_later_pickup/small/standard_same_day').glob('*.csv'))
    fleet_path=ROOT/'config/routing/validation/zhou_small.json'
    fleet=json.loads(fleet_path.read_text(encoding='utf-8'))
    ref_path=ROOT/'output/results/final_freeze_20260830/exact/small_exact_20260830_220305.jsonl'
    refs={}
    for line in ref_path.read_text(encoding='utf-8').splitlines():
        r=json.loads(line)
        if r['model']==4 and r['proven_optimal']:refs[r['instance']]=r['objective']
    sources=sorted((ROOT/'models/routing').rglob('*.py'))+[fleet_path,ref_path]+paths
    hashes={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in sources}
    seeds=args.seeds
    manifest=dict(instances=[p.stem for p in paths],seeds=seeds,seconds=5,iterations=1_000_000,
        python=sys.executable,fleet=fleet,sha256=hashes,protocol='independent_interleaved',exact_target_given=False)
    manifest['polish_reserve_seconds']=0.05
    (output/'manifest.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
    count=0
    with (output/'runs.jsonl').open('x',encoding='utf-8') as handle:
        for path in paths:
            for index,seed in enumerate(seeds):
                for enabled in ([False,True] if index%2==0 else [True,False]):
                    problem=load_problem(path,fleet)
                    started=time.perf_counter()
                    r1,r2,assignment,info=solve_alns(problem,problem.fleet.e2_per_sat_count,
                        max_time=5,max_iterations=1_000_000,seed=seed,verbose=False,
                        return_info=True,search_phase_fraction=1.0,exchange_polish=enabled)
                    elapsed=time.perf_counter()-started
                    audit=audit_exact_solution(problem,4,r1,r2,assignment=assignment,objective=info['objective'])
                    delta=route_cost(problem,r1,r2)-info['objective']
                    assert info['feasible'] and audit['valid'] and abs(delta)<1e-6
                    ref=refs[path.stem]
                    assert info['objective']>=ref-1e-5
                    r=dict(instance=path.stem,seed=seed,enabled=enabled,wall_time=elapsed,
                        objective=info['objective'],exact_objective=ref,gap_pct=100*(info['objective']-ref)/ref,
                        hit=abs(info['objective']-ref)<=1e-5,info=info,audit=audit,route_cost_delta=delta,
                        e1_routes=r1,e2_routes=r2,assignment=assignment)
                    handle.write(json.dumps(r,ensure_ascii=False)+'\n');handle.flush()
                    count+=1
                    print(count,path.stem,seed,enabled,round(r['gap_pct'],4),flush=True)
    assert count==len(paths)*len(seeds)*2
    assert all(hashlib.sha256((ROOT/p).read_bytes()).hexdigest()==h for p,h in hashes.items())
    print('COMPLETE',flush=True)


if __name__=='__main__':
    main()
