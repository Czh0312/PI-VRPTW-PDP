"""M5 distance certificates are bounds on complete cycles, not MIP proofs."""
from dataclasses import replace
from itertools import permutations
import math
import time
from unittest.mock import patch

import pytest

from models.common.cycle import MultiBatchInstance
from models.common.cycle.audit import audit_solution
from models.routing.model_5.exact.multibatch_bounds import distance_certificate, _tour_bound
from tests.routing.test_multibatch_cycle import fixture, hand_solution
from tests.routing.test_multibatch_departure_loads import nonmetric_problem


@pytest.mark.parametrize('seed', [0, 1, 2, 3, 4, 5])
def test_bound_never_exceeds_nonmetric_full_model_optimum(seed):
    pytest.importorskip('gurobipy')
    from models.routing.model_5.exact.engine import solve_exact
    problem = nonmetric_problem(seed)
    result = solve_exact(problem, time_limit=5)
    assert result['info']['proven_optimal'] and audit_solution(problem, result)['valid']
    certificate = distance_certificate(problem)
    assert 0 <= certificate['bound'] <= result['info']['objective']+1e-7
    assert result['info']['distance_certificate_bound'] == certificate['bound']
    assert result['info']['gurobi_best_bound'] == pytest.approx(result['info']['objective'])


def test_e2_component_bounded_by_all_feasible_raw_task_partitions():
    # Independently enumerate all 6! task permutations and 2^5 route splits.
    # Feasibility uses raw loads and the one-delivery-batch rule; no time or
    # exact-formulation constraints participate in the oracle.
    problem = nonmetric_problem(3)
    problem.fleet.e2_weight_cap = 6
    problem.fleet.e2_volume_cap = 3
    site = next(s for s in problem.satellites if len([o for o in problem.orders if o.satellite == s]) >= 2)
    orders = [replace(o, satellite=site) for o in problem.orders]
    problem = MultiBatchInstance(problem.network, orders, problem.operating_policy)
    tasks = [(o.id, kind) for o in problem.orders for kind in ('d', 'p')]
    component = next(r for r in distance_certificate(problem)['components'] if r.get('satellite') == site)
    feasible, minimum = 0, math.inf
    for sequence in permutations(tasks):
        for boundaries in range(1 << (len(sequence)-1)):
            routes, route = [], []
            for i, key in enumerate(sequence):
                route.append(key)
                if i == len(sequence)-1 or boundaries & (1 << i):
                    routes.append(route)
                    route = []
            distance, valid = 0.0, True
            for route in routes:
                batches = {problem.order_map[oid].batch for oid, kind in route if kind == 'd'}
                if len(batches) > 1:
                    valid = False
                    break
                weight = sum(problem.order_map[oid].goods_weight+problem.order_map[oid].count*problem.fleet.container_weight
                             for oid, kind in route if kind == 'd')
                volume = sum(problem.order_map[oid].count*problem.fleet.container_volume
                             for oid, kind in route if kind == 'd')
                if weight > 6 or volume > 3:
                    valid = False
                    break
                delivered = {oid: i for i, (oid, kind) in enumerate(route) if kind == 'd'}
                previous = site
                for i, (oid, kind) in enumerate(route):
                    order = problem.order_map[oid]
                    if kind == 'p' and oid in delivered and delivered[oid] >= i:
                        valid = False
                        break
                    amount = order.count*problem.fleet.container_weight+(order.goods_weight if kind == 'd' else 0)
                    weight += -amount if kind == 'd' else amount
                    volume += (-1 if kind == 'd' else 1)*order.count*problem.fleet.container_volume
                    if not (0 <= weight <= 6 and 0 <= volume <= 3):
                        valid = False
                        break
                    distance += problem.distance(previous, order.customer)
                    previous = order.customer
                distance += problem.distance(previous, site)
                if not valid:
                    break
            if valid:
                feasible += 1
                minimum = min(minimum, distance)
                assert component['bound'] <= distance+1e-7
    assert feasible > 0 and minimum < math.inf


def test_optional_e1_satellite_detour_uses_shortest_closure():
    from models.common.domain.node import Satellite
    from models.common.domain.instance import ProblemInstance
    base = fixture(pool=2)
    nodes = dict(base.network.nodes)
    nodes['T'] = Satellite('T', 0, 0)
    matrix = {a: {b: 0 if a == b else 100 for b in nodes} for a in nodes}
    for a, b in [('D', 'T'), ('T', 'S'), ('S', 'D'), ('S', 'C'), ('C', 'S')]:
        matrix[a][b] = 1
    fleet = dict(E1_vehicle_count=1, vehicles_per_satellite=1, E1_Qk=500, E1_Ck=500,
                 E2_Qv=500, E2_Cv=500, container_weight=1, container_volume=1)
    problem = MultiBatchInstance(ProblemInstance(nodes, fleet, distance_matrix=matrix),
                                 base.orders, base.operating_policy)
    certificate = distance_certificate(problem)
    assert all(r['bound'] <= 3 for r in certificate['components'] if r['echelon'] == 'E1')
    assert certificate['bound'] <= 10  # Two batches, each relaxed E1=3 and E2=2.


def test_large_tour_fallback_zero_distances_and_budget():
    sites = ['D']+[str(i) for i in range(13)]
    closure = {a: {b: 0 if a == b else 1 for b in sites} for a in sites}
    assert _tour_bound(closure, 'D', sites[1:], lambda: None) == 14
    zero = fixture(pool=2, zero=True)
    assert distance_certificate(zero)['bound'] == 0
    with pytest.raises(TimeoutError):
        distance_certificate(fixture(pool=2), deadline=time.perf_counter()-1)
    assert distance_certificate(fixture(pool=2, one_batch=True))['scope'] == 'not_applicable'


def test_build_timeout_preserves_audited_start_with_independent_bound():
    pytest.importorskip('gurobipy')
    from models.routing.model_5.exact import engine as solver
    problem = fixture(pool=2)
    initial = hand_solution(problem)
    initial['info']['objective'] = audit_solution(problem, initial)['objective']
    with patch.object(solver, 'construct_incumbent', return_value=None), \
            patch.object(solver, 'build_model', side_effect=TimeoutError('forced')):
        result = solver.solve_exact(problem, time_limit=2, initial_result=initial)
    assert result['info']['status'] == 'TIME_LIMIT_BUILD'
    assert not result['info']['proven_optimal'] and not result['info']['proven_infeasible']
    assert audit_solution(problem, result)['valid']
    assert result['info']['best_bound_source'] == 'distance_certificate'
    assert result['info']['gurobi_best_bound'] is None
    assert result['info']['best_bound'] == distance_certificate(problem)['bound']


def test_certificate_disabled_control_and_full_model_proof_scope():
    pytest.importorskip('gurobipy')
    from models.routing.model_5.exact.engine import solve_exact
    problem = fixture(pool=2)
    result = solve_exact(problem, time_limit=2, use_distance_certificate=False)
    assert result['distance_certificate']['scope'] == 'disabled'
    assert result['info']['proven_optimal'] and audit_solution(problem, result)['valid']
    assert result['info']['best_bound_source'] == 'gurobi'
    assert result['info']['proof_scope'] == 'full_integer_time_multibatch_indexed'
