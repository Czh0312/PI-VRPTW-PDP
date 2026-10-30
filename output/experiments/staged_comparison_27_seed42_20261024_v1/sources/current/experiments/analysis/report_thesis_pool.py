"""Final E4 first-attempt audits and per-pool descriptive resource tables."""
import argparse
import math
from collections import defaultdict
from pathlib import Path
from statistics import mean, stdev

from experiments.analysis.analyze_thesis_stages import primary_rows, table
from experiments.runs import run_thesis_completion as driver
from models.common.cycle.audit import audit_solution
from models.common.cycle.instance import load_instance
from tools.configuration.loader import load_routing_case


def report(study):
    study = Path(study).resolve()
    manifest, rows = primary_rows(study)
    if any(r['status'] in ('NOT_RUN', 'RUNNING') for r in rows):
        raise ValueError('Final pool report requires all registered first attempts')
    records, groups = [], defaultdict(list)
    for row in rows:
        record = dict(id=row['id'], case=row['reference_case'], pool=row['fixed_pool'],
                      seed=row['seed'], method=row['method'], status=row['status'], valid=row['valid'])
        if row['valid']:
            config, fleet = load_routing_case(study/row['case_path'], use_pool_config=False)
            problem = load_instance(config, fleet)
            path = row['_path']/'result.json'
            result = driver.read(path)
            if (problem.pool_min != row['fixed_pool'] or problem.pool_max != row['fixed_pool'] or
                    result['container_pool_size'] != row['fixed_pool'] or
                    result['order_batch_assignment'] != config['joint_batch_options']['fixed_assignment']):
                raise ValueError('Fixed E4 decisions differ from the registered input')
            saved = driver.read(row['_path']/'audit.json')
            if saved['result_sha256'] != driver.sha(path):
                raise ValueError('Original audit/result hash mismatch')
            audit = audit_solution(problem, result)
            if (not audit['valid'] or not audit['periodic_replay']['valid'] or
                    audit['periodic_replay']['cycles'] != 2 or
                    not math.isclose(audit['objective'], row['objective'], abs_tol=1e-6, rel_tol=1e-7)):
                raise ValueError(f'Independent final audit failed: {row["id"]}')
            driver.write(study/'final_audits'/f'{row["id"]}.json', dict(
                result_sha256=driver.sha(path), case_sha256=driver.sha(study/row['case_path']),
                solver_called=False, audit=audit))
            record.update(objective=audit['objective'], lower_bound=row.get('best_bound'),
                proven_optimal=row['proven_optimal'], result_sha256=driver.sha(path),
                minimum_pool=audit['minimum_pool_for_schedule'],
                e1_work_minutes=audit['time_metrics']['working_minutes']['e1'],
                e2_work_minutes=audit['time_metrics']['working_minutes']['e2'],
                e1_trip_count=audit['e1_trip_count'], e2_trip_count=audit['e2_trip_count'],
                **audit['cost']['components'])
        records.append(record)
        groups[row['reference_case'], row['fixed_pool']].append(record)
    table(study/'tables/pool_resource_details.csv', records)
    summaries = []
    for (case, pool), members in sorted(groups.items()):
        valid = [r for r in members if r['valid']]
        values = [r['objective'] for r in valid]
        summary = dict(case=case, pool=pool, planned=len(members), valid=len(valid),
            mean=mean(values) if values else None, stdev=stdev(values) if len(values)>1 else None,
            best=min(values) if values else None, worst=max(values) if values else None,
            optimal=sum(r.get('proven_optimal', False) for r in members))
        for key in ('container_pool', 'e1_distance', 'e2_distance', 'e1_work', 'e2_work',
                    'e1_work_minutes', 'e2_work_minutes', 'e1_trip_count', 'e2_trip_count'):
            summary[f'mean_{key}'] = mean(r[key] for r in valid) if valid else None
        summaries.append(summary)
    table(study/'tables/pool_resource_summary.csv', summaries)
    review = dict(planned=len(rows), terminal=len(rows), fresh_audits=sum(r['valid'] for r in records),
        pool_conditions=len(groups), analysis_source_sha256=driver.sha(Path(__file__)), solver_called=False)
    driver.write(study/'pool_final_review.json', review)
    print(review)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--study', required=True)
    report(parser.parse_args().study)
