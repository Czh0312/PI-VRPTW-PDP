"""M3 directly optimizes the same economic contract as M5–M8."""
from copy import deepcopy
from dataclasses import replace
import json
import random
import subprocess
import sys

import pytest

from models.common.cycle.audit import audit_solution
from models.common.cycle.instance import load_instance
from models.common.cycle.single_batch_cost import SingleBatchCostInstance
from models.cost.comparison import evaluate_comparison_cost
from models.routing.model_3.alns.evaluator import evaluate, initial_state
from models.routing.model_3.alns.performance.single_batch_cache import SingleBatchCache
from models.routing.service import solve_routing
from tests.routing.test_single_batch_solver_audit import tiny_problem
from tools.configuration.loader import PROJECT_ROOT, load_json, load_routing_case
from tools.data.build_model6_demo_suite import synthetic_profile


def cost_problem(*, general=False):
    base = tiny_problem(3, 1)
    profile = (synthetic_profile(5) if general else
               load_json('config/experiments/benchmark27_v1/economic.json'))
    return SingleBatchCostInstance(base.network, base.orders, base.operating_policy, profile)


@pytest.mark.parametrize('method', ['exact', 'alns'])
@pytest.mark.parametrize('general', [False, True])
def test_direct_cost_solve_matches_hand_arithmetic_and_two_cycles(method, general):
    p = cost_problem(general=general)
    r = solve_routing(model=3, method=method, problem=p,
                      alns_time=.5, alns_iter=100, exact_time=10)
    a = audit_solution(p, r)
    assert a['valid'], a['errors']
    assert a['periodic_replay']['valid']
    assert r['objective_mode'] == 'cost'
    assert r['total_distance'] == pytest.approx(sum(a['distances'].values()))
    assert r['container_pool_size'] == p.total
    if general:
        expected = (sum(a['distances'].values()) + 5*p.total + .5*p.total
                    + .1*sum(a['time_metrics']['working_minutes'].values())
                    + 10*a['e1_vehicle_count'] + 5*a['e2_vehicle_count']
                    + a['e1_trip_count'] + a['e2_trip_count'])
        assert r['cost_components']['e1_work'] > 0
        assert r['cost_components']['e2_vehicles'] > 0
    else:
        expected = .1*a['distances']['e1'] + .05*a['distances']['e2'] + 5.02*p.total
    assert r['info']['objective'] == pytest.approx(expected)
    priced = evaluate_comparison_cost(p, r, p.cost_profile.config)
    assert priced['cost_is_optimized'] and priced['route_objective_mode'] == 'cost'
    assert priced['pool_basis'] == 'configured_pool'
    assert 'schedule_minimum_pool_reference' not in priced
    if method == 'exact':
        assert r['info']['proven_optimal']
        assert r['info']['best_bound'] == pytest.approx(expected)


@pytest.mark.parametrize('compact', [False, True])
def test_shared_internal_arcs_and_refined_clock_match_physical_cost(compact):
    from models.common.cycle.state import empty_result
    from models.routing.model_3.exact.formulation import build_model
    from models.routing.model_3.exact.solution import extract_solution

    base = tiny_problem(3, 1)
    # The same physical minutes represented on a 20-second clock.
    policy = replace(base.operating_policy, time_tick_subdivision=3,
        cycle_end=base.operating_policy.cycle_end*3,
        batches=tuple(replace(b, start=b.start*3, cutoff=b.cutoff*3, end=b.end*3)
                      for b in base.operating_policy.batches),
        cleaning_duration=base.operating_policy.cleaning_duration*3,
        e1_load_time=3, e2_dispatch_prep_time=3,
        e2_return_unload_time=3, depot_return_unload_time=3)
    orders = [replace(o, delivery_window=tuple(t*3 for t in o.delivery_window),
                      pickup_window=tuple(t*3 for t in o.pickup_window),
                      service_time=o.service_time*3, min_use_time=o.min_use_time*3)
              for o in base.orders]
    p = SingleBatchCostInstance(base.network, orders, policy, synthetic_profile())
    # Preserve the source's already-rounded travel durations when refining.
    p._travel = {key: value*3 for key, value in base._travel.items()}
    m, ctx = build_model(p, compress_routes=compact)
    try:
        m.Params.TimeLimit = 10
        m.optimize()
        assert m.SolCount
        r = empty_result(p, 'exact')
        extract_solution(p, m, ctx, r)
        a = audit_solution(p, r)
        assert a['valid'], a['errors']
        assert m.ObjVal == pytest.approx(a['objective'])
        assert ctx['compressed_e2_routes'] == compact
    finally:
        m.dispose()


def test_m3_matches_m8_single_batch_when_its_cutoff_and_pool_are_fixed():
    from models.common.cycle.calendar import CalendarCycleInstance

    base = tiny_problem(3, 2)
    orders = [replace(o, release_time=0, allowed_batch_ids=('b1',)) for o in base.orders]
    policy = replace(base.operating_policy,
        batches=(replace(base.operating_policy.batches[0], end=base.operating_policy.cycle_end),))
    profile = synthetic_profile()
    m8 = CalendarCycleInstance(base.network, orders, policy,
        dict(batch_count_candidates=[1]), profile, model=8)
    r8 = solve_routing(model=8, method='exact', problem=m8, exact_time=10)
    assert r8['audit']['valid'] and r8['info']['proven_optimal']
    selected = m8.selected_problem(r8)
    fixed = selected.project(r8['order_batch_assignment'], r8['container_pool_size'], r8['batch_cutoffs'])
    m3 = SingleBatchCostInstance(fixed.network, fixed.orders, fixed.operating_policy, profile)
    r3 = solve_routing(model=3, method='exact', problem=m3, exact_time=10)
    assert r3['audit']['valid'] and r3['info']['proven_optimal']
    assert r3['info']['objective'] == pytest.approx(r8['info']['objective'])
    assert r3['economic_profile_hash'] == r8['economic_profile_hash']


def test_cached_evaluation_prices_candidates_before_search_acceptance():
    p = cost_problem(general=True)
    state = initial_state(p, random.Random(42))
    score, decoded, issues = evaluate(p, state)
    assert not issues
    cached = SingleBatchCache(p)
    cached_score, cached_result, cached_issues = cached(state)
    assert cached_score == pytest.approx(score)
    assert cached_result['cost'] == decoded['cost'] and not cached_issues
    assert cached_score == decoded['info']['objective']
    assert cached_score != decoded['total_distance']
    invalid = deepcopy(state)
    invalid['e2'][0]['return_after'] = p.operating_policy.cycle_end + 100
    assert cached(invalid)[2]


@pytest.mark.parametrize('field,value', [
    ('total_distance', 0), ('container_pool_size', 1), ('economic_profile_hash', 'wrong'),
    ('objective_mode', 'distance'), ('cost_components', {}), ('cost', {})])
def test_cost_audit_rejects_tampering(field, value):
    p = cost_problem()
    r = solve_routing(model=3, method='alns', problem=p, alns_time=.3, alns_iter=0)
    assert r['audit']['valid']
    bad = json.loads(json.dumps(r))
    bad[field] = value
    assert not audit_solution(p, bad)['valid']
    changed_price = {**p.cost_profile.config, 'e1_distance_cost_per_km': 2}
    with pytest.raises(ValueError, match='same economic profile'):
        evaluate_comparison_cost(p, r, changed_price)


def test_explicit_case_and_cli_cost_modes_preserve_the_distance_default(monkeypatch):
    from cli import multibatch
    from cli.route import main

    cfg, fleet = load_routing_case('config/experiments/examples/single_batch_cost_demo.json')
    assert load_instance(cfg, fleet).cost_objective
    cfg.pop('economic_config')
    with pytest.raises(ValueError, match='M3 cost objective requires economic_config'):
        load_instance(cfg, fleet)
    with pytest.raises(ValueError, match='M3 objective_mode'):
        load_instance({**cfg, 'objective_mode': 'other'}, fleet)
    monkeypatch.setattr(multibatch, 'run_case', lambda cfg, fleet, **kw: load_instance(cfg, fleet))
    base = ['--case', 'config/experiments/examples/single_batch_demo.json']
    # The active demo may explicitly select cost. The API default without an
    # objective flag and the CLI's explicit distance override remain distance.
    default_cfg, default_fleet = load_routing_case(base[1])
    default_cfg.pop('objective_mode', None)
    assert not getattr(load_instance(default_cfg, default_fleet), 'cost_objective', False)
    assert not getattr(main(base + ['--objective-mode', 'distance']), 'cost_objective', False)
    priced = base + ['--economic-config', 'config/experiments/benchmark27_v1/economic.json']
    assert main(priced).cost_objective
    assert not getattr(main(priced + ['--objective-mode', 'distance']), 'cost_objective', False)


def test_cost_alns_keeps_solver_and_model_import_isolation():
    from tests.routing.test_solver_isolation import SCRIPT

    script = SCRIPT.replace(
        "problem = load_instance({**config, 'model': number}, fleet)",
        "problem = load_instance({**config, 'model': number, 'objective_mode': 'cost', "
        "'economic_config': 'config/experiments/benchmark27_v1/economic.json'}, fleet)")
    result = subprocess.run([sys.executable, '-c', script, 'alns', '3'],
                            cwd=PROJECT_ROOT, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
