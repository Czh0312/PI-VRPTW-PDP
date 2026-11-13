"""Guard experiment coverage and honest ALNS comparisons with certified values."""
import pytest

from experiments.runs.run_small7_efficiency_check import plan,score


def inputs():
    cases=[dict(base_case_id=f'S{i:02}',model=m,size='small',customer_count=7,
        family_id='f',source_block='b') for m in range(1,5) for i in range(1,10)]
    failures={('S01',3),('S01',4),('S02',4)}
    rows=[dict(case=r['base_case_id'],model=r['model'],
        status='TIME_LIMIT' if (r['base_case_id'],r['model']) in failures else 'OPTIMAL',
        proven_optimal=(r['base_case_id'],r['model']) not in failures) for r in cases]
    return dict(cases=cases),rows


def test_matrix_preserves_all_cold_timeouts_and_all_36_alns_pairs():
    suite,prior=inputs();records,jobs=plan(suite,prior)
    assert len(records)==36 and len(jobs)==39
    exact=[r for r in jobs if r['method']=='exact']
    assert [(r['case'],r['model'],r['budget']) for r in exact]==[('S01',3,600),('S01',4,600),('S02',4,600)]
    alns=[r for r in jobs if r['method']=='alns']
    assert len(alns)==36 and all(r['budget']==60 and r['seed']==42 and r['iterations']==10**9 for r in alns)
    assert len({r['id'] for r in jobs})==39


def test_changed_customer_count_is_rejected():
    suite,prior=inputs();suite['cases'][0]['customer_count']=10
    with pytest.raises(ValueError):plan(suite,prior)


def test_missing_or_changed_timeout_selection_is_rejected():
    suite,prior=inputs();prior[-1]['proven_optimal']=False
    with pytest.raises(ValueError):plan(suite,prior)
    with pytest.raises(ValueError):plan(suite,prior[:-1])


def reference():return dict(objective=100.,proof='cold_exact')


def test_matching_reference_does_not_create_an_alns_optimality_proof():
    row=dict(valid=True,objective=100.,budget=60,proven_optimal=False)
    value=score(row,reference(),dict(time_to_first_feasible=.5,
        incumbent_history=[dict(time=1.,objective=110.),dict(time=12.,objective=100.)]))
    assert value['matches_known_optimum'] and value['optimal_gap_percent']==0
    assert value['first_optimum_recorded_seconds']==12
    assert value['first_optimum_recorded_within_budget']
    assert not row['proven_optimal'] and 'proven_optimal' not in value
    assert not value['history_independently_replayed']


def test_failed_or_conflicting_results_are_not_zero_gap_observations():
    failed=score(dict(valid=False,objective=None,budget=60),reference())
    assert failed['optimal_gap_percent'] is None and not failed['matches_known_optimum']
    conflict=score(dict(valid=True,objective=99.,budget=60),reference())
    assert conflict['reference_conflict'] and conflict['optimal_gap_percent'] is None
    assert not conflict['matches_known_optimum']


def test_positive_gap_and_late_history_are_reported_honestly():
    value=score(dict(valid=True,objective=110.,budget=60),reference())
    assert value['optimal_gap_percent']==pytest.approx(10.)
    late=score(dict(valid=True,objective=100.,budget=60),reference(),dict(
        incumbent_history=[dict(time=60.2,objective=100.)]))
    assert late['matches_known_optimum'] and not late['first_optimum_recorded_within_budget']


@pytest.mark.parametrize('target',[0,float('nan'),float('inf')])
def test_invalid_reference_is_rejected(target):
    with pytest.raises(ValueError):score(dict(valid=False,budget=60),dict(objective=target,proof='x'))
