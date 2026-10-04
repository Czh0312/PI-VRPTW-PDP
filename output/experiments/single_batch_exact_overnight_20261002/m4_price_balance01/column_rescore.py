"""Ex-post original-price rescore of ALL balanced returned columns; no solve."""
from pathlib import Path
from datetime import datetime, timezone
import math
import time
from common import OUT, read, write, frozen_cases, load_case, sha
from balance import transformed_prices


def main():
    assert not (OUT/'column_rescore_summary.json').exists()
    begun = time.perf_counter()
    cases = {case: load_case(folder) for _, case, folder in frozen_cases()}
    records = []
    groups = []
    for path in read(OUT/'prices5/jobs.json'):
        job = read(Path(path))
        if not job['balanced']:
            continue
        problem = cases[job['case']]
        history = read(job['fixed_history_path'])
        original, _ = transformed_prices(problem, history, False)
        balanced, _ = transformed_prices(problem, history, True)
        result = read(Path(job['result_path']))
        before = len(records)
        for site, price in result['pricing'].items():
            for index, column in enumerate(price['columns']):
                tasks = [tuple(k) for k in column['tasks']]
                assert tasks and len(set(tasks)) == len(tasks)
                points = [site]+[problem.order_map[o].customer for o, k in tasks]+[site]
                cost = math.fsum(problem.distance(a, b) for a, b in zip(points, points[1:]))
                assert abs(cost-column['cost']) < 1e-7
                pi, nu = original[site]
                old_rc = cost-math.fsum(pi[k] for k in tasks)-nu
                pi, nu = balanced[site]
                new_rc = cost-math.fsum(pi[k] for k in tasks)-nu
                records.append(dict(case=job['case'], historical_budget=job['historical_budget'],
                    seed=job['seed'], site=site, column_index=index, tasks=[list(k) for k in tasks],
                    independent_raw_cost=cost, original_reduced_cost=old_rc,
                    balanced_reduced_cost=new_rc, original_negative=old_rc < -1e-6,
                    balanced_negative=new_rc < -1e-6, source_result_sha256=sha(Path(job['result_path'])),
                    complete_original_schedule=False, original_pricing_absence_proof=False))
        these = records[before:]
        groups.append(dict(case=job['case'], historical_budget=job['historical_budget'], seed=job['seed'],
            returned_columns=len(these), original_negative=sum(r['original_negative'] for r in these),
            original_nonnegative=sum(not r['original_negative'] for r in these)))
    assert len(groups) == 8
    summary = dict(observed_utc=datetime.now(timezone.utc).isoformat(), groups=groups,
        returned_columns=len(records), original_negative=sum(r['original_negative'] for r in records),
        original_nonnegative=sum(not r['original_negative'] for r in records),
        both_negative=sum(r['original_negative'] and r['balanced_negative'] for r in records),
        raw_distance_checks=len(records), no_solver_created=True, seconds=time.perf_counter()-begun,
        scope='Ex-post read-only rescore, not fresh root/integer evidence. Every returned row retained; no LP update or new pricing. Columns alone are not original physical schedules/UBs, and balanced no-negative result cannot prove original absence.')
    write(OUT/'column_rescore_records.json', records)
    write(OUT/'column_rescore_summary.json', summary)
    print(summary)


if __name__ == '__main__':
    main()
