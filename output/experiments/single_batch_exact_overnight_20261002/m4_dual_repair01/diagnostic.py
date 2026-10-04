"""Conditional offline dual repair; no production or timing rerun."""
from pathlib import Path
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import json
import math
import os
import random
import sys
import time

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[3]
sys.path.insert(0, str(OUT/'source_snapshot'))
import gurobipy as gp
from configuration.loader import load_routing_case
from models.routing.common.cycle import MultiBatchInstance, load_instance
from models.routing.common.cycle.audit import audit_solution
from models.routing.solvers.exact.single_batch_columns import problem_fingerprint
from models.routing.solvers.exact.single_batch_pickup_bound import pickup_contexts, project_original_schedule
from models.routing.solvers.exact.solver import solve_exact
from tests.routing.test_single_batch_pickup_bound import independent_trips, fully_enumerated_joint_lp
from tests.routing.test_single_batch_solver_audit import tiny_problem, enumerate_tiny


def read(p): return json.loads(p.read_text())
def write(p, v): p.write_text(json.dumps(v, indent=2, allow_nan=False)+'\n')
def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()


def repair(problem, history, counts):
    """Original fleet-only repair is included; all routes must be nonempty."""
    assert problem.model_number == 4 and history['phase'] == 2
    prices = {s: (dict((tuple(k), float(v)) for k, v in rec['tasks']), float(rec['fleet']))
              for s, rec in history['prices'].items()}
    rho = history['pricing_lower_bounds']
    old_gamma = history['assignment_duals']
    customer_keys = {c: [(o.id, k) for o in problem.orders if o.customer == c for k in ('d', 'p')]
                     for c in old_gamma}
    eligible = {c: [s for s, (pi, _) in prices.items() if all(k in pi for k in keys)]
                for c, keys in customer_keys.items()}
    assert all(eligible.values()) and all(nu <= 1e-9 for _, nu in prices.values())
    m = gp.Model('offline_uniform_task_fleet_repair')
    m.Params.OutputFlag = 0; m.Params.Threads = 1; m.Params.TimeLimit = 5
    try:
        alpha = {s: m.addVar() for s in prices}; beta = {s: m.addVar() for s in prices}
        gamma = {c: m.addVar(lb=-gp.GRB.INFINITY, ub=old_gamma[c]) for c in old_gamma}
        for s in prices: m.addConstr(alpha[s]+beta[s] >= max(0.0, -rho[s]))
        for c, keys in customer_keys.items():
            for s in eligible[c]:
                m.addConstr(gamma[c] <= math.fsum(prices[s][0][k] for k in keys)-len(keys)*alpha[s])
        m.setObjective(gp.quicksum(gamma.values())+gp.quicksum(counts[s]*(prices[s][1]-beta[s])
                       for s in prices), gp.GRB.MAXIMIZE)
        m.optimize(); assert m.Status == gp.GRB.OPTIMAL
        a = {s: max(0.0, v.X) for s, v in alpha.items()}
        # Repair residual numerical infeasibility instead of trusting ObjVal.
        b = {s: max(0.0, v.X, -rho[s]-a[s]) for s, v in beta.items()}
        g = {c: min(old_gamma[c], *(math.fsum(prices[s][0][k]-a[s] for k in keys)
                                 for s in eligible[c])) for c, keys in customer_keys.items()}
        raw = math.fsum(g.values())+math.fsum(counts[s]*(prices[s][1]-b[s]) for s in prices)
        norm = math.fsum(abs(v) for v in g.values())
        norm += math.fsum(abs(v) for s, (pi, nu) in prices.items() for v in list(pi.values())+[nu, a[s], b[s]])
        margin = 1e-7*max(1.0, abs(raw), norm)*(1+sum(counts.values()))
        old = math.fsum(old_gamma.values())+math.fsum(counts[s]*(nu+min(0.0, rho[s]))
                                                     for s, (_, nu) in prices.items())
        return dict(alpha=a, beta=b, gamma=g, raw_objective=raw, margin=margin,
                    corrected=raw-margin, fleet_only_raw=old, lp_work=m.Work,
                    conditional_on_full_pricing_lower_bounds=True)
    finally: m.dispose()


def tiny_validation():
    rng = random.Random(61002); records = []; route_checks = assignment_checks = projections = 0
    for seed in (0, 1, 4, 5):
        for duplicate in (False, True):
            base = tiny_problem(4, seed)
            p = (MultiBatchInstance(base.network, [base.orders[0], replace(base.orders[1],
                 customer=base.orders[0].customer)], base.operating_policy, model=4) if duplicate else base)
            domains, contexts, check = pickup_contexts(p); assert check['passed']
            routes = {s: independent_trips(p, s, ctx) for s, ctx in contexts.items()}
            assert all(routes.values())
            counts = {s: c['maximum_trips'] for s, c in contexts.items()}
            oracle = fully_enumerated_joint_lp(p, domains, contexts); integer = enumerate_tiny(p)
            assert math.isfinite(integer) and math.isfinite(oracle)
            for revision in range(6):
                pi = {s: {n['key']: rng.uniform(-8, 30) for n in ctx['nodes']} for s, ctx in contexts.items()}
                nu = {s: -rng.uniform(0, 3) for s in contexts}
                gamma = {c: min(math.fsum(pi[s][o.id, k] for o in p.orders if o.customer == c for k in ('d', 'p'))
                               for s in choices) for c, choices in domains.items()}
                rho = {s: min(cost-math.fsum(pi[s][k] for k in seq)-nu[s] for seq, cost in rr)
                       - (rng.uniform(0, 5) if revision % 2 else 0) for s, rr in routes.items()}
                h = dict(phase=2, prices={s: dict(tasks=[[list(k), v] for k, v in pp.items()], fleet=nu[s])
                                         for s, pp in pi.items()}, pricing_lower_bounds=rho, assignment_duals=gamma)
                out = repair(p, h, counts)
                assert out['raw_objective'] >= out['fleet_only_raw']-1e-6
                assert out['corrected'] <= oracle+1e-6 and out['corrected'] <= integer+1e-6
                for s, rr in routes.items():
                    assert out['alpha'][s]+out['beta'][s] >= max(0, -rho[s])-1e-7
                    for seq, cost in rr:
                        rc = cost-math.fsum(pi[s][k]-out['alpha'][s] for k in seq)-(nu[s]-out['beta'][s])
                        assert rc >= -1e-7; route_checks += 1
                for c, choices in domains.items():
                    for s in choices:
                        total = math.fsum(pi[s][o.id, k]-out['alpha'][s] for o in p.orders
                                         if o.customer == c for k in ('d', 'p'))
                        assert out['gamma'][c] <= total+1e-7; assignment_checks += 1
                records.append(dict(seed=seed, duplicate=duplicate, revision=revision,
                                    full_joint_lp=oracle, original_integer_optimum=integer, **out))
            # Original physical schedule before large-history diagnostic.
            start = solve_exact(p, time_limit=3); audit = audit_solution(p, start)
            assert audit['valid'] and audit['periodic_replay']['valid']
            assert start['info']['proven_optimal']; project_original_schedule(p, start, contexts)
            projections += 1
    write(OUT/'tiny_records.json', records)
    summary = dict(original_tiny_cases=8, repeated_customer_cases=4, repair_records=len(records),
                   raw_route_duals_checked=route_checks, assignment_duals_checked=assignment_checks,
                   audited_original_schedule_projections=projections)
    write(OUT/'tiny_verification.json', summary); return summary


def original_projections():
    contexts = {}; records = []
    for batch in ('paired15_30', 'independent15_30'):
        for folder in sorted((OUT/'frozen_histories'/batch).glob('zhou*')):
            cfg = read(folder/'case.json')
            for key in ('data', 'orders', 'routing_config', 'operating_policy', 'dataset_metadata', 'pool_config'):
                original = Path(cfg[key]); cfg[key] = str((OUT/'frozen_histories'/batch/'inputs'/original.name).resolve())
            write(folder/'case_rebased.json', cfg)
            config, fleet = load_routing_case(folder/'case_rebased.json'); p = load_instance(config, fleet)
            start = read(folder/'start.json'); a = audit_solution(p, start)
            assert a['valid'] and a['periodic_replay']['valid']
            _, ctx, gate = pickup_contexts(p); assert gate['passed']
            columns = project_original_schedule(p, start, ctx)
            contexts[batch, folder.name] = p
            records.append(dict(batch=batch, case=folder.name, problem_sha256=problem_fingerprint(p),
                                raw_tasks=sum(len(c['tasks']) for _, c in columns), audited=True, replay=True))
    assert len(records) == 2 and all(r['raw_tasks'] == 40 for r in records)
    write(OUT/'original_projections.json', records); return contexts


def main():
    assert not (OUT/'summary.json').exists(); begun = time.perf_counter()
    manifest = read(OUT/'prepared_manifest.json')
    for p, h in manifest['source_sha256'].items(): assert sha(OUT/'source_snapshot'/p) == h
    for p, rec in manifest['prior_input_start_result_parameters_logs'].items(): assert sha(OUT/p) == rec['sha256']
    tiny = tiny_validation(); problems = original_projections()
    write(OUT/'validation_checkpoint.json', dict(status='TINY_AND_ORIGINAL_PROJECTIONS_COMPLETE_BEFORE_HISTORIES', tiny=tiny))
    records = []; runs = []
    for file in sorted((OUT/'frozen_histories').glob('*/*/*/result.json')):
        batch, case = file.parts[-4:-2]; p = problems[batch, case]; result = read(file)
        assert result['problem_sha256'] == problem_fingerprint(p)
        run_records = []
        for h in result['history']:
            if h['phase'] != 2: continue
            out = repair(p, h, result['maximum_trips'])
            assert out['raw_objective'] >= out['fleet_only_raw']-1e-6
            assert abs(out['fleet_only_raw']-h['margin']-h['corrected_e2_bound']) < 1e-5
            rec = dict(batch=batch, case=case, run=file.parent.name, iteration=h['iteration'],
                       original_corrected=h['corrected_e2_bound'], restricted_objective=h['restricted_objective'],
                       rho=h['pricing_lower_bounds'], pricing_complete=h['pricing_complete'], **out)
            records.append(rec); run_records.append(rec)
        assert run_records
        runs.append(dict(batch=batch, case=case, run=file.parent.name, phase2_records=len(run_records),
                         baseline_e2=result['e2_bound'], repaired_e2=max(0.0, *(r['corrected'] for r in run_records)),
                         best_raw=max(r['raw_objective'] for r in run_records)))
    assert len(runs) == 16
    summary = dict(status='OFFLINE_MECHANISM_COMPLETE', tiny=tiny, original_large_projections=2,
                   original_tasks_per_large_projection=40, frozen_runs=len(runs), all_phase2_records=len(records),
                   positive_repaired_runs=sum(r['repaired_e2'] > 1e-6 for r in runs),
                   default=False, production_edits=[], actual_seconds=time.perf_counter()-begun,
                   limitation='Archived rho used conditionally; no newly independently certified large bound or same-budget speedup. All runs/iterations retained; no timing rerun.')
    write(OUT/'all_history_records.json', records); write(OUT/'runs.json', runs); write(OUT/'summary.json', summary)
    print(json.dumps(summary)); print(json.dumps(runs))


if __name__ == '__main__': main()
