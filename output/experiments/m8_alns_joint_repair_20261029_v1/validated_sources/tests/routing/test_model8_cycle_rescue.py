"""A cycle rescue must preserve already valid proposals and the shared budget."""
from copy import deepcopy
import time

from models.common.cycle.calendar import CalendarCycleInstance
from models.routing.model_8.alns.evaluator import JointEvaluator
from models.routing.model_8.alns.neighborhoods import destroy_repair
from tests.routing.test_model8_evaluation_pool import sample
from tests.routing.test_model6_joint import joint_fixture


class FixedRemoval:
    def sample(self, population, count):
        return list(population)[:count]


def checker(ev, assignment, cutoffs):
    return lambda state: ev(assignment, {**state, 'cutoffs': dict(cutoffs)})


def test_cycle_rescue_returns_an_independently_audited_repair():
    p, assignment, state = sample()
    saved = deepcopy(state)
    ev = JointEvaluator(p)
    fixed = ev.fixed(assignment)
    validate = checker(ev, assignment, state['cutoffs'])
    legacy = destroy_repair(fixed, state, FixedRemoval(), 'random', time.perf_counter()+10.)
    assert 'TIMING_CYCLE' in validate(legacy)[2]
    stats = {}
    rescued = destroy_repair(fixed, state, FixedRemoval(), 'random', time.perf_counter()+10.,
        validator=validate, repair_stats=stats)
    _, result, issues = validate(rescued)
    assert result is not None and not issues and result['audit']['periodic_replay']['valid']
    assert stats == dict(timing_repair_attempts=1, timing_repair_successes=1)
    assert state == saved and not result['info']['proven_optimal']


def test_valid_legacy_proposal_is_returned_without_an_extra_repair():
    source = joint_fixture(zero=True, one_batch=True)
    p = CalendarCycleInstance(source.network, source.orders, source.operating_policy,
        {'batch_count_candidates': [1]}, source.cost_profile.config, model=8).fixed_calendar(1)
    assignment = {o.id: 'b1' for o in p.orders}
    state = dict(deliveries={'b1': [['S']]}, pickup_order={'b1': ['S']}, pickup_vehicle={'b1': {'S': 0}},
        cutoffs=dict(p.default_cutoffs), e2=[dict(satellite='S', vehicle=0, tasks=[(o.id, 'd'), (o.id, 'p')]) for o in p.orders])
    ev = JointEvaluator(p)
    fixed = ev.fixed(assignment)
    validate = checker(ev, assignment, state['cutoffs'])
    old = destroy_repair(fixed, state, FixedRemoval(), 'random', time.perf_counter()+10.)
    assert validate(old)[1] is not None
    stats = {}
    new = destroy_repair(fixed, state, FixedRemoval(), 'random', time.perf_counter()+10., validator=validate, repair_stats=stats)
    assert new == old and not stats


def test_failed_rescue_retains_the_original_proposal():
    p, assignment, state = sample()
    fixed = JointEvaluator(p).fixed(assignment)
    old = destroy_repair(fixed, state, FixedRemoval(), 'random', time.perf_counter()+10.)
    stats = {}
    new = destroy_repair(fixed, state, FixedRemoval(), 'random', time.perf_counter()+10.,
        validator=lambda trial: (1e6, None, ('TIMING_CYCLE',)), repair_stats=stats)
    assert new == old and stats == {'timing_repair_attempts': 1}


def test_expired_budget_keeps_a_complete_incumbent_without_validation():
    p, assignment, state = sample()
    fixed = JointEvaluator(p).fixed(assignment)
    def forbidden(state):
        raise AssertionError('Expired construction must not spend another validation budget')
    result = destroy_repair(fixed, state, FixedRemoval(), 'random', 0., validator=forbidden)
    result['cutoffs'] = dict(state['cutoffs'])
    assert result == state
