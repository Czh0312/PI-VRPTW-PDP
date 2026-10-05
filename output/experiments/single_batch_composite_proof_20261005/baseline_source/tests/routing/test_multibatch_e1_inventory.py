"""Check E1 relaxation projections, stock boundaries and lower-bound semantics."""
from copy import deepcopy
from itertools import permutations
import math

import pytest

from models.routing.common.cycle import MultiBatchInstance
from models.routing.common.cycle.audit import audit_solution
from models.routing.solvers.exact.multibatch_e1_inventory import (
    build_e1_inventory, e1_inventory_certificate, set_e1_projection,
)
from models.routing.solvers.exact.multibatch_route_cg import route_cover_certificate
from models.routing.solvers.exact.multibatch_e1_routes import build_e1_routes, enumerate_e1_routes, set_route_projection
from tests.routing.test_multibatch_cycle import fixture, hand_solution
from tests.routing.test_multibatch_departure_loads import nonmetric_problem


def fix_and_check(problem, schedule, *, symmetry=True):
    gp = pytest.importorskip('gurobipy')
    audited = audit_solution(problem, schedule)
    assert audited['valid'], audited['errors']
    model, ctx = build_e1_inventory(problem, symmetry=symmetry)
    try:
        set_e1_projection(problem, ctx, schedule, fix=True)
        model.optimize()
        assert model.Status == gp.GRB.OPTIMAL
        assert model.ObjVal == pytest.approx(audited['distances']['e1'])
    finally:
        model.dispose()
    model, ctx = build_e1_routes(problem)
    try:
        set_route_projection(problem, ctx, schedule, fix=True)
        model.optimize()
        assert model.Status == gp.GRB.OPTIMAL
        assert model.ObjVal == pytest.approx(audited['distances']['e1'])
    finally:
        model.dispose()


@pytest.mark.parametrize('unloaded,pool', [(29, 1), (30, 2), (31, 2)])
@pytest.mark.parametrize('cleaning', [0, 20])
def test_full_hand_schedule_projects_at_exact_cutoff_and_clean_boundaries(unloaded, pool, cleaning):
    p = fixture(pool=pool, cleaning=cleaning)
    fix_and_check(p, hand_solution(p, unloaded))


@pytest.mark.parametrize('seed', range(6))
@pytest.mark.parametrize('backend', ['indexed', 'patterns'])
def test_nonmetric_full_optimum_projects_and_relaxation_bounds_its_e1_cost(seed, backend):
    pytest.importorskip('gurobipy')
    from models.routing.solvers.exact.solver import solve_exact
    p = nonmetric_problem(seed)
    full = solve_exact(p, time_limit=5)
    assert full['info']['proven_optimal'] and full['audit']['valid']
    fix_and_check(p, full)
    with_stock = e1_inventory_certificate(p, time_limit=5, initial_result=full, backend=backend)
    without_stock = e1_inventory_certificate(p, time_limit=5, inventory=False, backend=backend)
    assert with_stock['relaxation_optimal'] and without_stock['relaxation_optimal']
    assert without_stock['bound'] <= with_stock['bound']+1e-6
    assert with_stock['bound'] <= full['audit']['distances']['e1']+1e-6
    assert not with_stock['proven_optimal'] and not with_stock['proven_infeasible']


@pytest.mark.parametrize('seed', range(6))
def test_complete_e1_enumeration_matches_independent_mixed_task_permutations(seed):
    p = nonmetric_problem(seed)
    policy, f = p.operating_policy, p.fleet
    for batch in policy.batches:
        tasks = [(s, k) for s in p.satellites for k in ('d', 'p')
                 if (p.demands[s, batch.id] if k == 'd' else any(o.satellite == s for o in p.orders))]
        oracle = {}
        for length in range(1, len(tasks)+1):
            for sequence in permutations(tasks, length):
                delivery = tuple(s for s, k in sequence if k == 'd')
                pickup = tuple(s for s, k in sequence if k == 'p')
                if sequence != tuple((s, 'd') for s in delivery)+tuple((s, 'p') for s in pickup):
                    continue
                if (sum(p.weights[s, batch.id] for s in delivery) > f.e1_weight_cap+1e-6
                        or sum(p.demands[s, batch.id]*f.container_volume for s in delivery) > f.e1_volume_cap+1e-6
                        or len(pickup)*f.container_weight > f.e1_weight_cap+1e-6
                        or len(pickup)*f.container_volume > f.e1_volume_cap+1e-6):
                    continue
                now = batch.start+policy.e1_load_time if delivery else batch.cutoff
                previous, cost, picked, valid = p.depot, 0, False, True
                for site, kind in sequence:
                    if kind == 'p' and not picked:
                        now = max(now, batch.cutoff)
                        picked = True
                    now += p.travel(previous, site)+p.service(site)
                    cost += p.distance(previous, site)
                    previous = site
                    if kind == 'd' and now > batch.cutoff:
                        valid = False
                        break
                returned = now+p.travel(previous, p.depot)+policy.depot_return_unload_time
                if (not valid or returned > batch.end or returned+policy.e1_turnaround_time > policy.cycle_end
                        or pickup and returned+policy.cleaning_duration > policy.cycle_end):
                    continue
                oracle[delivery, pickup] = cost+p.distance(previous, p.depot), returned
        enumerated = {(r['delivery'], r['pickup']): (r['cost'], r['return_complete']) for r in enumerate_e1_routes(p, batch)}
        assert enumerated == oracle


def test_clean_inventory_rejects_too_late_reuse_without_claiming_m5_infeasibility():
    pytest.importorskip('gurobipy')
    p = fixture(cleaning=25)
    without = e1_inventory_certificate(p, time_limit=2, inventory=False)
    with_stock = e1_inventory_certificate(p, time_limit=2)
    assert without['relaxation_optimal'] and without['bound'] == pytest.approx(40, abs=1e-4)
    assert with_stock['relaxation_infeasible']
    assert with_stock['bound'] == 0 and not with_stock['proven_infeasible']


def test_old_stock_pickup_only_route_projects_without_current_supply():
    original = fixture(pool=2)
    p = MultiBatchInstance(original.network, original.orders[:1], original.operating_policy)
    r = hand_solution(original, 31)
    r['e2_trips'].pop()
    r['e1_trips'][1].update(departure=90, return_complete=110)
    r['e1_trips'][1]['events'] = [dict(kind='p', site_id='S', quantity=1, start=100)]
    r['batches'][1]['container_demand'] = 0
    fix_and_check(p, r)


def test_zero_times_and_positive_turnaround_do_not_apply_batch_vehicle_symmetry():
    p = fixture(pool=2, zero=True, cleaning=0, e1_turnaround_time=2)
    r = hand_solution(p)
    for trip in r['e1_trips']:
        b = p.batch_map[trip['batch_id']]
        trip.update(departure=b.start, return_complete=b.cutoff)
        trip['events'][0]['start'] = b.start
        trip['events'][1]['start'] = b.cutoff
    for trip in r['e2_trips']:
        trip.update(departure=p.batch_map[p.order_map[trip['events'][0]['order_id']].batch].start)
        trip['events'][0]['start'] = trip['departure']
        trip['events'][1]['start'] = trip['departure']
        trip['return_complete'] = trip['departure']
    fix_and_check(p, r)
    m, ctx = build_e1_inventory(p)
    try:
        assert not ctx['batch_symmetry']
    finally:
        m.dispose()


def test_noncanonical_vehicle_labels_are_reordered_only_with_zero_turnaround():
    p = nonmetric_problem(2)
    from models.routing.solvers.exact.solver import solve_exact
    full = solve_exact(p, time_limit=5)
    copied = deepcopy(full)
    for trip in copied['e1_trips']:
        trip['vehicle_id'] = p.fleet.e1_count-1-trip['vehicle_id']
    fix_and_check(p, copied)


def test_combined_certificate_replaces_e1_component_and_counts_total_budget(tmp_path):
    gp = pytest.importorskip('gurobipy')
    from models.routing.variants.model_5.exact import e1_inventory_certificate as public
    p = fixture(pool=1)
    base = route_cover_certificate(p, time_limit=2, initial_result=hand_solution(p))
    combined = route_cover_certificate(p, time_limit=2, e1_time_limit=1,
        initial_result=hand_solution(p), diagnostics_dir=tmp_path)
    assert combined['e1_bound'] == pytest.approx(40, abs=1e-4)
    assert combined['bound'] >= base['bound']-1e-4
    assert combined['bound'] <= 44 and combined['runtime'] < 2.25
    assert combined['e1_certificate']['relaxation_optimal']
    assert not combined['proven_optimal']
    alone = public(p, time_limit=2)
    assert alone['bound'] == pytest.approx(combined['e1_bound'])
    replay = gp.read(str(tmp_path/'e1_inventory.mps'))
    try:
        replay.Params.OutputFlag = 0
        replay.optimize()
        assert replay.Status == gp.GRB.OPTIMAL
        assert replay.ObjVal >= combined['e1_certificate']['bound']
    finally:
        replay.dispose()


def test_budget_scope_invalid_schedule_and_unfinished_bound():
    p = fixture(pool=2)
    tiny = e1_inventory_certificate(p, time_limit=1e-12)
    assert tiny['bound'] == 0 and not tiny['proven_optimal']
    for value in (0, -1, math.inf, math.nan):
        with pytest.raises(ValueError):
            e1_inventory_certificate(p, time_limit=value)
    for value in (-1, math.inf, math.nan):
        with pytest.raises(ValueError, match='e1_time_limit'):
            route_cover_certificate(p, e1_time_limit=value)
    with pytest.raises(ValueError, match='multibatch M5'):
        e1_inventory_certificate(fixture(one_batch=True))
    with pytest.raises(ValueError, match='independent audit'):
        e1_inventory_certificate(p, initial_result={})


def test_timeout_uses_objbound_never_the_relaxation_incumbent():
    pytest.importorskip('gurobipy')
    # The difficult site geometry is not needed: a zero budget suffices to
    # verify no bound is synthesized from a feasible projection or objective.
    p = fixture(pool=2)
    result = e1_inventory_certificate(p, time_limit=.001, initial_result=hand_solution(p))
    assert result['bound'] <= 40
    if result['raw_bound'] is not None:
        assert result['bound'] == max(0, result['raw_bound']-result['margin'])
