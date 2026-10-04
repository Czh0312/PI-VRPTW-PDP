"""Full-schedule projections, valid cutoff domains, and audited primal recovery."""
from copy import deepcopy
from dataclasses import replace
import math
import time
from types import SimpleNamespace

import pytest

from models.routing.common.cycle import MultiBatchInstance, Order
from models.routing.common.cycle.audit import audit_solution
from models.routing.common.cycle.state import empty_result
from models.routing.common.cycle.precheck import travel_lower_bounds
from models.routing.common.domain.instance import ProblemInstance
from models.routing.common.domain.node import Depot, Satellite, Customer
from models.routing.solvers.exact.multibatch_joint_inventory import (
    build_joint_inventory, cutoff_domains, joint_route_inventory_certificate,
    recover_joint_schedule, set_joint_projection, solve_joint_inventory,
)
from models.routing.solvers.exact.multibatch_route_enumeration import enumerate_route_universe
from models.routing.solvers.exact.multibatch_route_pricing import route_context
from tests.routing.test_multibatch_cycle import fixture, hand_solution
from tests.routing.test_multibatch_departure_loads import nonmetric_problem


def domains(problem):
    return {s: (c, enumerate_route_universe(problem, c)) for s in problem.satellites
            for c in [route_context(problem, s)] if c['nodes']}


@pytest.mark.parametrize('seed', range(6))
def test_known_nonmetric_full_optimum_projects_and_joint_bound_remains_below(seed):
    gp = pytest.importorskip('gurobipy')
    from models.routing.solvers.exact.solver import solve_exact
    p = nonmetric_problem(seed)
    initial = solve_exact(p, time_limit=5)
    assert initial['info']['proven_optimal'] and initial['audit']['valid']
    d = domains(p)
    model, ctx = build_joint_inventory(p, d)
    try:
        projection = set_joint_projection(p, ctx, initial, fix=True)
        model.optimize()
        assert model.Status == gp.GRB.OPTIMAL
        assert model.ObjVal <= initial['info']['objective']+1e-6
        assert projection['projected_e2_cost'] <= initial['audit']['distances']['e2']+1e-6
    finally:
        model.dispose()
    cert = solve_joint_inventory(p, d, deadline=time.perf_counter()+5, initial_result=initial)
    assert cert['relaxation_optimal'] and cert['bound'] <= initial['info']['objective']+1e-6
    assert not cert['proven_optimal'] and not cert['proven_infeasible']


@pytest.mark.parametrize('returned', [29, 30, 31])
@pytest.mark.parametrize('cleaning', [0, 20])
def test_full_cutoff_and_cleaning_boundary_projections(returned, cleaning):
    gp = pytest.importorskip('gurobipy')
    p = fixture(pool=2, cleaning=cleaning)
    initial = hand_solution(p, returned)
    assert audit_solution(p, initial)['valid']
    model, ctx = build_joint_inventory(p, domains(p))
    try:
        set_joint_projection(p, ctx, initial, fix=True)
        model.optimize()
        assert model.Status == gp.GRB.OPTIMAL
        assert model.ObjVal <= audit_solution(p, initial)['objective']+1e-6
    finally:
        model.dispose()


def test_later_delivery_forces_whole_trip_pickups_to_a_later_cutoff():
    p = fixture(pool=2)
    p = MultiBatchInstance(p.network, [replace(p.orders[0], pickup_window=(0, 89)), p.orders[1]], p.operating_policy)
    ctx = route_context(p, 'S')
    rows = cutoff_domains(p, ctx, enumerate_route_universe(p, ctx), travel_lower_bounds(p))
    row = next(r for r in rows if set(r['keys']) == {('o2', 'd'), ('o1', 'p')})
    assert row['allowed_cutoffs'] == ('b2',) and row['minimum_return_bound'] > p.batch_map['b1'].cutoff


def test_all_pickups_of_a_selected_route_share_one_cutoff():
    gp = pytest.importorskip('gurobipy')
    p = fixture(pool=2)
    model, ctx = build_joint_inventory(p, domains(p))
    try:
        data = ctx['routes']['S']
        i = next(i for i, r in enumerate(data['rows']) if set(r['keys']) == {('o1', 'p'), ('o2', 'p')})
        var = data['choices'][i, 'b2']
        var.LB = var.UB = 1
        first = ctx['e1']['allocation']['o1', 'b1']
        first.LB = first.UB = 1
        model.optimize()
        assert model.Status == gp.GRB.INFEASIBLE
    finally:
        model.dispose()


def test_cost_dominance_does_not_impose_the_retained_routes_actual_return_clock():
    gp = pytest.importorskip('gurobipy')
    p = fixture(pool=2)
    d = domains(p)
    # Same raw path/cost, valid but later serialization. The original full
    # schedule returns at 16 and must still be able to choose the first cutoff.
    column = next(c for g in d['S'][1]['groups'] for c in g['columns']
                  if set(c['tasks']) == {('o1', 'd'), ('o1', 'p')})
    column.update(starts=[25, 39], return_complete=40)
    model, ctx = build_joint_inventory(p, d)
    try:
        set_joint_projection(p, ctx, hand_solution(p), fix=True)
        model.optimize()
        assert model.Status == gp.GRB.OPTIMAL
    finally:
        model.dispose()


def test_incomplete_or_missing_site_universe_cannot_create_joint_certificate():
    pytest.importorskip('gurobipy')
    p = fixture(pool=2)
    d = domains(p)
    d['S'][1]['complete'] = False
    with pytest.raises(ValueError, match='complete'):
        build_joint_inventory(p, d)
    with pytest.raises(ValueError, match='every active'):
        build_joint_inventory(p, {})
    out = joint_route_inventory_certificate(p, time_limit=5, initial_result=hand_solution(p), enumeration_max_states=1)
    assert out['status'] == 'PARTIAL_ENUMERATION_SEPARATE_BOUND' and out['joint_certificate'] is None
    assert out['bound'] <= out['upper_bound'] and not out['proven_optimal']


def test_deadline_keeps_previous_bound_without_an_incumbent_objective(monkeypatch):
    gp = pytest.importorskip('gurobipy')
    import models.routing.solvers.exact.multibatch_joint_inventory as module
    class TimedOut:
        Params = SimpleNamespace()
        Status, ObjBound, SolCount, NodeCount = gp.GRB.TIME_LIMIT, 4.75, 0, 1
        NumVars = NumConstrs = NumGenConstrs = 0
        def optimize(self): pass
        def dispose(self): pass
        @property
        def ObjVal(self): raise AssertionError('No incumbent objective exists')
    monkeypatch.setattr(module, 'build_joint_inventory', lambda *a, **kw: (TimedOut(), dict(choices=[], backend='patterns')))
    out = solve_joint_inventory(fixture(pool=2), {}, deadline=time.perf_counter()+1, fallback_bound=4.5)
    assert out['raw_bound'] == 4.75 and out['bound'] == pytest.approx(4.75-4.75e-7)
    assert out['relaxation_objective'] is None and not out['relaxation_optimal']
    assert not out['proven_optimal']


def test_tiny_total_budget_returns_the_audited_initial_solution_unchanged():
    p = fixture(pool=2)
    initial = hand_solution(p)
    before = deepcopy(initial)
    out = joint_route_inventory_certificate(p, time_limit=1e-12, initial_result=initial)
    assert out['bound'] == 0 and out['result'] == initial == before
    assert out['upper_bound'] == audit_solution(p, initial)['objective'] and out['runtime'] < .1


def test_recovery_keeps_actual_schedule_and_never_uses_a_pool_bound_as_global():
    pytest.importorskip('gurobipy')
    p = fixture(pool=2)
    initial = hand_solution(p)
    before = deepcopy(initial)
    joint = solve_joint_inventory(p, domains(p), initial_result=initial, deadline=time.perf_counter()+2)
    result, info = recover_joint_schedule(p, initial, joint, deadline=time.perf_counter()+2)
    assert audit_solution(p, result)['valid'] and initial == before
    assert info['objective'] <= audit_solution(p, initial)['objective']+1e-6
    assert result['info']['best_bound'] is None and not result['info']['proven_optimal']


def test_recovery_uses_physical_indexed_e1_for_positive_turnaround():
    pytest.importorskip('gurobipy')
    p = fixture(pool=2, e1_turnaround_time=2)
    initial = hand_solution(p)
    assert audit_solution(p, initial)['valid']
    result, info = recover_joint_schedule(p, initial, dict(selected_columns=[]), deadline=time.perf_counter()+2)
    assert info['backend'] == 'indexed' and audit_solution(p, result)['valid']


def test_public_entry_same_total_budget_and_replayable_joint_exports(tmp_path):
    gp = pytest.importorskip('gurobipy')
    from models.routing.variants.model_5.exact import joint_route_inventory_certificate as public
    p = fixture(pool=2)
    out = public(p, time_limit=5, joint_time_limit=1, recovery_time_limit=1,
                 initial_result=hand_solution(p), diagnostics_dir=tmp_path)
    assert out['runtime'] < 5.25 and out['bound'] <= out['upper_bound']+1e-6
    assert audit_solution(p, out['result'])['valid'] and not out['proven_optimal']
    model = gp.read(str(tmp_path/'joint/joint_inventory.mps.gz'))
    try:
        model.Params.OutputFlag = 0
        model.optimize()
        assert model.Status == gp.GRB.OPTIMAL
        assert model.ObjVal+1e-6 >= out['joint_certificate']['raw_bound']
    finally:
        model.dispose()


@pytest.mark.parametrize('value', [-1, math.inf, math.nan])
def test_parameter_guards(value):
    p = fixture(pool=2)
    for name in ('joint_time_limit', 'recovery_time_limit', 'e1_time_limit', 'integer_cover_time_limit', 'enumeration_time_limit'):
        with pytest.raises(ValueError): joint_route_inventory_certificate(p, **{name: value})
    with pytest.raises(ValueError): joint_route_inventory_certificate(p, time_limit=value)


def test_partial_budgets_wrong_model_and_bad_initial_are_rejected():
    with pytest.raises(ValueError): joint_route_inventory_certificate(fixture(pool=2), enumeration_max_states=True)
    with pytest.raises(ValueError): joint_route_inventory_certificate(fixture(pool=2), joint_time_limit=0)
    with pytest.raises(ValueError): joint_route_inventory_certificate(fixture(one_batch=True))
    invalid = hand_solution(fixture(pool=2))
    invalid['e2_trips'].pop()
    with pytest.raises(ValueError, match='audit'): joint_route_inventory_certificate(fixture(pool=2), initial_result=invalid)


def test_reference_only_mode_and_recovery_build_deadline_preserve_feasible_upper():
    pytest.importorskip('gurobipy')
    p = fixture(pool=2)
    initial = hand_solution(p)
    out = joint_route_inventory_certificate(p, time_limit=5, initial_result=initial, joint_time_limit=0, recovery_time_limit=0)
    assert out['joint_certificate'] is None and out['result'] == initial and out['bound'] <= out['upper_bound']
    result, metadata = recover_joint_schedule(p, initial, dict(selected_columns=[]), deadline=0)
    assert result == initial and metadata['status'] == 'TIME_LIMIT_BUILD'


def test_five_active_sites_use_indexed_e1_and_keep_full_schedule_projection():
    gp = pytest.importorskip('gurobipy')
    template = fixture(pool=5, zero=True, cleaning=0)
    nodes, orders = {'D': Depot('D', 0, 0)}, []
    for i in range(5):
        site, customer = f'S{i}', f'C{i}'
        nodes[site] = Satellite(site, 0, 0)
        nodes[customer] = Customer(customer, 0, 0, 0, 0, (0, 125), (0, 125), site)
        orders.extend(replace(o, id=f'{o.id}_{i}', customer=customer, satellite=site) for o in template.orders)
    fleet = dict(E1_vehicle_count=1, vehicles_per_satellite=1, E1_Qk=500, E1_Ck=500,
                 E2_Qv=500, E2_Cv=500, container_weight=1, container_volume=1)
    p = MultiBatchInstance(ProblemInstance(nodes, fleet), orders, template.operating_policy)
    initial = empty_result(p, 'manual')
    for index, batch in enumerate(p.operating_policy.batches):
        selected = [o for o in orders if o.batch == batch.id]
        initial['e1_trips'].append(dict(trip_id=f'e1_{index}', vehicle_id=0, batch_id=batch.id,
            departure=batch.start, return_complete=batch.cutoff+1,
            events=[dict(kind='d', site_id=o.satellite, quantity=1, start=batch.start+1) for o in selected]
                 +[dict(kind='p', site_id=o.satellite, quantity=1, start=batch.cutoff) for o in selected]))
        for o in selected:
            initial['e2_trips'].append(dict(trip_id=f'e2_{o.id}', vehicle_id=0, satellite_id=o.satellite,
                trip_index=index, departure=batch.start+1, return_complete=batch.start+4,
                events=[dict(kind='d', order_id=o.id, start=batch.start+2, quantity=1),
                        dict(kind='p', order_id=o.id, start=batch.start+3, quantity=1)]))
    assert audit_solution(p, initial)['valid']
    model, ctx = build_joint_inventory(p, domains(p))
    try:
        assert ctx['backend'] == 'indexed'
        set_joint_projection(p, ctx, initial, fix=True)
        model.optimize()
        assert model.Status == gp.GRB.OPTIMAL and model.ObjVal == 0
    finally:
        model.dispose()
