"""Focused validation of the experiment's clock adapter and matched objective."""
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
import random
import sys

import pytest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE/'source' if (HERE/'source').is_dir() else ROOT))
import calendar_grid as grid
from models.common.cycle import load_instance
from models.common.cycle.audit import audit_solution
from models.routing.service import solve_routing
from tools.configuration.loader import load_routing_case


def problem(model=8, counts=(1,2,3,4)):
    case = ROOT/f'data/formal_benchmark27_spacetime_v4_1/model_{model}/small/S01/case.json'
    cfg, fleet = load_routing_case(case, use_pool_config=False)
    # Original config paths are rooted at the working project, not the snapshot.
    for name in ('data','orders','routing_config','economic_config','operating_policy','dataset_metadata'):
        if isinstance(cfg.get(name),str):
            cfg[name] = str(ROOT/cfg[name])
    if model == 8:
        cfg['joint_batch_options'] = {**cfg['joint_batch_options'],'batch_count_candidates':list(counts)}
    else:
        cfg['objective_mode'] = 'cost'
    return load_instance(cfg,fleet)


def test_minute_projection_respects_bounds_and_ties():
    assert grid.nearest(43410,28860,75540,60) == 43440
    assert grid.nearest(101,101,241,60) == 120
    assert grid.nearest(999,101,241,60) == 240
    with pytest.raises(ValueError):
        grid.nearest(1,1,59,60)


@pytest.mark.parametrize('count',[2,3,4])
def test_boundary_projection_retains_cycle_and_both_minimum_phases(count):
    p = problem()
    rng = random.Random(51+count)
    for _ in range(100):
        raw = [p.operating_policy.cycle_start] + sorted(rng.randrange(28800,75601) for _ in range(count-1)) + [75600]
        selected = grid.boundaries_on_grid(p,count,raw)
        assert selected[0] == 28800 and selected[-1] == 75600
        assert all(v % 60 == 0 for v in selected)
        assert all(b-a >= 120 for a,b in zip(selected,selected[1:]))


def test_strict_result_check_rejects_unsnapped_decisions():
    p = problem()
    result = dict(info=dict(objective=1.),cycle_batch_count=2,batch_boundaries=[28800,52200,75600],
        batch_cutoffs={'b1':43440,'b2':63900},batches=[dict(start=28800,cutoff=43440,end=52200),
                                                      dict(start=52200,cutoff=63900,end=75600)])
    assert grid.check_result(p,result)['valid']
    for field in ('boundary','cutoff','batch'):
        bad = deepcopy(result)
        if field == 'boundary': bad['batch_boundaries'][1] += 1
        elif field == 'cutoff': bad['batch_cutoffs']['b1'] -= 30
        else: bad['batches'][0]['cutoff'] += 1
        assert not grid.check_result(p,bad)['valid']


def test_adapter_preserves_physical_seconds_and_m3_reference_calendar():
    p3 = problem(3)
    reference = p3.operating_policy
    p = problem()
    physical = dict(p._travel)
    grid.install()
    assert problem(3).operating_policy == reference
    assert reference.batches[0].cutoff == 69030  # Original 19:10:30.
    for count in (1,2,3,4):
        child = p.fixed_calendar(count)
        assert child.operating_policy.time_tick_seconds == 1
        assert child.operating_policy.time_tick_subdivision == 1
        assert all(v % 60 == 0 for v in child.default_cutoffs.values())
        assert p._travel == physical
    assert p.calendar_contract['calendar_decision_grid_seconds'] == 60


def test_refined_clock_still_uses_physical_minutes():
    p = problem()
    refined = replace(p.operating_policy,time_tick_subdivision=7)
    assert grid.step(refined) == 420
    assert grid.nearest(43410*7,28860*7,75540*7,grid.step(refined)) == 43440*7


def test_full_m8_alns_decodes_minute_candidates_and_replays_two_cycles(monkeypatch):
    grid.install()
    from models.routing.model_8.alns.evaluator import JointEvaluator
    original = JointEvaluator.__call__
    checked = []
    def observed(self,assignment,state):
        score,result,issues = original(self,assignment,state)
        assert all(v % grid.step(self.problem.operating_policy) == 0 for v in state['cutoffs'].values())
        checked.append(1)
        return score,result,issues
    monkeypatch.setattr(JointEvaluator,'__call__',observed)
    p = problem(counts=(1,))
    result = solve_routing(model=8,method='alns',problem=p,alns_time=3.,alns_iter=10**9,seed=42)
    assert checked
    assert result['audit']['valid'],result['info']
    assert audit_solution(p,result)['periodic_replay']['valid']
    assert grid.check_result(p,result)['valid']


def test_m3_alns_optimizes_same_cost_profile_directly():
    p = problem(3)
    result = solve_routing(model=3,method='alns',problem=p,alns_time=2.,alns_iter=10**9,seed=42)
    audit = audit_solution(p,result)
    assert audit['valid'] and audit['periodic_replay']['valid']
    assert result['objective_mode'] == 'cost'
    cost = p.cost_profile.evaluate(audit,p.total)
    assert cost['total'] == pytest.approx(result['info']['objective'])


@pytest.mark.parametrize('counts',[(2,),(1,2,3,4)])
def test_variable_calendar_and_count_scheduler_use_the_grid(counts):
    grid.install()
    p = problem(counts=counts)
    result = solve_routing(model=8,method='alns',problem=p,alns_time=4.,alns_iter=10**9,seed=43)
    assert not result['info'].get('audit_failure_count')
    assert not result['info'].get('certificate_error_count')
    if result['info'].get('objective') is not None:
        assert grid.check_result(p,result)['valid']
        assert audit_solution(p,result)['periodic_replay']['valid']
    else:
        assert result['info']['status'] == 'HEURISTIC_NO_SOLUTION'
