"""Exact optimum/replay and nonmetric safety checks for the isolated prototype."""
import importlib.util
import json
from pathlib import Path
from functools import partial

import gurobipy as gp
import pytest

from models.common.cycle.audit import audit_solution
from models.common.cycle.calendar import attach_calendar_decisions
from models.common.cycle.precheck import travel_lower_bounds
from models.common.cycle.unified_milp import build_unified_model
from models.common.solver_config import read_milp_info
from tests.routing.test_model8_physical_bounds import instance

spec = importlib.util.spec_from_file_location('occupancy_trial', Path(__file__).with_name('occupancy_probe.py'))
prototype = importlib.util.module_from_spec(spec)
spec.loader.exec_module(prototype)


@pytest.mark.parametrize('kind', ['ordinary', 'late_release', 'multiple_satellites', 'unequal_counts'])
@pytest.mark.parametrize('profile', ['physical_occupancy', 'physical_time'])
def test_occupancy_bound_preserves_exact_optimum_and_physical_replay(kind, profile):
    p = instance(kind)
    from models.routing.model_8.exact import solve_exact
    baseline = solve_exact(p, time_limit=15, strengthening='none', milp_options=dict(formulation='compact'))
    assert baseline['info']['proven_optimal']
    model, ctx = build_unified_model(p, build_fixed=partial(prototype.build_with_occupancy,
                                                         strengthening=profile), compact_e2=True)
    try:
        model.Params.OutputFlag = 0
        model.Params.TimeLimit = 15
        model.optimize()
        assert model.Status == gp.GRB.OPTIMAL
        k = next(k for k, v in ctx['selectors'].items() if v.X > .5)
        block = ctx['blocks'][k]
        info = read_milp_info(model, proof_scope='occupancy_equivalence_test', build_time=0)
        result = prototype.trial.extract_solution(block['problem'], model, block['context'], info)
        boundaries = [b['start'] for b in result['batches']] + [block['problem'].operating_policy.cycle_end]
        attach_calendar_decisions(p, result, k, boundaries)
        audit = audit_solution(p, result)
        assert audit['valid'] and audit['periodic_replay']['valid']
        assert audit['objective'] == pytest.approx(baseline['info']['objective'])
    finally:
        model.dispose()


def test_shortest_path_relaxation_does_not_use_a_nonmetric_direct_arc():
    p = instance('ordinary').for_count(2)
    # Make a direct arc huge while retaining the shorter two-arc path.
    p._travel = dict(p._travel)
    p._travel[p.depot, p.satellites[0]] = 10000
    p.travel_bounds = travel_lower_bounds(p)
    bound = prototype.occupancy_bound(p)
    assert max(r['earliest_clean'] for r in bound['intervals']) < 10000
    assert min(r['latest_loading'] for r in bound['intervals']) > -10000
    assert 0 <= bound['pool_lower_bound'] <= p.total


@pytest.mark.parametrize('case', [f'S{i:02d}' for i in range(1, 10)])
def test_saved_audited_plans_respect_every_mandatory_interval(case):
    base = prototype.ROOT/'output/experiments/small7_m8_alns300_20261024_v1'
    parent = prototype.trial.load_instance(*prototype.trial.load_routing_case(
        base/f'cases/{case}_m8.json', use_pool_config=False))
    saved = json.loads((base/f'runs/{case}_m8_alns_300s_s42/attempt_001/result.json').read_text(encoding='utf-8'))
    assert audit_solution(parent, saved)['valid']
    problem = parent.for_count(saved['cycle_batch_count'])
    bound = prototype.occupancy_bound(problem)
    assert bound['pool_lower_bound'] <= saved['container_pool_size']
    first = saved['batches'][0]['id']
    assert all(saved['order_batch_assignment'][row['order']] == first
               for row in prototype.forced_first_batch(problem, bound))
    batches = {b['id']: b for b in saved['batches']}
    for interval in bound['intervals']:
        oid = interval['order']
        assigned = saved['order_batch_assignment'][oid]
        assert batches[assigned]['start'] <= interval['latest_loading']
        site = problem.order_map[oid].satellite
        unloaded = next(t['return_complete'] for t in saved['e2_trips']
                        if any(e['order_id'] == oid and e['kind'] == 'p' for e in t['events']))
        recovery = next(b['id'] for b in saved['batches'] if unloaded <= b['cutoff'])
        clean = next(t['return_complete'] + problem.operating_policy.cleaning_duration
                     for t in saved['e1_trips'] if t['batch_id'] == recovery
                     and any(e['site_id'] == site and e['kind'] == 'p' and e['quantity'] > 0
                             for e in t['events']))
        assert clean >= interval['earliest_clean']
