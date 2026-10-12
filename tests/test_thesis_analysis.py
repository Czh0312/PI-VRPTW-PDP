"""Failed runs cannot improve reported means, denominators or paired effects."""
from experiments.analysis.analyze_thesis_stages import grouped_statistics, paired_versions, ablation_contrasts


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
