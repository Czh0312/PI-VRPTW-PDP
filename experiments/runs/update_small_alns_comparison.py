"""Refresh M4 on the original seeds, retaining explicit per-row provenance."""
import hashlib
import json
import sys
import time

from experiments.runs.run_model12346_alns_scaling import ROOT, load_problem, route_cost, solve_one
from experiments.runs.run_small_alns_validation import read_rows, references
from models.routing.common.solution_audit import audit_exact_solution

OUT = ROOT / 'output/experiments/small_alns_updated_20260908'
PREVIOUS = ROOT / 'output/experiments/small_alns_validation_20260908/verified_runs.jsonl'


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    previous = read_rows(PREVIOUS)
    instances = sorted({r['instance'] for r in previous})
    seeds = [42, 43, 44]
    assert len(previous) == 396 and len(instances) == 22
    fleet_path = ROOT / 'config/routing/validation/zhou_small.json'
    fleet = json.loads(fleet_path.read_text(encoding='utf-8'))
    refs, reference_paths = references()
    sources = sorted((ROOT/'models/routing').rglob('*.py')) + [fleet_path, PREVIOUS] + reference_paths
    sources += [ROOT/'data/zhou_later_pickup/small/standard_same_day'/f'{name}.csv' for name in instances]
    hashes = {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sources}
    manifest = dict(instances=instances, seeds=seeds, seconds=5, models=[1,2,3,4,6,7],
        rerun_models=[4], retained_models=[1,2,3,6,7], python=sys.executable,
        retained_source=str(PREVIOUS.relative_to(ROOT)), sha256=hashes, fleet=fleet,
        protocol='independent_serial', target_given=False, expected_reruns=66,
        note='M4 refreshed on the original seeds; other models retain their earlier audited runs. This is an updated comparison, not a new six-model simultaneous batch.')
    with (OUT/'manifest.json').open('x', encoding='utf-8') as handle:
        json.dump(manifest, handle, ensure_ascii=False, indent=2)
    rows = []
    with (OUT/'rerun_m4.jsonl').open('x', encoding='utf-8') as handle:
        for name in instances:
            for seed in seeds:
                problem = load_problem(ROOT/'data/zhou_later_pickup/small/standard_same_day'/f'{name}.csv', fleet)
                started = time.perf_counter()
                r1, r2, assignment, info = solve_one(4, problem, max_time=5, iterations=1_000_000, seed=seed)
                elapsed = time.perf_counter()-started
                audit = audit_exact_solution(problem,4,r1,r2,assignment=assignment,objective=info['objective'])
                delta = route_cost(problem,r1,r2)-info['objective']
                ref = refs[4,name]
                valid = bool(info['feasible'] and audit['valid'] and abs(delta)<1e-6)
                assert valid and info['objective'] >= ref-1e-5
                row = dict(instance=name,customers=len(problem.customers),seed=seed,model=4,
                    time_budget=5,exact_objective=ref,objective=info['objective'],valid=valid,
                    wall_time=elapsed,info=info,route_cost_delta=delta,audit=audit,
                    e1_routes=r1,e2_routes=r2,assignment=assignment,
                    gap_pct=100*(info['objective']-ref)/ref,source_batch=OUT.name,rerun=True)
                handle.write(json.dumps(row,ensure_ascii=False)+'\n');handle.flush()
                rows.append(row)
                print(f"{len(rows)}/66 {name} seed={seed} gap={row['gap_pct']:.4f}%",flush=True)
    for row in previous:
        if row['model'] != 4:
            row = dict(row)
            row.update(source_batch=PREVIOUS.parent.name,rerun=False)
            rows.append(row)
    rows.sort(key=lambda r:(r['instance'],r['seed'],r['model']))
    assert len(rows)==396
    assert len({(r['instance'],r['seed'],r['model']) for r in rows})==396
    assert all(hashlib.sha256((ROOT/p).read_bytes()).hexdigest()==h for p,h in hashes.items())
    (OUT/'runs.jsonl').write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in rows),encoding='utf-8')
    print('COMPLETE: 66 refreshed + 330 retained',flush=True)


if __name__ == '__main__':
    main()
