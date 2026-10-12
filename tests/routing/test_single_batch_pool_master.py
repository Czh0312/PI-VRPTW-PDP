"""Integer E1 load equivalence and raw scheduling counterexamples.

Every pinned assignment/tour combination is compared with direct whole-cargo
arithmetic, including infeasible combinations, rather than only optimums.
"""
from copy import deepcopy
from dataclasses import replace
import itertools
import math
import time

import pytest

from models.common.cycle import MultiBatchInstance
from models.common.cycle.audit import audit_solution
from models.routing.model_1.exact.single_batch_columns import DeliveryMaster, solve_with_route_bound
from models.routing.model_1.exact.single_batch_pool_master import DeliveryPoolMaster
from models.routing.model_1.exact.single_batch_route_pool import route_pool_incumbent
from tests.routing.test_single_batch_route_pool import full_pool, physical_case
from tests.routing.test_single_batch_solver_audit import tiny_problem, enumerate_tiny


@pytest.mark.parametrize('model', [1, 2])
@pytest.mark.parametrize('vehicles,weight,volume', [(1, 20, 20), (2, 3.5, 20), (2, 20, 1.5), (2, 2, 1)])
def test_every_assignment_and_e1_selection_matches_whole_cargo_oracle(model, vehicles, weight, volume):
    gp = pytest.importorskip('gurobipy')
    p = tiny_problem(model, 1)
    p.fleet.e1_count, p.fleet.e1_weight_cap, p.fleet.e1_volume_cap = vehicles, weight, volume
    # Multiple orders of one customer must travel together through E1. E2
    # coverage is omitted to isolate the E1 subproblem for exhaustive pinning.
    orders = list(p.orders)+[replace(p.orders[0], id='duplicate', goods_weight=.25)]
    p = MultiBatchInstance(p.network, orders, p.operating_policy, model=model)
    pool = full_pool(p)
    contexts = {s: {**ctx, 'nodes': []} for s, ctx in pool['contexts'].items()}
    masters = [klass(p, pool['domains'], contexts, pool['tours'])
               for klass in (DeliveryMaster, DeliveryPoolMaster)]
    try:
        for master in masters:
            master.phase_two()
            for v in list(master.assignment.values())+list(master.active.values())+master.alpha:
                v.VType = gp.GRB.BINARY
            master.model.update()
        customers = sorted(pool['domains'])
        choices = itertools.product(*(pool['domains'][c] for c in customers))
        subsets = [indices for n in range(vehicles+1)
                   for indices in itertools.combinations(range(len(pool['tours'])), n)]
        for owners in choices:
            owner = dict(zip(customers, owners))
            for selected in subsets:
                tours = [pool['tours'][i] for i in selected]
                visits = [s for t in tours for s in t['sites']]
                eligible = (set(visits) == set(owner.values()) and len(visits) == len(set(visits))
                            and all((c, s) in next(t for t in tours if s in t['sites'])['permitted']
                                    for c, s in owner.items()))
                expected = eligible and all(
                    math.fsum(o.goods_weight+o.count*p.fleet.container_weight for o in p.orders
                              if owner[o.customer] in t['sites']) <= weight+1e-6
                    and math.fsum(o.count*p.fleet.container_volume for o in p.orders
                                  if owner[o.customer] in t['sites']) <= volume+1e-6 for t in tours)
                for master in masters:
                    for (c, s), variable in master.assignment.items():
                        variable.LB = variable.UB = int(owner[c] == s)
                    for s, variable in master.active.items():
                        variable.LB = variable.UB = int(s in owner.values())
                    for i, variable in enumerate(master.alpha):
                        variable.LB = variable.UB = int(i in selected)
                    master.model.optimize()
                    assert (master.model.Status == gp.GRB.OPTIMAL) == expected, (owner, selected, expected)
                    assert master.model.Status in (gp.GRB.OPTIMAL, gp.GRB.INFEASIBLE)
        assert masters[1].model.NumVars < masters[0].model.NumVars
    finally:
        for master in masters:
            master.dispose()


@pytest.mark.parametrize('model', [1, 2])
@pytest.mark.parametrize('seed', range(6))
def test_compact_hybrid_retains_full_integer_proof_and_independent_audit(model, seed):
    pytest.importorskip('gurobipy')
    p = tiny_problem(model, seed)
    oracle = enumerate_tiny(p)
    result = solve_with_route_bound(p, time_limit=3, root_time=1, pool_time=1,
                                   pool_compact=True, dual_cuts=True, pricing_method='mip', mip_strategy='pool')
    if math.isfinite(oracle):
        assert audit_solution(p, result)['valid']
        assert result['info']['proven_optimal']
        assert result['info']['objective'] == pytest.approx(oracle)
    else:
        assert result['info']['proven_infeasible']
    assert '_route_pool' not in result['info']['route_bound_certificate']
    if 'route_pool_primal' in result['info']:
        assert result['info']['route_pool_primal']['global_proof'] is False
        assert result['info']['route_pool_primal']['formulation'] == 'compact_integer_cargo'


def test_compact_cannot_use_restricted_trip_limit_to_claim_full_infeasibility():
    pytest.importorskip('gurobipy')
    base = physical_case(service=0, prep=0, turn=0)
    orders = list(base.orders)+[replace(base.orders[0], id='c')]
    p = MultiBatchInstance(base.network, orders, replace(base.operating_policy, e2_max_trips_per_vehicle=3), model=1)
    for key in p._travel:
        p._travel[key] = 0
    pool = full_pool(p)
    original = deepcopy(pool)
    result, stats = route_pool_incumbent(p, pool, deadline=time.perf_counter()+2, compact=True)
    assert result is None and stats['status'] == 'NO_IMPROVEMENT_IN_POOL'
    assert not stats['global_proof'] and pool == original
    expanded, stats = route_pool_incumbent(p, pool, deadline=time.perf_counter()+2, compact=True, slot_limit=3)
    assert expanded is not None and audit_solution(p, expanded)['periodic_replay']['valid']
    assert not expanded['info']['proven_optimal'] and expanded['info']['best_bound'] is None
