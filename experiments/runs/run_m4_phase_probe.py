"""Equal-budget, interleaved baseline/phase search trials with route audits."""
import argparse
import hashlib
import json
import time
from pathlib import Path

from experiments.runs.run_model12346_alns_scaling import ROOT, load_problem, route_cost
from models.routing.alns.variants.model_4 import solve_alns
from models.routing.common.solution_audit import audit_exact_solution


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--instances', nargs='+')
    parser.add_argument('--seeds', nargs='+', type=int, default=[42,43,44])
    parser.add_argument('--fractions', nargs='+', type=float, default=[1.0,0.75,0.5])
    parser.add_argument('--seconds', type=float, default=5.0)
    args = parser.parse_args()
    fleet = json.loads((ROOT/'config/routing/validation/zhou_small.json').read_text(encoding='utf-8'))
    paths = sorted((ROOT/'data/zhou_later_pickup/small/standard_same_day').glob('*.csv'))
    if args.instances:
        paths = [p for p in paths if p.stem in args.instances]
        assert len(paths) == len(set(args.instances))
    refs = {}
    for line in (ROOT/'output/results/final_freeze_20260830/exact/small_exact_20260830_220305.jsonl').read_text(encoding='utf-8').splitlines():
        r=json.loads(line)
        if r['model']==4 and r['proven_optimal']:
            refs[r['instance']]=r['objective']
    args.output.parent.mkdir(parents=True, exist_ok=True)
    hashes={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted((ROOT/'models/routing').rglob('*.py'))}
    args.output.with_suffix('.manifest.json').write_text(json.dumps(dict(seconds=args.seconds,seeds=args.seeds,fractions=args.fractions,instances=[p.stem for p in paths],source_sha256=hashes),indent=2),encoding='utf-8')
    count=0
    with args.output.open('x',encoding='utf-8') as handle:
        for path in paths:
            for si,seed in enumerate(args.seeds):
                fractions=args.fractions[si%len(args.fractions):]+args.fractions[:si%len(args.fractions)]
                for fraction in fractions:
                    problem=load_problem(path,fleet)
                    start=time.perf_counter()
                    r1,r2,assignment,info=solve_alns(problem,problem.fleet.e2_per_sat_count,
                        max_iterations=1_000_000,max_time=args.seconds,seed=seed,
                        verbose=False,return_info=True,search_phase_fraction=fraction)
                    elapsed=time.perf_counter()-start
                    audit=audit_exact_solution(problem,4,r1,r2,assignment=assignment,objective=info['objective'])
                    delta=route_cost(problem,r1,r2)-info['objective']
                    assert info['feasible'] and audit['valid'] and abs(delta)<1e-6
                    ref=refs[path.stem]
                    assert info['objective']>=ref-1e-5
                    row=dict(instance=path.stem,seed=seed,fraction=fraction,wall_time=elapsed,
                        objective=info['objective'],exact_objective=ref,gap_pct=100*(info['objective']-ref)/ref,
                        hit=abs(info['objective']-ref)<=1e-5,audit=audit,info=info,
                        e1_routes=r1,e2_routes=r2,assignment=assignment)
                    handle.write(json.dumps(row,ensure_ascii=False)+'\n');handle.flush()
                    count+=1
                    print(count,path.stem,seed,fraction,round(row['gap_pct'],4),flush=True)
    assert all(hashlib.sha256((ROOT/p).read_bytes()).hexdigest()==h for p,h in hashes.items())
    print('COMPLETE',flush=True)


if __name__=='__main__':
    main()
