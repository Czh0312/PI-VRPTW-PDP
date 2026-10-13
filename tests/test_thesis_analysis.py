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


def test_statistics_keep_ablation_and_pool_conditions_separate():
    rows = [row('final', True, 100, group='A'), row('final', True, 60, group='B'),
            row('final', False, None, group='B'), row('final', True, 10, fixed_pool=0),
            row('final', True, 20, fixed_pool=2), row('final', True, 30)]
    stats = {(r['group'], r['fixed_pool']): r for r in grouped_statistics(rows)}
    assert len(stats) == 5
    assert stats['A', None]['mean'] == 100
    assert stats['B', None]['mean'] == 60 and stats['B', None]['feasible_rate'] == .5
    assert stats[None, 0]['mean'] == 10 and stats[None, None]['mean'] == 30


def test_version_pairs_keep_restrictions_and_reject_duplicates():
    a = row('old', True, 100, group='A')
    b = row('new', True, 60, group='B')
    pairs = paired_versions([a, b, dict(a, version='new', objective=90)], ['old', 'new'])
    assert len(pairs) == 2
    assert pairs[0]['first_minus_second'] == 10
    assert not pairs[1]['both_valid']
    with pytest.raises(ValueError, match='Duplicate'):
        paired_versions([a, a], ['old', 'new'])


def test_ablation_contrasts_keep_model_and_version_separate():
    a = row('v1', True, 100, group='A')
    b = row('v1', True, 90, group='B')
    results = ablation_contrasts([a, b, dict(a, version='v2'), dict(b, model=7)])
    assert len(results) == 3
    assert sum(r['A_minus_B'] is not None for r in results) == 1
    with pytest.raises(ValueError, match='Duplicate'):
        ablation_contrasts([a, a])


def test_ablation_bounds_distinguish_search_reversal_from_certificate_conflict():
    a = row('v1', True, 100, group='A', best_bound=80)
    b = row('v1', True, 110, group='B', best_bound=70)
    result = ablation_contrasts([a, b])[0]
    assert result['A_minus_B'] == -10
    assert result['A_minus_B_lower'] == -30 and result['A_minus_B_upper'] == 30
    assert result['A_to_B_incumbent_reversal']
    assert not result['A_to_B_certificate_conflict']
    b.update(valid=False, objective=None, status='TIME_LIMIT', best_bound=101)
    result = ablation_contrasts([a, b])[0]
    assert result['A_minus_B'] is None and result['A_minus_B_lower'] is None
    assert result['A_minus_B_upper'] == -1 and result['A_to_B_certificate_conflict']
    b.update(best_bound=None, proven_infeasible=True, status='INFEASIBLE')
    assert ablation_contrasts([a, b])[0]['A_to_B_certificate_conflict']


def test_ablation_optimal_contrast_intervals_collapse_and_unknown_bounds_stay_unknown():
    rows = [dict(row('v1', True, cost, group=g), proven_optimal=True) for g, cost in zip('ABCD', (100, 90, 80, 65))]
    result = ablation_contrasts(rows)[0]
    assert result['interaction_lower'] == result['interaction_upper'] == -5
    assert result['A_minus_D_lower'] == result['A_minus_D_upper'] == 35
    rows[-1].update(proven_optimal=False, best_bound=None)
    result = ablation_contrasts(rows)[0]
    assert result['interaction_lower'] is None and result['interaction_upper'] == -5
    assert result['A_minus_D_upper'] is None
