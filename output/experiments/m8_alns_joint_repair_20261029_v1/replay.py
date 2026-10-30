"""Fixed-work repair replay. No random experiments, exact plans or optimal costs."""
import argparse
import hashlib
from itertools import combinations
import json
import os
import subprocess
import sys
import time
from bench import HERE, ROOT, read, save, sha, sources


def worker(variant):
    sys.path[:0] = [str(HERE/'sources'/variant), str(ROOT)]
    from models.common.cycle import load_instance
    from tools.configuration.loader import load_routing_case
    from models.routing.model_8.alns.construction import _seed_from_result
    from models.routing.model_8.alns.evaluator import JointEvaluator
    from models.routing.model_8.alns.neighborhoods import destroy_repair, _local_cost_scorer
    rows = []
    for case in [f'S{i:02}' for i in range(1, 10)]:
        parent = load_instance(*load_routing_case(ROOT/f'data/formal_benchmark27_uniform_7_20_50_v1/model_8/small/{case}/case.json', use_pool_config=False))
        historical = read(HERE/f'runs/final/{case}/baseline/result.json')
        child = parent.fixed_calendar(historical['cycle_batch_count'], historical['batch_boundaries'])
        _, assignment, state = _seed_from_result(child, historical)
        ev = JointEvaluator(child)
        fixed = ev.fixed(assignment, state['cutoffs'])
        fixed._joint_cost_rates = child.cost_profile.rates
        for removed in combinations(sorted(assignment), 2):
            class FixedRemoval:
                def sample(self, population, count):
                    assert count == 2
                    return [next(o for o in population if o.id == oid) for oid in removed]
            validate = lambda trial: ev(assignment, {**trial, 'cutoffs': dict(state['cutoffs'])})
            stats = {}
            kwargs = dict(validator=validate, repair_stats=stats) if variant == 'rescue' else {}
            begun, cpu = time.perf_counter(), time.process_time()
            proposal = destroy_repair(fixed, state, FixedRemoval(), 'random', float('inf'),
                                      scorer=_local_cost_scorer, **kwargs)
            value, result, issues = validate(proposal)
            elapsed, cpu_elapsed = time.perf_counter()-begun, time.process_time()-cpu
            payload = json.dumps(proposal, sort_keys=True, separators=(',', ':'))
            rows.append(dict(case=case, removed=list(removed), valid=result is not None,
                objective=value if result is not None else None,
                periodic_valid=result['audit']['periodic_replay']['valid'] if result else None,
                issues=list(issues), proposal_sha256=hashlib.sha256(payload.encode()).hexdigest(),
                wall_seconds=elapsed, cpu_seconds=cpu_elapsed, stats=stats))
        print(json.dumps(dict(variant=variant, case=case, pairs=21,
            valid=sum(r['valid'] for r in rows if r['case'] == case)), ensure_ascii=False), flush=True)
    assert not any(name.startswith('gurobipy') or (name.startswith('models.') and '.exact' in name) for name in sys.modules)
    save(HERE/f'replay_{variant}.json', rows)


def run():
    plan_path = HERE/'replay_plan.json'
    if plan_path.exists():
        raise FileExistsError(plan_path)
    variants = {v: sources(v) for v in ['baseline', 'rescue']}
    historical_hashes = {f'runs/final/{case}/baseline/result.json': sha(HERE/f'runs/final/{case}/baseline/result.json')
                         for case in [f'S{i:02}' for i in range(1, 10)]}
    plan = dict(variants=variants, driver_sha256=sha(__file__), historical_hashes=historical_hashes,
        input_hashes=read(HERE/'inputs.json'), original_experiment_root_seed=42,
        design='Every unordered pair of requests from each of nine recorded ALNS incumbents; deterministic fixed workload.',
        extra_experiment_seeds=0, exact_reference_used=False, optimal_plans_used=False)
    save(plan_path, plan)
    for variant in variants:
        completed = subprocess.run([sys.executable, '-X', 'utf8', str(HERE/'replay.py'), '--worker', variant],
            cwd=ROOT, env={**os.environ, 'PYTHONHASHSEED': '0'}, capture_output=True, text=True, encoding='utf-8', timeout=180)
        (HERE/f'replay_{variant}.log').write_text(completed.stdout+completed.stderr, encoding='utf-8')
        if completed.returncode:
            raise RuntimeError(completed.stdout+completed.stderr)
        print(completed.stdout.strip(), flush=True)
    before, after = read(HERE/'replay_baseline.json'), read(HERE/'replay_rescue.json')
    assert len(before) == len(after) == 189
    preserved = recovered = lost = 0
    for a, b in zip(before, after):
        assert (a['case'], a['removed']) == (b['case'], b['removed'])
        if a['valid']:
            assert b['valid'] and abs(a['objective']-b['objective']) <= 1e-9
            assert a['proposal_sha256'] == b['proposal_sha256']
            preserved += 1
        elif b['valid']:
            assert 'TIMING_CYCLE' in a['issues'] and b['periodic_valid']
            recovered += 1
        lost += int(a['valid'] and not b['valid'])
    summary = dict(pairs=189, baseline_valid=sum(r['valid'] for r in before), rescue_valid=sum(r['valid'] for r in after),
        preserved_feasible_proposals=preserved, recovered_from_timing_cycles=recovered, lost_feasible_proposals=lost,
        baseline_cpu_seconds=sum(r['cpu_seconds'] for r in before), rescue_cpu_seconds=sum(r['cpu_seconds'] for r in after),
        attempts=sum(r['stats'].get('timing_repair_attempts', 0) for r in after),
        successes=sum(r['stats'].get('timing_repair_successes', 0) for r in after),
        full_ALNS_performance_comparison=False, extra_root_seeds=0)
    assert all(sources(v) == value for v, value in variants.items())
    assert all(sha(path) == value for path, value in plan['input_hashes'].items())
    assert sha(__file__) == plan['driver_sha256']
    save(HERE/'replay_summary.json', summary)
    print(json.dumps(summary, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--worker', choices=['baseline', 'rescue'])
    args = parser.parse_args()
    worker(args.worker) if args.worker else run()
