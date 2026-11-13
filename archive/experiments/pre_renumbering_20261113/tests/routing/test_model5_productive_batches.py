"""M5 fixes M8's assignment/calendar while retaining productive batches."""
from copy import deepcopy
from dataclasses import replace
import json
import random

import pytest

from models.common.cycle.audit import audit_solution
from models.common.cycle.fixed_pool import FixedBatchPoolInstance, attach_pool_cost
from models.common.cycle.precheck import check_necessary_conditions
from models.routing.model_5.alns.evaluator import evaluate, initial_state
from models.routing.model_5.alns.performance.cycle_cache import CycleEvaluator
from models.routing.model_5.alns.solver import solve_alns
from tests.routing.test_model5_pool_optimization import exact_available
from tests.routing.test_productive_batches import productive_fixture, fixed_child, explicit_plan


def witness(assignment, pickups, *, pool=None):
    parent = productive_fixture(8, orders=len(assignment))
    child = fixed_child(parent)
    fixed, raw = explicit_plan(child, assignment, pickups, pool=pool)
    m5 = FixedBatchPoolInstance(fixed.network, fixed.orders, fixed.operating_policy,
                               {}, parent.cost_profile.config)
    attach_pool_cost(m5, raw, pool or m5.pool_max)
    return m5, raw


@pytest.mark.parametrize('assignment,pickups,errors', [
    ({'o0': 'b2', 'o1': 'b2'}, {'o0': 80, 'o1': 85},
     {'EMPTY_BATCH_DELIVERY', 'EMPTY_BATCH_RECOVERY'}),
    ({'o0': 'b1', 'o1': 'b2'}, {'o0': 80, 'o1': 85}, {'EMPTY_BATCH_RECOVERY'}),
])
def test_cost_audit_rejects_empty_batches_and_projection_keeps_rule(assignment, pickups, errors):
    p, result = witness(assignment, pickups)
    checked = audit_solution(p, json.loads(json.dumps(result)))
    assert not checked['valid']
    assert {e['code'] for e in checked['errors']} == errors
    for pool in range(p.pool_min, p.pool_max + 1):
        assert p.project_pool(pool).require_productive_batches


@pytest.mark.parametrize('first,second', [(10, 80), (33, 34)])
def test_fixed_assignment_allows_cross_batch_recovery_and_cutoff_equality(first, second):
    assignment = {'o0': 'b1', 'o1': 'b1', 'o2': 'b2'}
    p, result = witness(assignment, {'o0': first, 'o1': second, 'o2': 85}, pool=2)
    checked = audit_solution(p, result)
    assert checked['valid'] and checked['periodic_replay']['valid'], checked['errors']
    assert [r['quantity'] for r in checked['cutoff_snapshots']] == [1, 2]
    assert [r['demand'] for r in checked['clean_stock']] == [2, 1]
    assert {o.id: o.batch for o in p.project_pool(2).orders} == assignment
    assert check_necessary_conditions(p)['passed']


def test_precheck_rejects_empty_delivery_and_impossible_recovery_without_solving():
    p, _ = witness({'o0': 'b2', 'o1': 'b2'}, {'o0': 80, 'o1': 85})
    check = check_necessary_conditions(p)
    assert check['proven_infeasible']
    assert {e['code'] for e in check['errors']} == {
        'EMPTY_BATCH_DELIVERY', 'NO_POSSIBLE_BATCH_RECOVERY'}
    p, _ = witness({'o0': 'b1', 'o1': 'b2'}, {'o0': 80, 'o1': 85})
    p = FixedBatchPoolInstance(p.network, [replace(o, pickup_window=(80, 90)) for o in p.orders],
                               p.operating_policy, {}, p.cost_profile.config)
    assert check_necessary_conditions(p)['errors'] == [
        dict(code='NO_POSSIBLE_BATCH_RECOVERY', batch_id='b1')]
    result = solve_alns(p, max_time=.1, max_iterations=10, seed=42)
    assert result['info']['proven_infeasible'] and result['info']['objective'] is None


@pytest.mark.parametrize('cached', [False, True])
def test_decoder_rejects_empty_recovery_even_when_another_schedule_is_feasible(cached):
    p, _ = witness({'o0': 'b1', 'o1': 'b2'}, {'o0': 10, 'o1': 85})
    state = initial_state(p, random.Random(42))
    run = CycleEvaluator(p) if cached else evaluate
    assert not run(p, state)[2]
    late = deepcopy(state)
    late['e2'][0]['return_after'] = 34
    _, _, issues = run(p, late)
    assert 'EMPTY_BATCH_RECOVERY' in issues


def test_alns_retains_fixed_calendar_and_assignment_and_both_flows():
    assignment = {'o0': 'b1', 'o1': 'b2'}
    p, _ = witness(assignment, {'o0': 10, 'o1': 85})
    result = solve_alns(p, max_time=.3, max_iterations=30, seed=42)
    checked = audit_solution(p, result)
    assert checked['valid'] and checked['periodic_replay']['valid']
    assert all(r['quantity'] for r in checked['cutoff_snapshots'])
    assert {o.id: o.batch for o in p.orders} == assignment
    assert [(b['start'], b['cutoff'], b['end']) for b in result['batches']] == [
        (b.start, b.cutoff, b.end) for b in p.operating_policy.batches]


@pytest.mark.parametrize('empty_delivery', [False, True])
@pytest.mark.parametrize('compact', [False, True])
def test_direct_exact_builder_cannot_bypass_productive_constraints(empty_delivery, compact, exact_available):
    from models.routing.model_5.exact.formulation import build_model
    assignment = {'o0': 'b2' if empty_delivery else 'b1', 'o1': 'b2'}
    p, _ = witness(assignment, {'o0': 80, 'o1': 85})
    p = FixedBatchPoolInstance(p.network, [replace(o, pickup_window=(80, 90)) for o in p.orders],
                               p.operating_policy, {}, p.cost_profile.config)
    model, _ = build_model(p, compress_multibatch_routes=compact)
    try:
        model.Params.TimeLimit = 5
        model.Params.OutputFlag = 0
        model.optimize()
        assert model.Status == 3 and model.SolCount == 0
    finally:
        model.dispose()
