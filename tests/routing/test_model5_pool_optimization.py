"""M5 optimizes N with fixed batches, using the same economic contract as M6–M8."""
from copy import deepcopy
from dataclasses import replace
import json
import random

import pytest

from models.common.cycle.audit import audit_solution
from models.common.cycle.fixed_pool import FixedBatchPoolInstance
from models.common.cycle.instance import load_instance
from models.cost.comparison import evaluate_comparison_cost
from models.routing.model_5.alns.evaluator import evaluate, initial_state
from models.routing.model_5.alns.solver import solve_alns
from models.routing.model_5.exact.solver import solve_exact
from tests.routing.test_multibatch_cycle import fixture
from tools.configuration.loader import load_json, load_routing_case
from tools.data.build_model6_demo_suite import synthetic_profile


def problem(*, cleaning=5, profile=None, **bounds):
    fixed = fixture(pool=2, cleaning=cleaning)
    return FixedBatchPoolInstance(fixed.network, fixed.orders, fixed.operating_policy,
                                  bounds, profile or synthetic_profile(5))


@pytest.fixture
def exact_available():
    gp = pytest.importorskip('gurobipy')
    try:
        model = gp.Model()
        model.dispose()
    except gp.GurobiError as exc:
        pytest.skip(str(exc))


@pytest.mark.parametrize('method', ['exact', 'alns'])
@pytest.mark.parametrize('cleaning,expected_pool', [(5, 1), (25, 2)])
def test_pool_depends_on_actual_reuse_timing(method, cleaning, expected_pool, exact_available):
    p = problem(cleaning=cleaning)
    result = (solve_exact(p, time_limit=5) if method == 'exact' else
              solve_alns(p, max_time=.5, max_iterations=100))
    audited = audit_solution(p, result)
    assert audited['valid'], audited['errors']
    assert audited['periodic_replay']['valid']
    assert result['container_pool_size'] == expected_pool
    assert result['objective_mode'] == 'cost'
    assert result['info']['objective'] == pytest.approx(p.cost_profile.evaluate(audited, expected_pool)['total'])
    assert result['total_distance'] == 44
    assert result['batches'] == [dict(id=b.id, start=b.start, cutoff=b.cutoff, end=b.end,
                                      container_demand=p.batch_demands[b.id]) for b in p.operating_policy.batches]
    if method == 'exact':
        assert result['info']['proven_optimal']


def test_candidate_schedules_change_pool_and_cost_before_search_acceptance():
    profile = load_json('config/experiments/benchmark27_v1/economic.json')
    p = problem(profile=profile)
    early = initial_state(p, random.Random(42))
    early_score, early_result, issues = evaluate(p, early)
    assert not issues
    late = deepcopy(early)
    late['e2'][0]['return_after'] = 31  # Miss b1's cutoff, reuse waits until b2.
    late_score, late_result, issues = evaluate(p, late)
    assert not issues
    assert early_result['container_pool_size'] == 1
    assert late_result['container_pool_size'] == 2
    assert late_score - early_score == pytest.approx(5)


def test_infeasible_search_proposals_are_rejected_without_audit_exceptions():
    p = problem()
    state = initial_state(p, random.Random(42))
    state['e2'][0]['return_after'] = p.operating_policy.cycle_end + 100
    score, _, issues = evaluate(p, state)
    assert issues and score >= 1e6


@pytest.mark.parametrize('compact', [False, True])
def test_exact_cost_replay_includes_loading_prep_work_trips_and_physical_vehicles(compact, exact_available):
    from models.routing.model_5.exact.formulation import build_model
    from models.routing.model_5.exact.solution import extract_solution
    from models.common.cycle.state import empty_result
    from tests.routing.test_model6_joint import joint_fixture
    p = joint_fixture()
    # Nonzero loading/preparation durations exercise occupied-time accounting.
    policy = replace(p.operating_policy, e1_load_time=1, e2_dispatch_prep_time=1)
    # Two distinct customers on one delivery batch require nonzero internal
    # route arcs, including the shared arcs in the compressed formulation.
    orders = [replace(o, batch='b1') for o in p.orders]
    p = FixedBatchPoolInstance(p.network, orders, policy, {}, synthetic_profile(5))
    model, ctx = build_model(p, compress_multibatch_routes=compact)
    try:
        model.Params.TimeLimit = 5
        model.optimize()
        assert model.SolCount
        result = empty_result(p, 'exact')
        extract_solution(p, model, ctx, result)
        audit = audit_solution(p, result)
        assert audit['valid'], audit['errors']
        assert model.ObjVal == pytest.approx(audit['objective'])
        assert result['cost_components']['e1_work'] > 0
        assert result['cost_components']['e2_vehicles'] > 0
        assert audit['distances']['e2'] == 4
    finally:
        model.dispose()


def test_bounds_limit_pool_and_audit_rejects_tampered_decisions():
    p = problem(pool_min=2, pool_max=2)
    result = solve_alns(p, max_time=.2, max_iterations=50)
    assert result['audit']['valid'] and result['container_pool_size'] == 2
    saved = json.loads(json.dumps(result))
    assert audit_solution(p, saved)['valid']
    for field, value in [('container_pool_size', 1), ('economic_profile_hash', 'wrong'),
                         ('total_distance', 0), ('cost_components', {})]:
        bad = deepcopy(saved)
        bad[field] = value
        assert not audit_solution(p, bad)['valid'], field
    comparable = evaluate_comparison_cost(p, result, p.cost_profile.config)
    assert comparable['pool_basis'] == 'optimized_pool' and comparable['cost_is_optimized']
    assert 'schedule_minimum_pool_reference' not in comparable


def test_cli_case_loads_cost_mode_without_fixing_reference_pool():
    cfg, fleet = load_routing_case('data/formal_benchmark27_one_box_v1/model_5/small/S01/case.json')
    p = load_instance(cfg, fleet)
    assert p.optimize_pool and p.pool_min == 1 and p.pool_max == p.total
    assert cfg['container_pool_size'] == p.total  # Frozen historical value is reference only.
    cfg['pool_options'] = dict(pool_min=2, pool_max=3)
    bounded = load_instance(cfg, fleet)
    assert (bounded.pool_min, bounded.pool_max) == (2, 3)


def test_cli_economic_override_upgrades_a_legacy_case(monkeypatch):
    from cli import multibatch
    from cli.route import main
    monkeypatch.setattr(multibatch, 'run_case', lambda cfg, fleet, **kw: load_instance(cfg, fleet))
    p = main(['--case', 'data/multi_batch/small/mechanism_forced_late/b3/case.json',
              '--economic-config', 'config/experiments/benchmark27_v1/economic.json'])
    assert p.optimize_pool


def test_missing_economic_profile_is_explicit_and_legacy_mode_is_opt_in():
    cfg, fleet = load_routing_case('config/experiments/examples/multibatch_demo.json')
    cfg.pop('economic_config')
    with pytest.raises(ValueError, match='M5 requires economic_config'):
        load_instance(cfg, fleet)
    legacy = load_instance({**cfg, 'objective_mode': 'distance'}, fleet)
    assert not getattr(legacy, 'optimize_pool', False)
