"""All B=1 cases must reach each exact model, including real-derived cases."""
import pytest

from experiments.runs.run_single_batch_exact import main, outcome


def test_full_single_batch_exact_plan_covers_four_models(capsys):
    plan = main(['--dry-run', '--exact-time', '300'])
    assert plan['case_count'] == 93
    assert plan['job_count'] == 372
    assert plan['total_budget_seconds'] == 111600
    jobs = plan['jobs']
    assert len({job['run'] for job in jobs}) == 372
    assert all(job['batch_count'] == 1 and job['solver'] == 'exact' for job in jobs)
    for model in range(1, 5):
        subset = [job for job in jobs if job['model'] == model]
        assert len(subset) == 93
        assert {job['size'] for job in subset} == {'small', 'medium', 'large'}
    assert any(job['source_kind'] == 'lade_derived_scenario' for job in jobs)
    capsys.readouterr()


def test_zhou_single_batch_plan_uses_same_seventy_cases_for_each_model(capsys):
    plan = main(['--dry-run', '--source-kinds', 'zhou_derived_scenario'])
    assert plan['case_count'] == 70
    assert plan['job_count'] == 280
    cases_by_model = [{job['case_id'] for job in plan['jobs'] if job['model'] == model}
                      for model in range(1, 5)]
    assert all(cases == cases_by_model[0] for cases in cases_by_model)
    for model in range(1, 5):
        for n in (7, 10, 12, 20, 30, 50, 100):
            jobs = [j for j in plan['jobs'] if j['model'] == model and j['customer_count'] == n]
            assert len(jobs) == 10
            assert all(j['batch_count'] == 1 and j['source_kind'] == 'zhou_derived_scenario' for j in jobs)
    capsys.readouterr()


@pytest.mark.parametrize('valid,optimal,infeasible,status,expected', [
    (True, True, False, 'OPTIMAL', 'OPTIMAL'),
    (True, False, False, 'TIME_LIMIT', 'FEASIBLE_NOT_PROVEN'),
    (False, False, False, 'TIME_LIMIT', 'NO_SOLUTION_WITHIN_BUDGET'),
    (False, False, False, 'TIME_LIMIT_BUILD', 'NO_SOLUTION_WITHIN_BUDGET'),
    (False, False, True, 'INFEASIBLE', 'INFEASIBLE'),
    (False, False, False, 'AUDIT_FAILED', 'ERROR_OR_AUDIT_FAILURE'),
])
def test_summary_distinguishes_proofs_from_time_limits(valid, optimal, infeasible, status, expected):
    assert outcome(dict(valid=valid, proven_optimal=optimal,
                        proven_infeasible=infeasible, status=status)) == expected
