"""Prevent failed solves or mismatched jobs from improving reported quality."""
import pytest

from tools.audit.summarize_model5_alns import compare, budget_extension


def row(seed, objective, valid=True):
    return dict(case_id='example_b3',family='example',split='held_out',customers=100,
                batches=3,pool=70,pool_label='tight',budget=5,seed=seed,
                valid=valid,objective=objective)


def test_quality_uses_paired_valid_runs_but_failures_remain_in_counts():
    data=compare([row(1,100),row(2,200),row(3,300)],
                 [row(1,80),row(2,220),row(3,None,False)])
    s=data['overview']
    assert s['before_valid']==3 and s['after_valid']==2
    assert s['common_valid']==2
    assert s['improved']==s['worse']==1
    assert s['paired_mean_change_percent']==pytest.approx(-5)
    assert s['dispersion_groups']==0
    assert data['paired'][2]['change_percent'] is None


def test_comparison_refuses_missing_seeds_or_duplicate_jobs():
    with pytest.raises(ValueError,match='identical unique'):
        compare([row(1,100),row(2,110)],[row(1,90)])
    with pytest.raises(ValueError,match='identical unique'):
        compare([row(1,100),row(1,100)],[row(1,90),row(2,90)])


def test_budget_extension_pairs_each_seed_and_retains_invalid_long_runs():
    rows=[row(1,100),row(2,200),row(3,300),
          dict(row(1,90),budget=15),dict(row(2,210),budget=15),
          dict(row(3,None,False),budget=15)]
    summary=budget_extension(rows)
    assert summary['pair_count']==3 and summary['common_valid']==2
    assert summary['short_valid']==3 and summary['long_valid']==2
    assert summary['improved']==summary['worse']==1
    assert summary['paired_mean_change_percent']==pytest.approx(-2.5)
    assert summary['pairs'][2]['change_percent'] is None
    with pytest.raises(ValueError,match='unique'):
        budget_extension(rows+[rows[0]])
    with pytest.raises(ValueError,match='iteration cap'):
        budget_extension([dict(row(1,100),iterations=100),dict(row(1,90),budget=15,iterations=200)])
