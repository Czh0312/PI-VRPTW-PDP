"""Prevent failed solves or mismatched jobs from improving reported quality."""
import pytest

from tools.audit.summarize_model5_alns import compare


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
