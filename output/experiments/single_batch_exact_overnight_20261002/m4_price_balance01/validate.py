"""Independent all-route integer clocks, shared assignment, raw schedules."""
from dataclasses import asdict, is_dataclass, replace
import math
import random
import time
from common import OUT, read, write, sha, load_case, frozen_cases
from balance import transformed_prices, price_all, explicit_dual_bound
from models.routing.common.cycle import MultiBatchInstance
from models.routing.common.cycle.audit import audit_solution
from models.routing.solvers.exact.single_batch_columns import problem_fingerprint
from models.routing.solvers.exact.single_batch_pickup_bound import pickup_contexts, project_original_schedule
from models.routing.solvers.exact.solver import solve_exact
from tests.routing.test_single_batch_pickup_bound import independent_trips, fully_enumerated_joint_lp
from tests.routing.test_single_batch_solver_audit import tiny_problem, enumerate_tiny


def plain(v):
    if is_dataclass(v): return plain(asdict(v))
    if isinstance(v, dict): return {str(k): plain(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)): return [plain(x) for x in v]
    if isinstance(v, float) and not math.isfinite(v): return {'numeric_literal': repr(v)}
    if v is None or isinstance(v, (str, int, float, bool)): return v
    return plain(vars(v))


def main():
    assert not (OUT/'validation.json').exists(); begun = time.perf_counter()
    rng = random.Random(61034); records = []; price_checks = raw_checks = projection_count = 0
    for seed in (0, 1, 4, 5):
        for duplicate in (False, True):
            base = tiny_problem(4, seed)
            p = (MultiBatchInstance(base.network, [base.orders[0], replace(base.orders[1],
                  customer=base.orders[0].customer)], base.operating_policy, model=4) if duplicate else base)
            fp = problem_fingerprint(p); domains, contexts, check = pickup_contexts(p); assert check['passed']
            routes = {s: independent_trips(p, s, ctx) for s, ctx in contexts.items()}; assert all(routes.values())
            lp = fully_enumerated_joint_lp(p, domains, contexts); integer = enumerate_tiny(p)
            assert math.isfinite(lp) and math.isfinite(integer)
            counts = {s: c['maximum_trips'] for s, c in contexts.items()}
            folder = OUT/'tiny_inputs'/f'{seed}_{int(duplicate)}'; folder.mkdir(parents=True)
            nodes = p.network.nodes
            write(folder/'business_input.json', dict(model=4, problem_sha256=fp, nodes=plain(nodes),
                orders=plain(p.orders), fleet=plain(p.fleet), policy=plain(p.operating_policy),
                distances={a: {b: p.distance(a,b) for b in nodes} for a in nodes},
                integer_travel={a: {b: p.travel(a,b) for b in nodes} for a in nodes}))
            for revision in range(3):
                pi = {}
                for s, ctx in contexts.items():
                    pi[s] = {}
                    for order in sorted({n['order'].id for n in ctx['nodes']}):
                        total = rng.uniform(-4, 35); split = rng.uniform(-60, 60)
                        pi[s][order, 'd'] = split; pi[s][order, 'p'] = total-split
                nu = {s: -rng.uniform(0, 4) for s in contexts}
                gamma = {c: min(math.fsum(pi[s][o.id,k] for o in p.orders if o.customer==c for k in ('d','p'))
                               for s in sites) for c, sites in domains.items()}
                history = dict(phase=2, prices={s: dict(tasks=[[list(k),v] for k,v in pp.items()],fleet=nu[s])
                                               for s, pp in pi.items()}, assignment_duals=gamma,
                               pricing_lower_bounds={s: 123456 for s in contexts})
                write(folder/f'prices_{revision}.json', history)
                for balanced in (False, True):
                    pp, gg = transformed_prices(p, history, balanced)
                    for s, old in pi.items():
                        for order in {k[0] for k in old}:
                            assert abs(math.fsum([pp[s][0][order,'d'],pp[s][0][order,'p']])
                                       -math.fsum([old[order,'d'],old[order,'p']])) < 1e-9
                    priced = price_all(p, contexts, history, counts, balanced=balanced, seed=42,
                                       deadline=time.perf_counter()+5, call_cap=3)
                    assert priced['pricing_complete'] and not priced['old_rho_reused']
                    minima = {}
                    for s, rr in routes.items():
                        minima[s] = min(cost-math.fsum(pp[s][0][k] for k in seq)-pp[s][1] for seq,cost in rr)
                        report = priced['pricing'][s]
                        assert report['optimal'] and abs(report['reduced_cost']-minima[s]) < 1e-6
                        assert priced['pricing_lower_bounds'][s] <= minima[s]+1e-7; price_checks += 1
                        shifted_nu = pp[s][1]+min(0, priced['pricing_lower_bounds'][s])
                        for seq,cost in rr:
                            assert cost-math.fsum(pp[s][0][k] for k in seq)-shifted_nu >= -1e-6
                            raw_checks += 1
                    for c, sites in domains.items():
                        for s in sites:
                            assert gg[c] <= math.fsum(pp[s][0][o.id,k] for o in p.orders
                                                       if o.customer==c for k in ('d','p'))+1e-7
                    exact = explicit_dual_bound(pp, gg, counts, minima)
                    assert priced['corrected_e2_bound'] <= exact['raw_corrected']+1e-6
                    assert priced['e2_bound'] <= lp+1e-6 and priced['e2_bound'] <= integer+1e-6
                    assert problem_fingerprint(p)==fp
                    records.append(dict(seed=seed,duplicate=duplicate,revision=revision,balanced=balanced,
                                        independent_joint_lp=lp,independent_original_integer=integer,
                                        exact_price_minima=minima, **priced))
            start = solve_exact(p,time_limit=3); a=audit_solution(p,start)
            assert a['valid'] and a['periodic_replay']['valid'] and start['info']['proven_optimal']
            project_original_schedule(p,start,contexts); write(folder/'original_integer_schedule.json',start)
            projection_count += 1
    write(OUT/'tiny_pricing_records.json',records)
    projections=[]
    for batch,case,folder in frozen_cases():
        p=load_case(folder);start=read(folder/'start.json');a=audit_solution(p,start)
        assert a['valid'] and a['periodic_replay']['valid']
        _,contexts,check=pickup_contexts(p);assert check['passed']
        cols=project_original_schedule(p,start,contexts);assert sum(len(c['tasks']) for _,c in cols)==40
        projections.append(dict(batch=batch,case=case,problem_sha256=problem_fingerprint(p),
            original_tasks=40,start_sha256=sha(folder/'start.json'),audited=True,replay=True,
            original_e2=a['distances']['e2'],original_upper=a['objective']))
    assert len(projections)==2;write(OUT/'original_projections.json',projections)
    summary=dict(original_tiny_cases=8,repeated_customer_cases=4,tiny_price_states=len(records),
                 complete_independent_pricing_checks=price_checks,all_raw_route_dual_checks=raw_checks,
                 original_tiny_projections=projection_count,original_large40task_projections=2,
                 old_rho_poisoned_and_never_reused=True,seconds=time.perf_counter()-begun)
    write(OUT/'validation.json',summary);print(summary)


if __name__=='__main__':main()
