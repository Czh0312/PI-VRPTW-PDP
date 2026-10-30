"""Full-replay oracle equivalence, inventory boundaries and failure semantics."""
from copy import deepcopy
from dataclasses import replace
import importlib.util
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]


def module(name, variant):
    path = HERE / f'sources/{variant}/models/routing/model_8/alns/evaluator.py'
    spec = importlib.util.spec_from_file_location(name, path)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


OLD = module('m8_old_evaluation_oracle', 'baseline')
NEW = module('m8_candidate_evaluation', 'candidate')


@pytest.fixture
def tiny():
    from tests.routing.test_model78_calendar import calendar_fixture
    from models.routing.model_8.alns.fixed_calendar_search import solve_alns
    from models.routing.model_8.alns.state import state_from_result
    p = calendar_fixture(8, counts=(2,)).fixed_calendar(2)
    result = solve_alns(p, max_time=.5, max_iterations=0, seed=42)
    assert result['audit']['valid']
    state = state_from_result(p, result)
    state['cutoffs'] = result['batch_cutoffs']
    return p, dict(result['order_batch_assignment']), state


def test_valid_candidate_needs_one_full_physical_replay(tiny, monkeypatch):
    from models.common.cycle import audit
    p, assignment, state = tiny
    counts = []
    original = audit._audit
    def observe(*args, **kwargs):
        counts.append((args[0].operating_policy.container_pool_size, args[2]))
        return original(*args, **kwargs)
    monkeypatch.setattr(audit, '_audit', observe)
    before = OLD.JointEvaluator(p)(assignment, deepcopy(state))
    assert before[1] is not None and len(counts) == 2
    counts.clear()
    evaluator = NEW.JointEvaluator(p)
    after = evaluator(assignment, deepcopy(state))
    assert after == before
    assert len(counts) == 1 and counts[0][1] is True
    assert evaluator.single_audit_accepts == 1 and evaluator.reference_fallbacks == 0


@pytest.mark.parametrize('ready,expected', [(9, 1), (10, 1), (11, 2)])
def test_cleaning_release_at_loading_is_available(ready, expected):
    from types import SimpleNamespace
    p = SimpleNamespace(operating_policy=SimpleNamespace(cleaning_duration=3,
        batches=[SimpleNamespace(id='b1', start=0), SimpleNamespace(id='b2', start=10)]),
        batch_demands={'b1': 1, 'b2': 1})
    raw = {'e1_trips': [{'return_complete': ready-3, 'events': [{'kind': 'p', 'quantity': 1}]}]}
    assert NEW._required_pool(p, raw) == expected


def test_release_uses_depot_cleaning_completion_and_actual_loading():
    from types import SimpleNamespace
    p = SimpleNamespace(operating_policy=SimpleNamespace(cleaning_duration=5,
        batches=[SimpleNamespace(id='b1', start=0), SimpleNamespace(id='b2', start=10)]),
        batch_demands={'b1': 1, 'b2': 1}, batch_loading_starts={'b1': 0, 'b2': 12})
    raw = {'e1_trips': [{'return_complete': 7, 'events': [{'kind': 'p', 'quantity': 1}]}]}
    assert NEW._required_pool(p, raw) == 1
    p.batch_loading_starts['b2'] = 11
    assert NEW._required_pool(p, raw) == 2


@pytest.mark.parametrize('mutation', ['return_late', 'duplicate_task', 'missing_task', 'bad_vehicle', 'reversed_route'])
def test_infeasible_neighbor_penalties_match_original(tiny, mutation):
    p, assignment, state = tiny
    state = deepcopy(state)
    if mutation == 'return_late':
        state['e2'][0]['return_after'] = p.operating_policy.cycle_end + 1
    elif mutation == 'duplicate_task':
        state['e2'][0]['tasks'].append(state['e2'][0]['tasks'][0])
    elif mutation == 'missing_task':
        state['e2'][0]['tasks'].pop()
    elif mutation == 'bad_vehicle':
        state['e2'][0]['vehicle'] = -1
    else:
        state['e2'][0]['tasks'].reverse()
    before = OLD.JointEvaluator(p)(assignment, deepcopy(state))
    after = NEW.JointEvaluator(p)(assignment, deepcopy(state))
    assert after == before


def test_corrupt_decoded_quantity_is_rejected_with_original_errors(tiny, monkeypatch):
    p, assignment, state = tiny
    for implementation in [OLD, NEW]:
        original = implementation.evaluate
        def corrupt(*args, _original=original, **kwargs):
            score, raw, issues = _original(*args, **kwargs)
            pickup = next(e for trip in raw['e1_trips'] for e in trip['events'] if e['kind'] == 'p')
            pickup['quantity'] += 1
            return score, raw, issues
        monkeypatch.setattr(implementation, 'evaluate', corrupt)
    before = OLD.JointEvaluator(p)(assignment, deepcopy(state))
    evaluator = NEW.JointEvaluator(p)
    after = evaluator(assignment, deepcopy(state))
    assert after == before and after[1] is None
    assert evaluator.reference_fallbacks == 1


def test_failed_fast_sizing_falls_back_to_full_original_replay(tiny, monkeypatch):
    p, assignment, state = tiny
    before = OLD.JointEvaluator(p)(assignment, deepcopy(state))
    monkeypatch.setattr(NEW, '_required_pool', lambda *args: 0)
    evaluator = NEW.JointEvaluator(p)
    after = evaluator(assignment, deepcopy(state))
    assert after == before and evaluator.reference_fallbacks == 1


@pytest.mark.parametrize('case', [f'S{i:02}' for i in range(1, 10)])
def test_full_original_schedules_match_old_evaluator_and_minimum_pool(case):
    import json
    from models.common.cycle import load_instance
    from models.common.cycle.audit import audit_solution
    from models.routing.model_8.alns.state import state_from_result
    from tools.configuration.loader import load_routing_case
    path = ROOT / f'data/formal_benchmark27_uniform_7_20_50_v1/model_8/small/{case}/case.json'
    parent = load_instance(*load_routing_case(path, use_pool_config=False))
    result = json.loads((ROOT / f'output/experiments/m8_all_small_rerun_20261029_v1/runs/{case}/s42/best_audited_result.json').read_text(encoding='utf-8'))
    p = parent.selected_problem(result)
    state = state_from_result(p, result)
    state['cutoffs'] = result['batch_cutoffs']
    assignment = dict(result['order_batch_assignment'])
    before = OLD.JointEvaluator(p)(assignment, deepcopy(state))
    after = NEW.JointEvaluator(p)(assignment, deepcopy(state))
    assert after == before
    fixed = OLD.JointEvaluator(p).fixed(assignment, state['cutoffs'])
    _, raw, issues = OLD.evaluate(fixed, state)
    OLD._delay_departures(fixed, raw)
    audit = audit_solution(fixed, raw)
    if audit['valid']:
        assert NEW._required_pool(fixed, raw) == audit['minimum_pool_for_schedule']


def test_every_frozen_replay_value_and_score_matches():
    import json
    count = 0
    for case in ['S02', 'S03', 'S09', 'M01', 'L01']:
        left, right = [json.loads((HERE / f'replays/{v}/{case}.json').read_text(encoding='utf-8'))
                       for v in ['baseline', 'candidate']]
        assert left['value_sha256'] == right['value_sha256']
        count += left['records']
    assert count >= 2000
