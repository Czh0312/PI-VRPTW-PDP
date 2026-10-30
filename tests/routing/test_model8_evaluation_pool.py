"""M8 pool sizing must match a full replay and retain infeasible penalties."""
from copy import deepcopy
from types import SimpleNamespace

import pytest

from models.common.cycle.audit import audit_solution
from models.common.cycle.calendar import CalendarCycleInstance
from models.common.cycle.joint import lift_result
from models.routing.model_8.alns import evaluator as ev
from tests.routing.test_model6_joint import joint_fixture


def sample():
    base = joint_fixture(zero=True, cleaning=5, pool_rate=30)
    parent = CalendarCycleInstance(base.network, base.orders, base.operating_policy,
        {'batch_count_candidates': [2]}, base.cost_profile.config, model=8)
    p = parent.fixed_calendar(2)
    assignment = {'o1': 'b1', 'o2': 'b2'}
    state = dict(deliveries={b.id: [['S']] for b in p.operating_policy.batches},
        pickup_order={b.id: ['S'] for b in p.operating_policy.batches},
        pickup_vehicle={b.id: {'S': 0} for b in p.operating_policy.batches},
        e2=[dict(satellite='S', vehicle=0, tasks=[('o1', 'd'), ('o1', 'p')]),
            dict(satellite='S', vehicle=0, tasks=[('o2', 'd'), ('o2', 'p')])],
        cutoffs=dict(p.default_cutoffs))
    return p, assignment, state


def full_replay_oracle(p, assignment, state):
    """The original two-replay evaluation, independent of the sizing shortcut."""
    cutoffs = p.validated_cutoffs(state.get('cutoffs'))
    fixed = p.project(assignment, p.total, cutoffs)
    _, raw, issues = ev.evaluate(fixed, state)
    fallback = (raw['info'].get('objective') or 0.) * max(
        p.cost_profile.rates['e1_distance_cost_per_km'],
        p.cost_profile.rates['e2_distance_cost_per_km'], 1.)
    penalty = ev.JointEvaluator(p).penalty
    if issues:
        return fallback + penalty * len(issues), None, tuple(issues)
    ev._delay_departures(fixed, raw)
    physical = audit_solution(fixed, raw)
    if not physical['valid']:
        return (fallback + penalty * max(1, len(physical['errors'])), None,
                tuple(e['code'] for e in physical['errors']))
    pool = max(p.pool_min, physical['minimum_pool_for_schedule'])
    cost = p.cost_profile.evaluate(physical, pool)['total']
    if pool > p.pool_max:
        return cost + penalty * (pool-p.pool_max), None, ('POOL_BOUNDS',)
    candidate = lift_result(p, raw, assignment, pool, 'alns', cutoffs)
    return ((candidate['info']['objective'], candidate, ()) if candidate['audit']['valid']
            else (cost + penalty, None, ('JOINT_AUDIT_FAILED',)))


def test_valid_shortcut_matches_full_replay_and_keeps_one_two_cycle_audit(monkeypatch):
    from models.common.cycle import audit
    p, assignment, state = sample()
    expected = full_replay_oracle(p, assignment, deepcopy(state))
    assert expected[1] is not None and expected[1]['container_pool_size'] == 1
    calls = []
    original = audit._audit
    def observe(*args, **kwargs):
        calls.append(args[2])
        return original(*args, **kwargs)
    monkeypatch.setattr(audit, '_audit', observe)
    evaluator = ev.JointEvaluator(p)
    actual = evaluator(assignment, state)
    assert actual == expected
    assert calls == [True]
    assert evaluator.single_audit_accepts == 1
    assert actual[1]['audit']['periodic_replay']['valid']


@pytest.mark.parametrize('ready,expected', [(9, 1), (10, 1), (11, 2)])
def test_same_time_clean_release_is_available_for_loading(ready, expected):
    p = SimpleNamespace(operating_policy=SimpleNamespace(cleaning_duration=3,
        batches=[SimpleNamespace(id='b1', start=0), SimpleNamespace(id='b2', start=10)]),
        batch_demands={'b1': 1, 'b2': 1})
    raw = {'e1_trips': [{'return_complete': ready-3, 'events': [{'kind': 'p', 'quantity': 1}]}]}
    assert ev._required_pool(p, raw) == expected


@pytest.mark.parametrize('change', ['late_return', 'missing_task', 'duplicate_task', 'bad_vehicle'])
def test_infeasible_neighbor_has_the_original_penalty(change):
    p, assignment, state = sample()
    if change == 'late_return':
        state['e2'][0]['return_after'] = p.operating_policy.cycle_end+1
    elif change == 'missing_task':
        state['e2'][0]['tasks'].pop()
    elif change == 'duplicate_task':
        state['e2'][0]['tasks'].append(state['e2'][0]['tasks'][0])
    else:
        state['e2'][0]['vehicle'] = -1
    assert ev.JointEvaluator(p)(assignment, deepcopy(state)) == full_replay_oracle(p, assignment, state)


def test_failed_sizing_proposal_uses_original_evaluation(monkeypatch):
    p, assignment, state = sample()
    expected = full_replay_oracle(p, assignment, deepcopy(state))
    monkeypatch.setattr(ev, '_required_pool', lambda *args: 0)
    evaluator = ev.JointEvaluator(p)
    assert evaluator(assignment, state) == expected
    assert evaluator.reference_fallbacks == 1


def test_full_audit_rejects_bad_quantities_after_fast_sizing(monkeypatch):
    p, assignment, state = sample()
    original = ev.evaluate
    def corrupt(*args, **kwargs):
        score, raw, issues = original(*args, **kwargs)
        pickup = next(e for trip in raw['e1_trips'] for e in trip['events'] if e['kind'] == 'p')
        pickup['quantity'] += 1
        return score, raw, issues
    monkeypatch.setattr(ev, 'evaluate', corrupt)
    expected = full_replay_oracle(p, assignment, deepcopy(state))
    evaluator = ev.JointEvaluator(p)
    actual = evaluator(assignment, state)
    assert actual == expected and actual[1] is None
    assert evaluator.reference_fallbacks == 1


def test_configured_minimum_pool_is_preserved():
    p, assignment, state = sample()
    p.pool_min = 2
    expected = full_replay_oracle(p, assignment, deepcopy(state))
    actual = ev.JointEvaluator(p)(assignment, state)
    assert actual == expected and actual[1]['container_pool_size'] == 2


def test_pool_upper_bound_keeps_original_penalized_response():
    p, assignment, state = sample()
    # Batch-one empties are picked up before its end, but finish cleaning
    # after batch two loads; both boxes are still needed simultaneously.
    state['cutoffs']['b1'] = p.operating_policy.batches[0].end-2
    p.pool_max = 1
    expected = full_replay_oracle(p, assignment, deepcopy(state))
    actual = ev.JointEvaluator(p)(assignment, state)
    assert actual == expected and actual[1] is None
    assert actual[2] == ('POOL_BOUNDS',)


def test_refined_physical_clock_preserves_cost_and_inventory():
    from dataclasses import replace
    from models.common.cycle.joint import JointBatchInstance
    p, assignment, state = sample()
    factor = 3
    policy = p.operating_policy
    fields = ['cycle_start', 'cycle_end', 'cleaning_duration', 'e1_load_time',
              'e2_dispatch_prep_time', 'e2_return_unload_time', 'depot_return_unload_time',
              'e1_turnaround_time', 'e2_turnaround_time']
    changes = {key: getattr(policy, key)*factor for key in fields}
    changes.update(time_tick_subdivision=policy.time_tick_subdivision*factor,
        batches=tuple(replace(b, start=b.start*factor, cutoff=b.cutoff*factor, end=b.end*factor)
                      for b in policy.batches))
    orders = [replace(o, delivery_window=tuple(t*factor for t in o.delivery_window),
                      pickup_window=tuple(t*factor for t in o.pickup_window),
                      service_time=o.service_time*factor, min_use_time=o.min_use_time*factor,
                      release_time=None if o.release_time is None else o.release_time*factor)
              for o in p.orders]
    refined = JointBatchInstance(p.network, orders, replace(policy, **changes), {}, p.cost_profile.config)
    refined._travel = {key: value*factor for key, value in p._travel.items()}
    refined_state = deepcopy(state)
    refined_state['cutoffs'] = {bid: cutoff*factor for bid, cutoff in state['cutoffs'].items()}
    expected = full_replay_oracle(refined, assignment, deepcopy(refined_state))
    actual = ev.JointEvaluator(refined)(assignment, refined_state)
    assert actual == expected and actual[1] is not None
    original = ev.JointEvaluator(p)(assignment, state)
    assert actual[0] == pytest.approx(original[0])
    assert actual[1]['container_pool_size'] == original[1]['container_pool_size']


def test_larger_instances_keep_original_evaluation_path(monkeypatch):
    from dataclasses import replace
    from models.common.cycle.joint import JointBatchInstance
    p, _, _ = sample()
    orders = [replace(p.orders[i % 2], id=f'many_{i}', batch='b1' if i < 5 else 'b2')
              for i in range(10)]
    many = JointBatchInstance(p.network, orders,
        replace(p.operating_policy, container_pool_size=10), {}, p.cost_profile.config)
    assignment = {o.id: o.batch for o in orders}
    batches = many.operating_policy.batches
    state = dict(deliveries={b.id: [['S']] for b in batches},
        pickup_order={b.id: ['S'] for b in batches},
        pickup_vehicle={b.id: {'S': 0} for b in batches}, e2=[], cutoffs=dict(many.default_cutoffs))
    for batch in batches:
        members = [o.id for o in orders if o.batch == batch.id]
        state['e2'].append(dict(satellite='S', vehicle=0,
            tasks=[(oid, kind) for kind in ['d', 'p'] for oid in members]))
    expected = full_replay_oracle(many, assignment, deepcopy(state))
    assert expected[1] is not None
    def forbidden(*args):
        raise AssertionError('Large-instance quality guard must bypass sizing shortcut')
    monkeypatch.setattr(ev, '_required_pool', forbidden)
    evaluator = ev.JointEvaluator(many)
    actual = evaluator(assignment, state)
    assert actual == expected and evaluator.single_audit_accepts == 0
