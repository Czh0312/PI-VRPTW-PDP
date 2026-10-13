"""Failed runs cannot improve reported means, denominators or paired effects."""
from experiments.analysis.analyze_thesis_stages import grouped_statistics, paired_versions, ablation_contrasts
from experiments.analysis.analyze_thesis_stages import paired_budgets
import pytest


def row(version, valid, value, **extra):
    return dict(case='same', model=6, method='alns', seed=42, budget=30, version=version,
                valid=valid, objective=value, status='FEASIBLE' if valid else 'HEURISTIC_NO_SOLUTION',
                proven_optimal=False, proven_infeasible=False, **extra)


def test_failed_seed_remains_in_denominator_not_cost_mean():
    rows = [row('a', True, 10), row('a', False, None)]
    stat = grouped_statistics(rows)[0]
    assert stat['planned'] == 2 and stat['valid'] == 1
    assert stat['feasible_rate'] == .5 and stat['mean'] == 10
    assert stat['stdev'] is None


def test_one_sided_failure_has_no_paired_improvement():
    pair = paired_versions([row('old', True, 10), row('new', False, None)], ['old', 'new'])[0]
    assert not pair['both_valid'] and pair['relative_improvement'] is None
    assert pair['second_status'] == 'HEURISTIC_NO_SOLUTION'


def test_interaction_sign_and_missing_group():
    rows = [row('same', True, value, group=group) for group, value in zip('ABCD', (100, 90, 80, 65))]
    result = ablation_contrasts(rows)[0]
    assert result['A_minus_D'] == 35
    assert result['interaction_A_minus_B_minus_C_plus_D'] == -5
    rows[-1].update(valid=False, objective=None)
    result = ablation_contrasts(rows)[0]
    assert result['A_minus_B'] == 10 and result['A_minus_D'] is None
    assert result['interaction_A_minus_B_minus_C_plus_D'] is None


def test_budget_pairs_preserve_failure_and_long_budget_regression():
    a = row('final', True, 10)
    b = dict(a, budget=60, valid=False, objective=None, status='HEURISTIC_NO_SOLUTION')
    c = dict(a, budget=300, objective=12)
    pairs = paired_budgets([a, b, c])
    assert len(pairs) == 3
    assert pairs[0]['long_status'] == 'HEURISTIC_NO_SOLUTION'
    assert pairs[0]['relative_improvement'] is None
    assert pairs[1]['short_minus_long'] == -2
    assert pairs[1]['relative_improvement'] == -.2
    assert pairs[2]['short_cost'] is None and pairs[2]['long_cost'] == 12


def test_budget_pairs_reject_changed_domain_and_duplicate_seed():
    a = row('final', True, 10, counts=[1, 2, 3], case_path='same.json')
    with pytest.raises(ValueError, match='Duplicate'):
        paired_budgets([a, dict(a)])
    with pytest.raises(ValueError, match='domain'):
        paired_budgets([a, dict(a, budget=60, counts=[1, 2])])
    with pytest.raises(ValueError, match='input'):
        paired_budgets([a, dict(a, budget=60, case_path='different.json')])


def test_budget_pairs_do_not_compare_different_ablation_groups():
    a = row('final', True, 10, group='A')
    b = dict(a, group='B', budget=60, objective=9)
    assert paired_budgets([a, b]) == []
    pairs = paired_budgets([a, b, dict(a, budget=60, objective=8)])
    assert len(pairs) == 1 and pairs[0]['group'] == 'A'
    assert pairs[0]['short_minus_long'] == 2
