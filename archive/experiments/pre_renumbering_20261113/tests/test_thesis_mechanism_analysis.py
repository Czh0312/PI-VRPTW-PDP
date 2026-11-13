"""Certificate scope and missing observations must survive mechanism analysis."""
import csv
from pathlib import Path

from experiments.analysis.analyze_thesis_mechanisms import e3, e4
from experiments.analysis.analyze_thesis_m2 import reference_error, assignment_diagnostics
from experiments.runs.run_thesis_completion import write
from experiments.analysis.thesis_pool_reference import threshold_with_reference, fixed_schedule_at_pool
from types import SimpleNamespace
import pytest


def rows(path):
    with path.open(encoding='utf-8-sig') as handle:
        return list(csv.DictReader(handle))


def test_unknown_k_bound_prevents_union_optimum(tmp_path):
    data = [dict(mechanism='release', group='standard_m7', counts=[1], status='FEASIBLE', valid=False,
        objective=None, best_bound=3., proven_optimal=False, proven_infeasible=False),
        dict(mechanism='release', group='standard_m7', counts=[2], status='TIME_LIMIT', valid=False,
        objective=None, best_bound=None, proven_optimal=False, proven_infeasible=False)]
    e3(tmp_path, data)
    record = rows(tmp_path/'tables/calendar_best_over_registered_k.csv')[0]
    assert record['lower'] == '' and record['proven_optimal'] == 'False'
    assert record['allowed_k'] == '[1, 2]'
    data[1].update(proven_infeasible=True, status='INFEASIBLE')
    e3(tmp_path, data)
    assert rows(tmp_path/'tables/calendar_best_over_registered_k.csv')[0]['lower'] == '3.0'


def test_pool_threshold_requires_every_lower_integer_excluded(tmp_path):
    (tmp_path/'figures').mkdir()
    data = [dict(reference_case='case', seed=42, fixed_pool=n, necessary_lower_bound=2,
        valid=n == 4, proven_infeasible=n == 2, status='FEASIBLE' if n == 4 else 'INFEASIBLE',
        objective=100. if n == 4 else None, cost_components={}) for n in (2, 4)]
    e4(tmp_path, data)
    record = rows(tmp_path/'tables/pool_thresholds.csv')[0]
    assert record['smallest_audited_feasible_pool'] == '4'
    assert record['proven_feasibility_threshold'] == '' and record['pending_integer_candidates'] == '[3]'
    data.append(dict(data[0], fixed_pool=3, proven_infeasible=False, status='HEURISTIC_NO_SOLUTION'))
    e4(tmp_path, data)
    assert rows(tmp_path/'tables/pool_thresholds.csv')[0]['proven_feasibility_threshold'] == ''
    data[-1].update(proven_infeasible=True, status='INFEASIBLE')
    e4(tmp_path, data)
    assert rows(tmp_path/'tables/pool_thresholds.csv')[0]['proven_feasibility_threshold'] == '4'


def test_m2_limited_reference_is_not_an_optimum():
    candidate = dict(valid=True, objective=12.)
    reference = dict(valid=True, objective=11., proven_optimal=False, best_bound=9., status='FEASIBLE')
    error = reference_error(candidate, reference)
    assert error['absolute_optimal_error'] is None
    assert error['relative_optimal_error'] is None
    assert error['difference_from_reference_incumbent'] == 1.
    reference['proven_optimal'] = True
    assert reference_error(candidate, reference)['absolute_optimal_error'] == 1.
    candidate.update(valid=False, objective=None)
    assert reference_error(candidate, reference)['relative_optimal_error'] is None


def test_reference_witness_does_not_turn_failed_formal_calls_into_success():
    data = [dict(fixed_pool=2, valid=False, proven_infeasible=False)]
    result = threshold_with_reference(2, data, 2)
    assert result['formal_smallest_feasible'] is None
    assert result['proven_threshold'] == 2
    assert threshold_with_reference(1, data, 2)['proven_threshold'] is None
    data[0]['proven_infeasible'] = True
    with pytest.raises(ValueError, match='contradicts'):
        threshold_with_reference(2, data, 2)


def test_fixed_schedule_projection_changes_only_pool_and_cost_audit_metadata():
    policy = dict(container_pool_size=2, cleaning_duration=5, cycle_end=100)
    source = dict(operating_policy=policy, container_pool_size=2, model=6,
                  info={'objective': 100}, total_distance=20, e1_trips=[{'departure': 10}])
    target = {**policy, 'container_pool_size': 1}
    fixed = SimpleNamespace(model_number=4, operating_policy=SimpleNamespace(as_dict=lambda: target))
    copied = fixed_schedule_at_pool(source, fixed, 1)
    assert source['operating_policy']['container_pool_size'] == 2
    assert source['info']['objective'] == 100
    assert copied['operating_policy']['container_pool_size'] == 1
    assert copied['e1_trips'] == source['e1_trips']
    target['cleaning_duration'] = 4
    with pytest.raises(ValueError, match='other than pool'):
        fixed_schedule_at_pool(source, fixed, 1)


def test_m2_projection_and_partition_diagnostics_are_distinct(tmp_path):
    write(tmp_path/'search_diagnostics.json', dict(
        assignment_proposals=[dict(assignment={'a': 's1'}), dict(assignment={'a': 's1'}), dict(assignment={'a': 's2'})],
        projection_attempts=2, projection_successes=1, projection_seconds=.2,
        assignment_subsolves=[dict(valid=True, elapsed=2, wall_seconds=1, info={'objective': 20}),
                             dict(valid=False, elapsed=4, wall_seconds=1, info={'objective': None}),
                             dict(valid=True, elapsed=6, wall_seconds=1, info={'objective': 10})]))
    write(tmp_path/'result.json', dict(info={'objective': 10}, assignment={'a': 's2'}))
    result = assignment_diagnostics(tmp_path)
    assert result['proposal_calls'] == 3 and result['unique_partitions'] == 2
    assert result['projection_success_fraction'] == .5 and result['valid_subsolves'] == 2
    assert result['final_assignment_changes'] == 1 and result['incumbent_discoveries'] == 2
    assert result['first_valid_subsolve_return'] == 2 and result['best_subsolve_return'] == 6
