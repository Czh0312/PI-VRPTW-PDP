"""Build E2/E4 only from the immutable audited common-reference attempts."""
from __future__ import annotations

import argparse
from copy import deepcopy
import math
from pathlib import Path

from experiments.runs import run_thesis_completion as driver
from experiments.analysis.analyze_thesis_stages import primary_rows
from models.common.cycle.audit import audit_solution
from models.common.cycle.instance import load_instance
from tools.configuration.loader import PROJECT_ROOT, load_routing_case
from tools.data.build_thesis_followups import base_design, job, relative, SEEDS


def portable(config):
    copied = deepcopy(config)
    for key, value in copied.items():
        if isinstance(value, str):
            try:
                copied[key] = Path(value).resolve().relative_to(PROJECT_ROOT).as_posix()
            except ValueError:
                pass
    return copied


def build(reference_study, destination):
    study, out = Path(reference_study).resolve(), Path(destination).resolve()
    manifest, rows = primary_rows(study)
    if len(rows) != 12 or any(not row['valid'] for row in rows):
        raise ValueError('All 12 preregistered references need audited feasible schedules; retain and diagnose failed references first')
    if out.exists() and any(out.iterdir()):
        raise ValueError('Refusing to overwrite a restricted design')
    out.mkdir(parents=True)
    jobs, e4, evidence, shrink = [], [], [], []
    e4_names = {'ordinary_0', 'repeated_orders_0', 'tight_pool_0', 'lade_0823_20aoi__spread_medium',
                'zhou_Set2_100-10-1_subset30__spread_medium', 'zhou_Set2_100-10-1_subset50__spread_medium'}
    for row in rows:
        config, fleet = load_routing_case(study/row['case_path'], use_pool_config=False)
        problem = load_instance(config, fleet)
        result = driver.read(row['_path']/'result.json')
        audited = audit_solution(problem, result)
        if not audited['valid']:
            raise ValueError(f'Reference independent re-audit failed: {row["case"]}')
        assignment = result['order_batch_assignment']
        if assignment != config['joint_batch_options']['fixed_assignment']:
            raise ValueError('Reference changed its registered fixed assignment')
        evidence.append(dict(case=row['case'], family=row['family'], total=problem.total,
            reference_result=relative(row['_path']/'result.json'), reference_sha256=driver.sha(row['_path']/'result.json'),
            reference_pool=result['container_pool_size'], minimum_pool_for_schedule=audited['minimum_pool_for_schedule'],
            calendar=result['candidate_schedule'], assignment=assignment, method=row['method'],
            budget=row['budget'], fixed_pool=problem.total))
        for group in 'ABCD':
            cfg = portable(config)
            options = dict(pool_min=1, pool_max=problem.total)
            if group in 'AC':
                options['fixed_assignment'] = assignment
            if group in 'AB':
                options.update(pool_min=problem.total, pool_max=problem.total)
            cfg['joint_batch_options'] = options
            target = out/f'e2_{row["case"]}_{group}.json'
            driver.write(target, cfg)
            method, seeds = ('exact', [42]) if row['size'] == 'small' else ('alns', SEEDS)
            for seed in seeds:
                item = job('e2', f'{row["case"]}_{group}', target, 6, method, 300, seed,
                           family=row['family'], group=group, size=row['size'])
                item['case'] = row['case']
                jobs.append(item)
        if row['case'] in e4_names:
            # Every batch consumes its full demand simultaneously: N >= max
            # batch demand is necessary regardless of previous cleaning.
            fixed = problem.project(assignment, result['container_pool_size'], result['batch_cutoffs'])
            lower = max(fixed.batch_demands.values())
            points = sorted({lower, *(math.ceil(problem.total*f) for f in (.50, .65, .80, 1.))})
            points = [n for n in points if lower <= n <= problem.total]
            if row['size'] == 'small':
                points = list(range(lower, problem.total+1))
            for pool in points:
                cfg = portable(config)
                cfg['joint_batch_options'] = dict(fixed_assignment=assignment, pool_min=pool, pool_max=pool)
                target = out/f'e4_{row["case"]}_n{pool}.json'
                driver.write(target, cfg)
                for seed in ([42] if row['size'] == 'small' else SEEDS):
                    item = job('e4', f'{row["case"]}_n{pool}', target, 6,
                               'exact' if row['size'] == 'small' else 'alns', 300, seed,
                               family=row['family'], fixed_pool=pool, necessary_lower_bound=lower,
                               size=row['size'], reference_case=row['case'])
                    e4.append(item)
                shrink.append(dict(case=row['case'], pool=pool, necessary_lower_bound=lower,
                    unchanged_schedule_feasible=pool >= audited['minimum_pool_for_schedule'],
                    minimum_pool_for_unchanged_schedule=audited['minimum_pool_for_schedule'],
                    reference_result=relative(row['_path']/'result.json')))
    prerequisites = [relative(study), 'output/thesis_completion/e1c_20261012_v1']
    driver.write(out/'e2_design.json', base_design('e2', jobs,
        'All 12 predefined reference cases; six small exact x4 and six medium/large ALNS x4x5; fixed original calendar/cutoffs, same cost and cold-start policy.',
        prerequisites=prerequisites))
    driver.write(out/'e4_design.json', base_design('e4', e4,
        'Three predefined small and three medium/large reference cases; fixed assignment/calendar and reoptimized routes/times; all small N from necessary lower bound, coarse fractions for larger cases.',
        prerequisites=prerequisites))
    driver.write(out/'reference_evidence.json', evidence)
    driver.write(out/'unchanged_schedule_pool_reference.json', shrink)
    print(f'E2: {len(jobs)} calls; E4 coarse stage: {len(e4)} calls. Medium/large boundary neighbors require a subsequent registered refinement.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--reference-study', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    build(args.reference_study, args.output)
