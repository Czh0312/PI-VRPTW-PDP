"""Enumerate all relaxed trip partitions, not only an optimal solution."""
from copy import deepcopy
import itertools
import math

import pytest

from models.routing.common.cycle.audit import audit_solution
from models.routing.solvers.exact.single_batch_columns import root_route_bound, solve_with_route_bound
from models.routing.solvers.exact.single_batch_dual_cuts import apply_dual_cuts
from models.routing.solvers.exact.single_batch_formulation import build_model
from tests.routing.test_single_batch_columns import contexts, independently_enumerated_trips
from tests.routing.test_single_batch_comparison import problem as one_customer
from tests.routing.test_single_batch_route_pool import physical_case
from tests.routing.test_single_batch_solver_audit import tiny_problem, enumerate_tiny


def partitions(keys, routes):
    if not keys:
        yield []
        return
    first = min(keys)
    for route in routes:
        covered = set(route['tasks'])
        if first in covered and covered <= keys:
            for tail in partitions(keys-covered, routes):
                yield [route]+tail


def check_all_partitions(p, certificate):
    domains, travel, sites = contexts(p)
    catalogs = {s: list(independently_enumerated_trips(p, ctx)) for s, ctx in sites.items()}
    customers = sorted(domains)
    checked = 0
    for choices in itertools.product(*(domains[c] for c in customers)):
        assignment = dict(zip(customers, choices))
        options = [list(partitions({o.id for o in p.orders if assignment[o.customer] == s}, catalogs[s])) for s in sites]
        for selected in itertools.product(*options):
            cost = math.fsum(c['cost'] for trips in selected for c in trips)
            count = {s: len(trips) for s, trips in zip(sites, selected)}
            for cut in certificate['dual_cuts']:
                rhs = math.fsum(coefficient for site, customer, coefficient in cut['assignment_terms']
                                if assignment[customer] == site)
                rhs += math.fsum(coefficient*count[site] for site, coefficient in cut['trip_terms'])-cut['margin']
                assert rhs <= cost+1e-8, (cut, assignment, selected)
                checked += 1
    return checked


@pytest.mark.parametrize('model', [1, 2])
@pytest.mark.parametrize('seed', range(8))
@pytest.mark.parametrize('method', ['label', 'mip'])
def test_dual_planes_hold_for_every_assignment_and_trip_partition(model, seed, method):
    p = tiny_problem(model, seed)
    r = root_route_bound(p, time_limit=3, dual_cuts=True, pricing_method=method)
    if not r['proven_infeasible']:
        assert r['dual_cuts']
        assert check_all_partitions(p, r) > 0


def test_duplicate_orders_and_multiple_physical_trips_obey_the_same_planes():
    for p in [one_customer(2, duplicate=True, window=(0, 5), pool=2), physical_case(service=9, prep=3, turn=4)]:
        r = root_route_bound(p, time_limit=2, dual_cuts=True)
        assert r['dual_cuts']
        assert check_all_partitions(p, r) > 0


def test_interrupted_pricing_planes_are_corrected_before_transfer():
    p = tiny_problem(2, 1)
    r = root_route_bound(p, time_limit=2, dual_cuts=True, label_limit=1)
    assert r['dual_cuts'] and not r['relaxation_complete']
    assert check_all_partitions(p, r) > 0


@pytest.mark.parametrize('model', [1, 2])
@pytest.mark.parametrize('compressed,shared', [(False, False), (False, True), (True, True)])
def test_e2_distance_definition_counts_every_directed_arc_once(model, compressed, shared):
    p = tiny_problem(model, 1)
    r = root_route_bound(p, time_limit=2, dual_cuts=True)
    m, ctx = build_model(p, compress_routes=compressed, share_e2=shared)
    try:
        assert apply_dual_cuts(m, p, ctx, r) == len(r['dual_cuts'])
        m.Params.TimeLimit = 3
        m.optimize()
        assert m.Status == 2
        assert m.ObjVal == pytest.approx(enumerate_tiny(p))
        e2_distance = m.getVarByName('B1_dual_E2_distance').X
        # Count shared arcs independently from the selected variable names.
        selected = {}
        for route in ctx['e2']:
            for (left, right), v in route['x'].items():
                if v.X < .5:
                    continue
                a = route['base'] if left == -1 else route['nodes'][left]['order'].customer
                b = route['base'] if right == -2 else route['nodes'][right]['order'].customer
                selected[v.VarName] = p.distance(a, b)
        assert e2_distance == pytest.approx(math.fsum(selected.values()))
    finally:
        m.dispose()


@pytest.mark.parametrize('model', [1, 2])
def test_full_hybrid_with_dual_cuts_retains_original_global_proof(model):
    p = tiny_problem(model, 1)
    r = solve_with_route_bound(p, time_limit=4, root_time=1, pool_time=.5, dual_cuts=True)
    assert audit_solution(p, r)['valid'] and r['info']['proven_optimal']
    assert r['info']['objective'] == pytest.approx(enumerate_tiny(p))
    assert r['info']['single_batch_route_dual_cuts'] > 0
    assert not r['info']['route_bound_certificate']['proven_optimal']


def test_time_penalties_and_wrong_problem_cannot_transfer_dual_planes():
    p = tiny_problem(2, 1)
    with pytest.raises(ValueError, match='unpenalized'):
        root_route_bound(p, time_limit=2, time_cuts=True, dual_cuts=True)
    r = root_route_bound(p, time_limit=2, dual_cuts=True)
    m, ctx = build_model(p)
    try:
        wrong = deepcopy(r)
        wrong['problem_sha256'] = 'other'
        with pytest.raises(ValueError, match='does not match'):
            apply_dual_cuts(m, p, ctx, wrong)
    finally:
        m.dispose()
