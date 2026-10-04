"""Validate actual domain/count/budget metadata then run frozen proof checks once."""
from pathlib import Path
from datetime import datetime, timezone
from common import OUT, read, write, frozen_cases, load_case, sha
from models.routing.solvers.exact.single_batch_pickup_bound import pickup_contexts
from balance import transformed_prices


def main():
    assert not (OUT/'verification').exists(), 'Retain previous verification; no blind rerun'
    assert not (OUT/'verification_scope_guard.json').exists(), 'Retain first scope audit'
    target = OUT/'prices5'
    rows = read(target/'comparison.json')
    jobs = [read(Path(path)) for path in read(target/'jobs.json')]
    assert len(rows) == len(jobs) == 16
    assert read(target/'manifest.json')['threads'] == 1
    problems = {case: load_case(folder) for _, case, folder in frozen_cases()}
    observed = set()
    for job in jobs:
        problem = problems[job['case']]
        assert problem.model_number == 4 and len(problem.operating_policy.batches) == 1
        _, contexts, check = pickup_contexts(problem)
        assert check['passed']
        counts = {s: c['maximum_trips'] for s, c in contexts.items()}
        assert job['maximum_trips'] == counts
        assert job['total_budget'] == 5 and job['call_cap'] == 1.0
        assert job['seed'] in (42, 43) and job['historical_budget'] in (15, 30)
        assert isinstance(job['balanced'], bool)
        assert job['source_snapshot_sha256'] == sha(OUT/'prepared_manifest.json')
        prices, _ = transformed_prices(problem, read(job['fixed_history_path']), job['balanced'])
        assert set(prices) == set(contexts)
        assert all(set(prices[s][0]) == set(contexts[s]['lookup']) for s in contexts)
        result = read(Path(job['result_path']))
        assert result['maximum_trips'] == counts and result['full_tasks'] == 40
        assert set(result['pricing_lower_bounds']) == set(contexts)
        assert set(result['pricing']) <= set(contexts) and set(result['built_sites']) <= set(contexts)
        assert result['balanced'] is job['balanced']
        matching = [r for r in rows if (r['case'], r['seed'], r['historical_budget'], r['balanced']) ==
                    (job['case'], job['seed'], job['historical_budget'], job['balanced'])]
        assert len(matching) == 1 and matching[0]['total_budget'] == 5
        observed.add((job['case'], job['seed'], job['historical_budget'], job['balanced']))
    assert len(observed) == 16 and len({x[:3] for x in observed}) == 8
    write(OUT/'verification_scope_guard.json', dict(observed_utc=datetime.now(timezone.utc).isoformat(),
        jobs=16, states=8, original_domains_and_counts=True, registered_total_budget_parameters=True,
        original_verifier_sha256=sha(OUT/'verify.py'), no_new_solve_in_guard=True))
    from verify import main as verify
    verify()


if __name__ == '__main__':
    main()
