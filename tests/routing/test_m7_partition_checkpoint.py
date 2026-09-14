"""A saved non-improvement proof is reusable only for a tighter cutoff."""
import pytest
from experiments.runs.resume_m7_partition_proofs import reusable


@pytest.mark.parametrize('old_cutoff,new_cutoff,expected',[(100,90,True),(90,100,False),(100,None,False),(None,90,False)])
def test_cutoff_compatibility(old_cutoff,new_cutoff,expected):
    assert reusable(dict(info={'status':'CUTOFF'},cutoff=old_cutoff),new_cutoff)==expected


def test_feasible_incumbent_is_not_a_reusable_proof():
    assert not reusable(dict(info={'status':'TIME_LIMIT','objective':100,'best_bound':90}),100)


def test_proven_optimum_and_infeasibility_are_reusable():
    assert reusable(dict(info={'status':'OPTIMAL','proven_optimal':True,'objective':100}),90)
    assert reusable(dict(info={'status':'INFEASIBLE'}),None)
