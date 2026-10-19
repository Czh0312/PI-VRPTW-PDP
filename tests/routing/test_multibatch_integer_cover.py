"""All-path integer oracles, partial-domain guards and budget/proof separation."""
from copy import deepcopy
from types import SimpleNamespace
import gzip
import json
import math
import time

import pytest

from models.common.cycle import MultiBatchInstance, Order
from models.common.cycle.audit import audit_solution
from models.common.domain.instance import ProblemInstance
from models.common.domain.node import Depot, Satellite, Customer
from models.routing.model_5.exact.bounds.route_cover_cg.route_cg import route_cover_certificate
from models.routing.model_5.exact.bounds.route_cover_cg.route_enumeration import enumerate_route_universe
from models.routing.model_5.exact.bounds.route_cover_cg.route_integer_cover import integer_route_cover
from models.routing.model_5.exact.bounds.route_cg.pickup_pricing import route_context
from tests.routing.test_multibatch_cycle import fixture, hand_solution
from tests.routing.test_multibatch_departure_loads import nonmetric_problem
from tests.routing.test_multibatch_route_cg import enumerate_trips, enumerated_lp


def integer_oracle(problem, site, columns):
    gp = pytest.importorskip('gurobipy')
    model = gp.Model()
    model.Params.OutputFlag = 0
    model.Params.MIPGap = model.Params.MIPGapAbs = 0
    try:
        x = model.addVars(len(columns), vtype=gp.GRB.BINARY, obj={i: c for i, (_, c) in enumerate(columns)})
        for order in problem.orders:
            if order.satellite == site:
                for kind in ('d', 'p'):
                    model.addConstr(gp.quicksum(x[i] for i, (seq, _) in enumerate(columns) if (order.id, kind) in seq) == 1)
        model.addConstr(x.sum() <= problem.fleet.e2_per_sat_count*problem.operating_policy.e2_max_trips_per_vehicle)
        model.optimize()
        assert model.Status == gp.GRB.OPTIMAL
        return model.ObjVal
    finally:
        model.dispose()


def fractional_problem():
    base = fixture(pool=4)
    nodes = {'D': Depot('D', 0, 0), 'S': Satellite('S', 0, 0)}
    nodes.update({k: Customer(k, i, 0, 0, 0, (0, 125), (0, 125), 'S') for i, k in enumerate(('A', 'B', 'C'))})
    arcs = {a: {b: int(a != b) for b in nodes} for a in nodes}
    fleet = dict(E1_vehicle_count=1, vehicles_per_satellite=1, E1_Qk=500, E1_Ck=500,
                 E2_Qv=4, E2_Cv=2, container_weight=1, container_volume=1)
    network = ProblemInstance(nodes, fleet, distance_matrix=arcs, travel_time_matrix=arcs)
    orders = [Order(k.lower(), k, 'S', 'b1', 1, 1, (0, 10), (0, 25), 0) for k in ('A', 'B', 'C')]
    return MultiBatchInstance(network, orders, base.operating_policy)


@pytest.mark.parametrize('seed', range(6))
def test_integer_cover_matches_independent_all_path_mip_and_bounds_full_m5(seed):
    pytest.importorskip('gurobipy')
    from models.routing.model_5.exact.enhanced_solver import solve_exact
    p = nonmetric_problem(seed)
    cert = route_cover_certificate(p, pricing_backend='hybrid', integer_cover_time_limit=1,
                                   e1_time_limit=1, time_limit=5)
    for site in cert['sites']:
        if site['status'] == 'EMPTY':
            continue
        oracle = integer_oracle(p, site['satellite'], enumerate_trips(p, site['satellite']))
        integer = site['integer_cover']
        assert integer['relaxation_optimal'] and integer['complete_route_universe']
        assert integer['raw_bound'] == pytest.approx(oracle, abs=1e-6)
        assert oracle-1e-3 <= site['bound'] <= oracle+1e-6
        assert site['bound'] >= site['lp_bound']
        assert not integer['proven_optimal'] and not integer['proven_infeasible']
    full = solve_exact(p, time_limit=5)
    assert full['info']['proven_optimal'] and audit_solution(p, full)['valid']
    assert cert['bound'] <= full['info']['objective']+1e-6
    assert not cert['proven_optimal'] and not cert['proven_infeasible']


def test_fractional_lp_is_strictly_strengthened_by_complete_integer_cover():
    pytest.importorskip('gurobipy')
    p = fractional_problem()
    paths = enumerate_trips(p, 'S')
    assert enumerated_lp(p, 'S', paths) == pytest.approx(4.5)
    assert integer_oracle(p, 'S', paths) == pytest.approx(5)
    cert = route_cover_certificate(p, time_limit=5, pricing_backend='hybrid', integer_cover_time_limit=1)
    site = cert['sites'][0]
    assert site['lp_bound'] == pytest.approx(4.5, abs=1e-4)
    assert site['bound'] == pytest.approx(5, abs=1e-4)
    assert site['lp_converged'] and site['integer_cover']['relaxation_optimal']
    cover = [tuple(k) for c in site['integer_cover']['selected_columns'] for k in c['tasks']]
    assert len(cover) == len(set(cover)) == 6
    assert not cert['proven_optimal']


def test_full_schedule_maps_to_dominated_integer_columns_without_mutation():
    pytest.importorskip('gurobipy')
    p = fixture(pool=2)
    initial = hand_solution(p)
    before = deepcopy(initial)
    cert = route_cover_certificate(p, time_limit=5, initial_result=initial,
        pricing_backend='hybrid', integer_cover_time_limit=1)
    projected = cert['sites'][0]['integer_cover']['initial_projection']
    assert projected['exact_task_cover'] and projected['mapped_trips'] == len(initial['e2_trips'])
    assert projected['objective'] <= audit_solution(p, initial)['distances']['e2']+1e-6
    assert initial == before


def test_partial_enumeration_never_runs_restricted_integer_bound():
    pytest.importorskip('gurobipy')
    p = fixture(pool=2)
    cert = route_cover_certificate(p, time_limit=5, initial_result=hand_solution(p),
        pricing_backend='hybrid', integer_cover_time_limit=1, enumeration_max_states=1)
    site = cert['sites'][0]
    assert site['pricing_backend'] == 'milp' and not site['enumeration']['complete']
    assert site.get('integer_cover') is None
    assert cert['bound'] <= audit_solution(p, hand_solution(p))['objective']+1e-6


def test_integer_stage_deadline_keeps_lp_bound_and_rejects_partial_domains():
    gp = pytest.importorskip('gurobipy')
    p = fixture(pool=2)
    ctx = route_context(p, 'S')
    universe = enumerate_route_universe(p, ctx)
    model = gp.Model()
    model.Params.OutputFlag = 0
    try:
        model.addVars(universe['columns'])
        model.update()
        result = integer_route_cover(p, ctx, universe, model, lp_bound=3.5, deadline=0)
        assert result['bound'] == 3.5 and result['raw_bound'] is None
        assert result['status'] == 'TIME_LIMIT_BUILD' and not result['relaxation_optimal']
        partial = {**universe, 'complete': False}
        with pytest.raises(ValueError, match='complete'):
            integer_route_cover(p, ctx, partial, model, lp_bound=0, deadline=time.perf_counter()+1)
        wrong = {**universe, 'columns': universe['columns']+1}
        with pytest.raises(ValueError, match='all'):
            integer_route_cover(p, ctx, wrong, model, lp_bound=0, deadline=time.perf_counter()+1)
    finally:
        model.dispose()


def test_timed_out_integer_stage_uses_solver_lower_bound_without_incumbent():
    gp = pytest.importorskip('gurobipy')
    p = fractional_problem()
    ctx = route_context(p, 'S')
    universe = enumerate_route_universe(p, ctx)
    class TimedOut:
        NumVars, NumConstrs = universe['columns'], 7
        Status, ObjBound, SolCount, NodeCount = gp.GRB.TIME_LIMIT, 4.75, 0, 3
        Params = SimpleNamespace()
        def update(self):
            pass
        def getVars(self):
            return [SimpleNamespace() for _ in range(self.NumVars)]
        def optimize(self):
            pass
        @property
        def ObjVal(self):
            raise AssertionError('No incumbent objective is available')
    result = integer_route_cover(p, ctx, universe, TimedOut(), lp_bound=4.5,
        deadline=time.perf_counter()+1)
    assert result['raw_bound'] == 4.75 and result['bound'] == pytest.approx(4.75-4.75e-7)
    assert result['relaxed_objective'] is None and not result['relaxation_optimal']
    assert not result['proven_optimal'] and not result['proven_infeasible']


def test_negligible_integer_budget_preserves_completed_lp_certificate():
    pytest.importorskip('gurobipy')
    cert = route_cover_certificate(fractional_problem(), time_limit=5,
        pricing_backend='hybrid', integer_cover_time_limit=1e-12)
    site = cert['sites'][0]
    assert site['lp_converged'] and site['bound'] == site['lp_bound']
    assert site['integer_cover']['status'] == 'TIME_LIMIT_BUILD'
    assert cert['status'] == 'PARTIAL_INTEGER_RELAXATION_BOUND' and not cert['integer_cover_complete']


@pytest.mark.parametrize('value', [-1, math.inf, math.nan])
def test_invalid_integer_budget(value):
    with pytest.raises(ValueError, match='integer_cover_time_limit'):
        route_cover_certificate(fixture(pool=2), integer_cover_time_limit=value, pricing_backend='hybrid')


def test_integer_budget_requires_complete_enumeration_backend():
    with pytest.raises(ValueError, match='hybrid'):
        route_cover_certificate(fixture(pool=2), integer_cover_time_limit=1)


def test_default_keeps_lp_only_path():
    pytest.importorskip('gurobipy')
    cert = route_cover_certificate(fractional_problem(), time_limit=5, pricing_backend='hybrid')
    assert cert['sites'][0]['integer_cover'] is None
    assert cert['sites'][0]['bound'] == pytest.approx(4.5, abs=1e-4)


def test_public_entry_exports_full_binary_cover_and_separate_lp(tmp_path):
    gp = pytest.importorskip('gurobipy')
    from models.routing.model_5.exact import route_cover_certificate as public
    p = fractional_problem()
    cert = public(p, time_limit=5, pricing_backend='hybrid', integer_cover_time_limit=1, diagnostics_dir=tmp_path)
    binary = list(tmp_path.glob('*_integer_master.mps.gz'))
    lp = list(tmp_path.glob('*_best_pricing_master.mps.gz'))
    assert len(binary) == len(lp) == 1
    for path, vtype, objective in ((binary[0], gp.GRB.BINARY, 5), (lp[0], gp.GRB.CONTINUOUS, 4.5)):
        m = gp.read(str(path))
        try:
            assert all(v.VType == vtype for v in m.getVars())
            m.Params.OutputFlag = 0
            m.Params.MIPGap = m.Params.MIPGapAbs = 0
            m.optimize()
            assert m.Status == gp.GRB.OPTIMAL and m.ObjVal == pytest.approx(objective)
        finally:
            m.dispose()
    with gzip.open(next(tmp_path.glob('*_best_pricing.json.gz')), 'rt') as stream:
        universe = json.load(stream)
    assert universe['complete'] and universe['columns'] == cert['sites'][0]['variables']
    assert cert['sites'][0]['integer_cover']['raw_bound'] == pytest.approx(5)
