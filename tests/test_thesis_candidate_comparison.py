from copy import deepcopy

import pytest

from experiments.analysis import compare_thesis_candidates as comparison
from experiments.runs import run_thesis_completion as driver


def frozen(study, digest='same'):
    job = dict(case='case', model=6, method='alns', seed=42, budget=30,
               counts=[1, 2, 3], iterations=100, family='family', split='holdout',
               diagnostics=True, case_path='case.json')
    driver.write(study / 'case.json', dict(data=str(study / 'data.csv'), pool_max=2))
    return dict(jobs=[job], original_root=str(study), input_hashes={'data.csv': digest})


def test_relocated_identical_inputs_match_but_changed_data_does_not(tmp_path):
    a, b = tmp_path / 'a', tmp_path / 'b'
    ma, mb = frozen(a), frozen(b)
    comparison.verify_matching(a, ma, b, mb)
    mb['input_hashes']['data.csv'] = 'changed'
    with pytest.raises(ValueError, match='inputs differ'):
        comparison.verify_matching(a, ma, b, mb)


@pytest.mark.parametrize('field,value', [('seed', 43), ('counts', [1, 2]), ('split', 'development')])
def test_changed_seed_or_domain_or_split_is_not_matched(tmp_path, field, value):
    a, b = tmp_path / 'a', tmp_path / 'b'
    ma, mb = frozen(a), frozen(b)
    mb['jobs'][0][field] = value
    with pytest.raises(ValueError):
        comparison.verify_matching(a, ma, b, mb)


def test_duplicate_version_cannot_silently_replace_primary_row(tmp_path):
    ma = frozen(tmp_path)
    ma['jobs'].append(deepcopy(ma['jobs'][0]))
    with pytest.raises(ValueError, match='Duplicate'):
        comparison.indexed(ma['jobs'])


def test_failed_candidate_and_unfinished_study_retain_denominator(tmp_path, monkeypatch):
    a, b = tmp_path / 'a', tmp_path / 'b'
    ma, mb = frozen(a), frozen(b)
    for study, manifest in ((a, ma), (b, mb)):
        driver.write(study / 'manifest.json', manifest)
    first = dict(ma['jobs'][0], valid=True, objective=10, status='FEASIBLE')
    second = dict(mb['jobs'][0], valid=False, objective=None, status='RUNNING')
    monkeypatch.setattr(comparison, 'primary_rows', lambda p: (ma, [first]) if p == a else (mb, [second]))
    monkeypatch.setattr(comparison, 'diagnostics', lambda rows: [])
    result = comparison.compare(a, b, tmp_path / 'out')
    assert not result['complete']
    summary = result['summary'][0]
    assert summary['planned'] == summary['baseline_valid'] == 1
    assert summary['candidate_valid'] == summary['both_valid'] == summary['better'] == 0
    assert summary['mean_relative_improvement'] is None


def test_revisits_are_summed_and_unaudited_costs_not_used(tmp_path):
    manifest = frozen(tmp_path)
    row = dict(manifest['jobs'][0], _path=tmp_path, status='FEASIBLE')
    driver.write(tmp_path / 'candidate_diagnostics.json', [
        dict(batch_count=1, started=1, returned=3, child_audit_valid=True,
             info=dict(status='FEASIBLE', objective=10)),
        dict(batch_count=2, started=3, returned=4, child_audit_valid=False,
             info=dict(status='HEURISTIC_NO_SOLUTION', objective=1)),
        dict(batch_count=1, started=4, returned=7, child_audit_valid=True,
             info=dict(status='FEASIBLE', objective=8))])
    rows = comparison.per_k_diagnostics([row], manifest['jobs'], 'baseline')
    assert rows[0]['visits'] == 2 and rows[0]['measured_visit_seconds'] == 5
    assert rows[0]['best_audited_child_cost'] == 8
    assert rows[1]['best_audited_child_cost'] is None
    assert rows[2]['diagnostics_observed'] and rows[2]['visits'] == 0
    missing = dict(row, status='RUNNING')
    missing.pop('_path')
    rows = comparison.per_k_diagnostics([missing], manifest['jobs'], 'candidate')
    assert all(not r['diagnostics_observed'] and r['measured_visit_seconds'] is None for r in rows)


def test_exact_comparison_checks_domain_and_certificate(tmp_path, monkeypatch):
    a, b = tmp_path / 'ref', tmp_path / 'candidate'
    ma, mb = frozen(a), frozen(b)
    ma['jobs'][0].update(id='ref', method='exact')
    mb['jobs'][0]['id'] = 'candidate'
    reference = dict(ma['jobs'][0], valid=True, objective=11., best_bound=9.,
                     proven_optimal=False, proven_infeasible=False, status='FEASIBLE')
    candidate = dict(mb['jobs'][0], valid=True, objective=12., status='FEASIBLE')
    monkeypatch.setattr(comparison, 'primary_rows', lambda path: (ma, [reference]))
    studies = [(b, mb, [candidate], 'candidate')]
    error = comparison.reference_comparisons(studies, a)[0]
    assert error['relative_optimal_error'] is None
    assert error['difference_from_reference_incumbent'] == 1.
    candidate['objective'] = 8.
    with pytest.raises(ValueError, match='contradicts'):
        comparison.reference_comparisons(studies, a)
    candidate['objective'] = 12.
    mb['input_hashes']['data.csv'] = 'different'
    with pytest.raises(ValueError, match='domain differs'):
        comparison.reference_comparisons(studies, a)
