"""Independent capacity pricing and full-schedule conditional projections."""
from copy import deepcopy
import math
import random
import time

import pytest

from models.routing.common.cycle import MultiBatchInstance, Order
from models.routing.common.cycle.audit import audit_solution
from models.routing.common.domain.instance import ProblemInstance
from models.routing.common.domain.node import Depot, Satellite, Customer
from models.routing.solvers.exact.multibatch_route_pricing import RoutePricer, route_context
from models.routing.solvers.exact.multibatch_route_cg import route_cover_certificate, site_route_cover
from models.routing.solvers.exact.multibatch_conditional_inventory import (
    build_conditional_inventory, conditional_context, set_conditional_projection, solve_conditional_inventory,
)
from models.routing.variants.model_5.exact import solve_hybrid
from tests.routing.test_multibatch_cycle import fixture, hand_solution, policy_config
from tests.routing.test_multibatch_route_cg import enumerate_trips
from tests.routing.test_multibatch_departure_loads import nonmetric_problem


def capacity_problem():
    nodes = {'D': Depot('D', 0, 0), 'S': Satellite('S', 0, 0)}
    orders = []
    for oid in ('A', 'B', 'C'):
        nodes[oid] = Customer(oid, 1, 0, 0, 0, (0, 125), (0, 125), 'S')
        orders.append(Order(oid, oid, 'S', 'b1', 1, 0, (0, 25), (0, 29), 0, 0))
    fleet = dict(E1_vehicle_count=1, vehicles_per_satellite=2, E1_Qk=10, E1_Ck=10,
                 E2_Qv=2, E2_Cv=2, container_weight=1, container_volume=1)
    cfg = policy_config()
    cfg.update(container_pool_size=3, e2_max_trips_per_vehicle=1)
    return MultiBatchInstance(ProblemInstance(nodes, fleet), orders, cfg)


@pytest.mark.parametrize('phase', [False, True])
def test_capacity_and_subset_prices_match_independent_elementary_trip_oracle(phase):
    pytest.importorskip('gurobipy')
    problem = capacity_problem()
    context, oracle = route_context(problem, 'S'), enumerate_trips(problem, 'S')
    subsets = {0: frozenset([('A', 'd'), ('B', 'd')]), 1: frozenset([('B', 'd'), ('C', 'd')])}
    rng = random.Random(441)
    pricer = RoutePricer(problem, context, subset_keys=subsets)
    try:
        for _ in range(10):
            pi = {n['key']: rng.uniform(-10, 20) for n in context['nodes']}
            nu, prize = -rng.uniform(0, 5), rng.uniform(0, 12)
            subset_prices = {s: rng.uniform(0, 10) for s in subsets}
            expected = min((0 if phase else cost)-math.fsum(pi[k] for k in seq)-nu
                -(prize if any(k == 'd' for _, k in seq) else 0)
                -math.fsum(subset_prices[s] for s, keys in subsets.items() if keys.intersection(seq))
                for seq, cost in oracle)
            priced = pricer.price(pi, nu, time_limit=5, phase_one=phase,
                                  category_duals={'b1': prize}, subset_duals=subset_prices, reuse_budget=True)
            assert priced['optimal'] and priced['reduced_cost'] == pytest.approx(expected, abs=1e-6)
            assert priced['lower_bound'] <= expected+1e-7
    finally:
        pricer.dispose()


def test_pricing_reuses_leftover_time_only_for_unfinished_categories_and_keeps_best_bounds():
    from types import SimpleNamespace
    p=capacity_problem();ctx=route_context(p,'S')
    class FakePricer:
        def __init__(self, finish):
            self.context=dict(nodes=ctx['nodes']);self.subset_flags={}
            self.model=SimpleNamespace(NumVars=1);self.calls=0;self.finish=finish
        def set_prices(self,*args):pass
        def price(self,*args,**kw):
            self.calls+=1
            optimal=self.finish or self.calls>1
            return dict(status=2 if optimal else 9,optimal=optimal,infeasible=False,
                lower_bound=-2 if optimal else -8,raw_lower_bound=-2 if optimal else -8,
                margin=0,reduced_cost=None,column=None,runtime=.001,work=.001)
    pricer=RoutePricer.__new__(RoutePricer)
    slow,fast=FakePricer(False),FakePricer(True)
    pricer.groups=[('b1',slow),(None,fast)]
    report=pricer.price({},time_limit=.1,reuse_budget=True,category_duals={'b1':3})
    assert slow.calls==2 and fast.calls==1
    assert report['optimal'] and report['lower_bound']==-5
    assert next(g for g in report['groups'] if g['category']=='b1')['budget_resumed']


def test_rounded_capacity_floor_closes_known_fractional_gap_without_claiming_full_optimality():
    pytest.importorskip('gurobipy')
    from models.routing.solvers.exact.solver import solve_exact
    problem = capacity_problem()
    full = solve_exact(problem, time_limit=5)
    assert full['info']['proven_optimal'] and full['info']['objective'] == pytest.approx(4)
    bounds = []
    for cuts in (False, True):
        cert = route_cover_certificate(problem, time_limit=5, batch_pricing=True, dual_repair=True,
                                      delivery_count_cuts=cuts, heuristic_pricing=False)
        assert cert['status'] == 'LP_CONVERGED' and not cert['proven_optimal']
        bounds.append(cert['sites'][0]['bound'])
    assert bounds == pytest.approx([3, 4], abs=1e-4)
    # Hybrid enumeration must not silently discard requested rows.
    hybrid = route_cover_certificate(problem, time_limit=5, pricing_backend='hybrid', delivery_count_cuts=True)
    assert hybrid['sites'][0]['pricing_backend'] == 'milp' and hybrid['sites'][0]['bound'] > 3.99


def test_invalid_subset_is_rejected_before_model_build_and_integer_cover_is_explicit():
    p, context = capacity_problem(), route_context(capacity_problem(), 'S')
    for subset in [[('missing', 'd')], [('A', 'p')], []]:
        with pytest.raises(ValueError, match='known delivery'):
            site_route_cover(p, context, deadline=0, subset_cuts=[subset])
    with pytest.raises(ValueError, match='MILP'):
        route_cover_certificate(p, pricing_backend='hybrid', integer_cover_time_limit=1, delivery_count_cuts=True)


@pytest.mark.parametrize('seed', [0, 2, 5])
def test_conditional_full_domain_floors_project_nonmetric_full_optima(seed):
    gp = pytest.importorskip('gurobipy')
    from models.routing.solvers.exact.solver import solve_exact
    p = nonmetric_problem(seed)
    initial = solve_exact(p, time_limit=5)
    assert initial['info']['proven_optimal'] and audit_solution(p, initial)['valid']
    site_bounds, conditions = {}, []
    for site in p.satellites:
        base = site_route_cover(p, route_context(p, site), deadline=time.perf_counter()+3,
                               batch_pricing=True, dual_repair=True)
        site_bounds[site] = base['bound']
    for trip in initial['e1_trips']:
        for event in trip['events']:
            if event['kind'] != 'd':
                continue
            threshold = event['start']+p.service(event['site_id'])
            context = conditional_context(p, event['site_id'], trip['batch_id'], threshold)
            leaf = site_route_cover(p, context, deadline=time.perf_counter()+3,
                                    batch_pricing=True, dual_repair=True, delivery_count_cuts=True)
            conditions.append(dict(site=event['site_id'], batch=trip['batch_id'], threshold=threshold, bound=leaf['bound']))
    model, ctx = build_conditional_inventory(p, site_bounds, conditions)
    try:
        set_conditional_projection(p, ctx, initial, fix=True)
        model.optimize()
        assert model.Status == gp.GRB.OPTIMAL
        assert model.ObjVal == pytest.approx(initial['info']['objective'], abs=1e-6)
    finally:
        model.dispose()
    cert = solve_conditional_inventory(p, site_bounds, conditions, initial_result=initial,
                                      deadline=time.perf_counter()+3)
    assert cert['bound'] <= initial['info']['objective']+1e-6 and not cert['proven_optimal']


def test_same_site_conditions_are_maximum_floors_not_sum_and_exact_threshold_activates():
    gp = pytest.importorskip('gurobipy')
    p, initial = fixture(pool=2), hand_solution(fixture(pool=2))
    conditions = [dict(site='S', batch='b1', threshold=10, bound=2),
                  dict(site='S', batch='b2', threshold=70, bound=3)]
    model, ctx = build_conditional_inventory(p, {'S': 1}, conditions)
    try:
        model.optimize()
        assert model.Status == gp.GRB.OPTIMAL and model.ObjVal == pytest.approx(43)
        assert len(ctx['conditional_rows']) == 2
        set_conditional_projection(p, ctx, initial, fix=True)
        model.optimize()
        assert model.Status == gp.GRB.OPTIMAL and model.ObjVal == pytest.approx(44)
    finally:
        model.dispose()


def test_public_fresh_entry_returns_audited_target_and_shared_total_deadline(tmp_path):
    pytest.importorskip('gurobipy')
    out = solve_hybrid(fixture(pool=2), time_limit=3, alns_time_limit=.05,
        primal_time_limit=.5, cover_time_limit=1, conditional_time_limit=.5, diagnostics_dir=tmp_path)
    assert out['runtime'] < 3.25 and out['target_reached'] and out['gap'] <= .05
    assert audit_solution(fixture(pool=2), out['result'])['valid']
    assert not out['proven_optimal'] and not out['proven_infeasible']
    assert out['upper_bound'] <= audit_solution(fixture(pool=2), out['initial_result'])['objective']


def test_no_cover_budget_preserves_audited_result_and_reports_unmet_target(monkeypatch):
    import models.routing.service as service
    p, initial = fixture(pool=2), hand_solution(fixture(pool=2))
    before = deepcopy(initial)
    monkeypatch.setattr(service, 'solve_routing', lambda **kwargs: deepcopy(initial))
    out = solve_hybrid(p, time_limit=1e-12, primal_time_limit=0, cover_time_limit=0, conditional_time_limit=0)
    assert initial == before and audit_solution(p, out['result'])['valid']
    assert out['bound'] == 0 and out['gap'] == 1 and not out['target_reached']
    assert out['status'] == 'FEASIBLE_TARGET_NOT_REACHED'


def test_adaptive_finish_reprices_a_trivial_valid_partial_floor_and_preserves_full_projection(tmp_path):
    pytest.importorskip('gurobipy')
    from models.routing.solvers.exact.multibatch_hybrid import finish_hybrid
    from models.routing.solvers.exact.multibatch_route_pricing import evaluate_column
    from models.routing.solvers.exact.solver import solve_exact
    p, initial = fixture(pool=2), hand_solution(fixture(pool=2))
    before = deepcopy(initial)
    context = route_context(p, 'S')
    # Zero is independently valid for this nonnegative-distance problem;
    # its status deliberately requests continuation of an unfinished stage.
    row = dict(satellite='S', bound=0.0, status='TIME_LIMIT', lp_converged=False,
        columns=[evaluate_column(p, context, [(e['order_id'], e['kind']) for e in t['events']])
                 for t in initial['e2_trips']])
    ref = dict(bound=0.0, e1_bound=0.0, sites=[row], subset_runs=[],
        components=[dict(satellite='S', bound=0.0, route_cover_bound=0.0, distance_bound=0.0)])
    saved = deepcopy(ref)
    out = finish_hybrid(p, ref, None, initial, deadline=time.perf_counter()+3,
                        fallback_bound=0.0, diagnostics_dir=tmp_path)
    optimum = solve_exact(p, time_limit=3)
    assert optimum['info']['proven_optimal'] and optimum['info']['objective'] == 44
    assert out['bound'] <= 44 and out['target_reached'] and out['runtime'] < 3.25
    assert out['sites'][0]['certificate']['lp_converged'] and out['masters'][0]['certificate']['relaxation_optimal']
    assert audit_solution(p, out['result'])['valid'] and initial == before and ref == saved


def test_adaptive_shares_time_prioritizes_certification_deficit_and_builds_one_joint_master(monkeypatch):
    from models.routing.solvers.exact.multibatch_hybrid import finish_hybrid
    import models.routing.solvers.exact.multibatch_route_cg as cg
    import models.routing.solvers.exact.multibatch_route_pricing as pricing
    import models.routing.solvers.exact.multibatch_conditional_inventory as inventory
    import models.routing.solvers.exact.multibatch_route_pool as pool
    p, initial = fixture(pool=2), hand_solution(fixture(pool=2))
    rows = [dict(satellite=s, bound=0, lp_converged=False, columns=[],
                 iterations=[dict(phase='distance', master_objective=v)])
            for s, v in [('S1', 30), ('S2', 90), ('S3', 60)]]
    ref = dict(bound=0, e1_bound=0, sites=rows, subset_runs=[],
               components=[dict(satellite=r['satellite'], bound=0) for r in rows])
    original = deepcopy(ref)
    calls, masters = [], []
    monkeypatch.setattr(pricing, 'route_context', lambda p, s: dict(satellite=s))
    def price(p, ctx, **kw):
        calls.append((ctx['satellite'], kw['deadline']-time.perf_counter()))
        return dict(satellite=ctx['satellite'], bound=1, lp_converged=True, columns=[], iterations=[])
    monkeypatch.setattr(cg, 'site_route_cover', price)
    def joint(p, floors, rows, **kw):
        masters.append(dict(floors))
        return dict(bound=3)
    monkeypatch.setattr(inventory, 'solve_conditional_inventory', joint)
    monkeypatch.setattr(pool, 'reoptimize_route_pool', lambda p, r, **kw: r)
    out = finish_hybrid(p, ref, None, initial, deadline=time.perf_counter()+100, fallback_bound=0)
    assert [s for s, _ in calls] == ['S2', 'S3', 'S1']
    assert 28 < calls[0][1] < 29 and 42 < calls[1][1] < 43
    assert masters == [{'S1': 1, 'S2': 1, 'S3': 1}]
    assert len(out['masters']) == 1 and ref == original


def test_adaptive_keeps_weaker_run_columns_for_primal_and_warms_from_earlier_subset_run(monkeypatch):
    from models.routing.solvers.exact.multibatch_hybrid import finish_hybrid
    from models.routing.solvers.exact.multibatch_route_pricing import evaluate_column
    import models.routing.solvers.exact.multibatch_route_cg as cg
    import models.routing.solvers.exact.multibatch_route_pool as pool
    p, initial = fixture(pool=2), hand_solution(fixture(pool=2))
    ctx = route_context(p, 'S')
    a, b = [evaluate_column(p, ctx, [(e['order_id'], e['kind']) for e in t['events']])
            for t in initial['e2_trips'][:2]]
    row = dict(satellite='S', bound=1, lp_converged=False, columns=[a], iterations=[])
    ref = dict(bound=1, e1_bound=0, sites=[row], components=[dict(satellite='S', bound=1)],
               subset_runs=[dict(satellite='S', cuts=[], certificate=dict(columns=[b]))])
    seeds, candidates = [], []
    def price(p, ctx, **kw):
        seeds.extend(kw['initial_result']['e2_trips'])
        return dict(satellite='S', bound=0, lp_converged=False, columns=[b], iterations=[])
    monkeypatch.setattr(cg, 'site_route_cover', price)
    def primal(p, r, **kw):
        candidates.extend(kw['additional_columns']['S'])
        return r
    monkeypatch.setattr(pool, 'reoptimize_route_pool', primal)
    out = finish_hybrid(p, ref, None, initial, deadline=time.perf_counter()+1, fallback_bound=1)
    assert len(seeds) == 2 and b in candidates
    assert out['reference_certificate']['sites'][0]['bound'] == 1


def test_global_columns_rebuild_costs_unlock_full_slots_and_keep_pool_bound_local():
    pytest.importorskip('gurobipy')
    from models.routing.solvers.exact.multibatch_route_pool import candidate_pools, reoptimize_route_pool
    from tests.routing.test_multibatch_joint_exchanges import full_slot_fixture
    p, initial = full_slot_fixture()
    before = deepcopy(initial)
    proposals = {'S': [dict(tasks=[(o, 'd') for o in ids]+[(o, 'p') for o in reversed(ids)], cost=-100)
                       for ids in [('A', 'B'), ('C', 'E')]]}
    proposals['S'] += [dict(tasks=[('missing', 'd')]),
                      dict(tasks=[(o, 'd') for o in ('A', 'B', 'C', 'E')])]
    pools = candidate_pools(p, initial, max_columns=1, transfers=False, additional_columns=proposals)
    for trip, rows in zip(initial['e2_trips'], pools):
        assert rows[0]['tasks'] == tuple((e['order_id'], e['kind']) for e in trip['events'])
        assert len(rows) == 3 and all(c['cost'] >= 0 for c in rows)
    result = reoptimize_route_pool(p, initial, time_limit=3, max_columns=1, transfers=False,
                                  additional_columns=proposals)
    assert result['info']['objective'] == pytest.approx(8)
    assert audit_solution(p, result)['valid'] and initial == before
    assert result['info']['best_bound'] is None and not result['info']['proven_optimal']


@pytest.mark.parametrize('value', [-1, math.inf, math.nan])
def test_public_invalid_budgets_and_thresholds(value):
    p = fixture(pool=2)
    for name in ('time_limit', 'alns_time_limit', 'primal_time_limit', 'cover_time_limit', 'conditional_time_limit', 'target_gap'):
        with pytest.raises(ValueError):
            solve_hybrid(p, **{name: value})
    with pytest.raises(ValueError):
        conditional_context(p, 'S', 'b1', value)
