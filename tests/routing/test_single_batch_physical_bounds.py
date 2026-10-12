"""Independent local path enumeration plus fixed full-schedule projections."""
from copy import deepcopy
from dataclasses import replace
import itertools
import math

import pytest

from models.common.cycle import MultiBatchInstance
from models.common.cycle.audit import audit_solution
from models.routing.model_3.exact import single_batch_physical_bounds as physical
from models.routing.model_3.exact.single_batch_columns import problem_fingerprint
from models.routing.model_3.exact.single_batch_formulation import build_model
from models.routing.model_3.exact.engine import _set_start, solve_exact
from tests.routing.test_single_batch_solver_audit import tiny_problem, enumerate_tiny


def one_trip_oracle(p, site):
    """Raw path enumeration with independent Floyd times, never MILP helpers."""
    nodes = list(p.network.nodes)
    short = {(a, b): p.travel(a, b) for a in nodes for b in nodes}
    for k in nodes:
        for i in nodes:
            for j in nodes:
                short[i, j] = min(short[i, j], short[i, k]+short[k, j])
    pol, fleet, batch = p.operating_policy, p.fleet, p.operating_policy.batches[0]
    orders = [o for o in p.orders if o.satellite == site]
    tasks = [(o.id, k) for o in orders for k in ('d', 'p')]
    supply = batch.start+pol.e1_load_time+short[p.depot, site]+p.service(site)
    best = math.inf
    for sequence in itertools.permutations(tasks):
        if any(sequence.index((o.id, 'p')) < sequence.index((o.id, 'd')) for o in orders):
            continue
        weight = sum(p.task(o.id, 'd')[1] for o in orders)
        volume = sum(p.task(o.id, 'd')[2] for o in orders)
        if weight > fleet.e2_weight_cap+1e-6 or volume > fleet.e2_volume_cap+1e-6:
            continue
        clock = max(pol.cycle_start, supply+pol.e2_dispatch_prep_time)
        previous, delivered, cost, valid = site, {}, 0.0, True
        for oid, kind in sequence:
            o, w, v = p.task(oid, kind)
            low, high = o.delivery_window if kind == 'd' else o.pickup_window
            clock = max(clock+p.travel(previous, o.customer), low)
            if kind == 'p':
                clock = max(clock, delivered[oid]+o.service_time+o.min_use_time)
            if clock > high or (kind == 'd' and clock+o.service_time > batch.cutoff):
                valid = False
                break
            if kind == 'd':
                delivered[oid] = clock
            weight += w*(1 if kind == 'p' else -1)
            volume += v*(1 if kind == 'p' else -1)
            if not (-1e-6 <= weight <= fleet.e2_weight_cap+1e-6 and -1e-6 <= volume <= fleet.e2_volume_cap+1e-6):
                valid = False
                break
            cost += p.distance(previous, o.customer)
            clock += o.service_time
            previous = o.customer
        returned = clock+p.travel(previous, site)+pol.e2_return_unload_time
        if valid and returned <= batch.cutoff and returned+pol.e2_turnaround_time <= pol.cycle_end:
            best = min(best, cost+p.distance(previous, site))
    return best


def problem(seed, duplicate):
    p = tiny_problem(3, seed)
    if duplicate:
        p = MultiBatchInstance(p.network, [p.orders[0], replace(p.orders[1],
            satellite=p.orders[0].satellite, customer=p.orders[0].customer)]+list(p.orders[2:]),
            p.operating_policy, model=3)
    return p


@pytest.mark.parametrize('seed', [0, 1, 4, 5])
@pytest.mark.parametrize('duplicate', [False, True])
@pytest.mark.parametrize('compressed', [False, True])
def test_physical_site_matches_independent_complete_single_trip_paths(seed, duplicate, compressed):
    gp = pytest.importorskip('gurobipy')
    p = problem(seed, duplicate)
    before = problem_fingerprint(p)
    for site in sorted({o.satellite for o in p.orders}):
        expected = one_trip_oracle(p, site)
        model, context = physical.build_physical_site(p, site, compress_routes=compressed)
        try:
            model.Params.TimeLimit = 3
            model.optimize()
            if math.isinf(expected):
                assert model.Status == gp.GRB.INFEASIBLE
            else:
                assert model.Status == gp.GRB.OPTIMAL
                assert model.ObjVal == pytest.approx(expected, abs=1e-6)
            assert context['local_problem'].network is p.network
            assert context['local_problem'].operating_policy == p.operating_policy
        finally:
            model.dispose()
    assert problem_fingerprint(p) == before


@pytest.mark.parametrize('seed', [0, 1, 4, 5])
@pytest.mark.parametrize('compressed', [False, True])
def test_all_original_paths_clocks_and_costs_project_and_floor_does_not_double_count(seed, compressed):
    gp = pytest.importorskip('gurobipy')
    p = tiny_problem(3, seed)
    initial = solve_exact(p, time_limit=5)
    assert initial['info']['proven_optimal']
    expected = enumerate_tiny(p)
    assert initial['info']['objective'] == pytest.approx(expected)
    cert = physical.physical_site_bounds(p, time_limit=4, initial_result=initial)
    assert cert['bound'] <= expected+1e-6
    assert not cert['proven_optimal'] and not cert['proven_infeasible']
    for site in {o.satellite for o in p.orders}:
        model, context = physical.build_physical_site(p, site, compress_routes=compressed)
        try:
            projected = physical.project_original_site(p, context, initial, fix=True)
            model.optimize()
            assert model.Status == gp.GRB.OPTIMAL
            assert model.ObjVal == pytest.approx(projected['cost'], abs=1e-6)
            assert cert['sites'][site]['bound'] <= projected['cost']+1e-6
        finally:
            model.dispose()
    model, context = build_model(p, compress_routes=compressed)
    try:
        physical.apply_physical_bounds(model, p, context, cert)
        assert _set_start(p, model, context, initial)
        model.update()
        for var in model.getVars():
            if abs(var.Start) < 1e90:
                var.LB = var.UB = var.Start
        model.optimize()
        assert model.Status == gp.GRB.OPTIMAL
        assert model.ObjVal == pytest.approx(expected)
    finally:
        model.dispose()


@pytest.mark.parametrize('seed', [0, 1, 2, 4, 5])
def test_original_complete_hybrid_matches_independent_integer_enumeration(seed):
    p = tiny_problem(3, seed)
    before = problem_fingerprint(p)
    result = physical.solve_exact_with_physical_bounds(p, time_limit=6)
    assert result['info']['proven_optimal']
    assert result['info']['objective'] == pytest.approx(enumerate_tiny(p))
    assert audit_solution(p, result)['valid']
    assert result['audit']['periodic_replay']['valid']
    assert problem_fingerprint(p) == before


@pytest.mark.parametrize('compressed', [False, True])
def test_cross_trip_physical_schedule_projects_without_splitting_vehicles(compressed):
    from tests.routing.test_single_batch_primal import split_problem
    from models.routing.model_3.exact.initial_solution import construct_incumbent
    import time
    p = split_problem()
    start = construct_incumbent(p, time.perf_counter()+2)
    assert start and len(start['e2_trips']) > 1
    before = deepcopy(start)
    for site in {o.satellite for o in p.orders}:
        model, ctx = physical.build_physical_site(p, site, compress_routes=compressed)
        try:
            record = physical.project_original_site(p, ctx, start, fix=True)
            model.optimize()
            assert model.Status == 2
            assert model.ObjVal == pytest.approx(record['cost'])
            assert len({r['vehicle'] for r in ctx['e2']}) == p.fleet.e2_per_sat_count
        finally:
            model.dispose()
    assert start == before


def test_tiny_budget_keeps_audited_initial_and_cannot_promote_a_proof():
    p = tiny_problem(3, 0)
    initial = solve_exact(p, time_limit=2)
    result = physical.solve_exact_with_physical_bounds(p, time_limit=1e-8, initial_result=initial)
    assert audit_solution(p, result)['valid']
    assert not result['info']['proven_optimal']
    assert not result['info']['proven_infeasible']
    assert result['info']['objective'] == initial['info']['objective']


@pytest.mark.parametrize('budget', [0, -1, math.inf, math.nan, True])
def test_bad_budgets(budget):
    with pytest.raises(ValueError, match='budget'):
        physical.physical_site_bounds(tiny_problem(3, 0), time_limit=budget)


def test_scope_identity_and_finite_certificate_guards():
    with pytest.raises(ValueError, match='M3'):
        physical.physical_site_bounds(tiny_problem(4, 0))
    p = tiny_problem(3, 0)
    cert = physical.physical_site_bounds(p, time_limit=2)
    model, context = build_model(p)
    try:
        bad = deepcopy(cert)
        bad['problem_sha256'] = 'changed'
        with pytest.raises(ValueError, match='match'):
            physical.apply_physical_bounds(model, p, context, bad)
        bad = deepcopy(cert)
        next(iter(bad['sites'].values()))['bound'] = math.inf
        with pytest.raises(ValueError, match='finite'):
            physical.apply_physical_bounds(model, p, context, bad)
    finally:
        model.dispose()
