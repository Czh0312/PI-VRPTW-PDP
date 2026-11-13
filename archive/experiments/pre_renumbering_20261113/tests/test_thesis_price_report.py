import pytest

from experiments.analysis.report_thesis_prices import summarize


def test_failed_and_unpaired_runs_remain_in_denominators():
    rows = [dict(valid=True, objective=90., repriced=100.),
            dict(valid=True, objective=120., repriced=100.),
            dict(valid=True, objective=70., repriced=None),
            dict(valid=False, objective=None, repriced=100.)]
    result = summarize(rows)
    assert (result['planned'], result['valid'], result['paired']) == (4, 3, 2)
    assert (result['better'], result['worse'], result['tied']) == (1, 1, 0)
    assert result['mean_improvement_pct'] == -5.
    assert result['mean_paired_reoptimized'] == 105.
    assert result['mean_cost'] == pytest.approx(280/3)


def test_empty_cost_evidence_is_missing_not_zero():
    result = summarize([dict(valid=False, objective=None, repriced=None)])
    assert result['planned'] == 1 and result['paired'] == 0
    assert result['mean_cost'] is None and result['mean_improvement_pct'] is None
