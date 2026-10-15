"""Final E5 fresh physical audits and seed-matched price response summaries."""
import argparse
from collections import defaultdict
import math
from pathlib import Path
from statistics import mean, stdev

from experiments.analysis.analyze_thesis_stages import primary_rows, table
from experiments.analysis.thesis_price_matching import validate_price_results
from experiments.analysis.report_thesis_ablation import source_family
from experiments.runs import run_thesis_completion as driver
from models.common.cycle.audit import audit_solution
from models.common.cycle.instance import load_instance
from models.cost.joint_batch import JointCostProfile
from tools.configuration.loader import load_routing_case


def summarize(members):
    valid = [r for r in members if r['valid']]
    paired = [r for r in valid if r.get('repriced') is not None]
    deltas = [r['repriced']-r['objective'] for r in paired]
    costs = [r['objective'] for r in valid]
    out = dict(planned=len(members), valid=len(valid), paired=len(paired),
        mean_cost=mean(costs) if costs else None,
        stdev_cost=stdev(costs) if len(costs)>1 else None,
        best_cost=min(costs) if costs else None, worst_cost=max(costs) if costs else None,
        better=sum(d > 1e-6 for d in deltas), worse=sum(d < -1e-6 for d in deltas),
        tied=sum(abs(d) <= 1e-6 for d in deltas),
        mean_paired_repriced=mean(r['repriced'] for r in paired) if paired else None,
        mean_paired_reoptimized=mean(r['objective'] for r in paired) if paired else None,
        mean_improvement_pct=mean(100*(r['repriced']-r['objective'])/r['repriced']
            for r in paired if r['repriced']) if any(r['repriced'] for r in paired) else None)
    for key in ('pool', 'k', 'delivery_batches', 'e1_trip_count', 'e2_trip_count',
                'e1_work_minutes', 'e2_work_minutes', 'container_pool', 'e1_distance',
                'e2_distance', 'e1_work', 'e2_work', 'solve_wall_seconds'):
        values = [r[key] for r in valid if r.get(key) is not None]
        out[f'mean_{key}'] = mean(values) if values else None
    return out


def report(study):
    study = Path(study).resolve()
    manifest, rows = primary_rows(study)
    if any(r['status'] in ('NOT_RUN', 'RUNNING') for r in rows):
        raise ValueError('Final price report requires every registered first attempt')
    table(study/'tables/price_input_matching.csv', validate_price_results(study, rows))
    audits, profiles, families, records = {}, {}, {}, []
    for row in rows:
        cfg, fleet = load_routing_case(study/row['case_path'], use_pool_config=False)
        raw = driver.read(study/row['case_path'])
        metadata = driver.read(Path(raw['dataset_metadata'])) if raw.get('dataset_metadata') else {}
        families[row['base_case']] = source_family(row['family'], metadata)
        economic = raw['economic_config']
        profiles[row['id']] = JointCostProfile(driver.read(Path(economic)) if isinstance(economic, str) else economic)
        if not row['valid']:
            continue
        path = row['_path']/'result.json'
        if driver.read(row['_path']/'audit.json')['result_sha256'] != driver.sha(path):
            raise ValueError('Saved audit/result hash mismatch')
        audit = audit_solution(load_instance(cfg, fleet), driver.read(path))
        if (not audit['valid'] or not audit['periodic_replay']['valid'] or
                audit['periodic_replay']['cycles'] != 2 or
                not math.isclose(audit['objective'], row['objective'], rel_tol=1e-7, abs_tol=1e-6)):
            raise ValueError(f'Fresh physical audit failed: {row["id"]}')
        audits[row['id']] = audit
        driver.write(study/'final_audits'/f'{row["id"]}.json', dict(
            result_sha256=driver.sha(path), case_sha256=driver.sha(study/row['case_path']),
            solver_called=False, audit=audit))
    baseline = {(r['base_case'], r['model'], r['seed']): r for r in rows if r['price_factor'] == 1}
    for row in rows:
        base = baseline[row['base_case'], row['model'], row['seed']]
        record = dict(id=row['id'], case=row['base_case'], family=families[row['base_case']],
            model=row['model'], seed=row['seed'], factor=row['price_factor'], status=row['status'],
            valid=row['valid'], baseline_valid=base['valid'], baseline_id=base['id'],
            objective=row['objective'], repriced=None, pool=row.get('pool'), k=row.get('k'),
            delivery_batches=row.get('delivery_batches'), solve_wall_seconds=row.get('solve_wall_seconds'))
        if base['valid']:
            record['repriced'] = profiles[row['id']].evaluate(audits[base['id']], base['pool'])['total']
            record['baseline_pool'] = base['pool']
            # Price-only changes leave the audited physical witness unchanged.
            expected = base['objective'] + (profiles[row['id']].rates['container_pool_cost_per_period']-
                profiles[base['id']].rates['container_pool_cost_per_period'])*base['pool']
            if not math.isclose(record['repriced'], expected, abs_tol=1e-6, rel_tol=1e-7):
                raise ValueError('Fixed-schedule repricing disagrees with pool-only difference')
        if row['valid']:
            audit = audits[row['id']]
            record.update(e1_trip_count=audit['e1_trip_count'], e2_trip_count=audit['e2_trip_count'],
                e1_work_minutes=audit['time_metrics']['working_minutes']['e1'],
                e2_work_minutes=audit['time_metrics']['working_minutes']['e2'], **audit['cost']['components'])
        records.append(record)
    table(study/'tables/price_final_pairs.csv', records)
    grouped = defaultdict(list)
    for record in records:
        grouped[record['case'], record['model'], record['factor']].append(record)
    summaries = [dict(case=case, family=families[case], model=model, factor=factor, **summarize(members))
        for (case, model, factor), members in sorted(grouped.items())]
    table(study/'tables/price_instance_summary.csv', summaries)
    family_rows = []
    for family in sorted(set(families.values())):
        for model in (6, 7, 8):
            for factor in (.5, 1., 2.):
                members = [r for r in summaries if (r['family'], r['model'], r['factor']) == (family, model, factor)]
                values = [r['mean_improvement_pct'] for r in members if r['mean_improvement_pct'] is not None]
                family_rows.append(dict(family=family, model=model, factor=factor, cases=len(members),
                    paired=sum(r['paired'] for r in members),
                    mean_case_improvement_pct=mean(values) if values else None))
    table(study/'tables/price_family_summary.csv', family_rows)
    table(study/'tables/price_incumbent_reversals.csv', [r for r in records
        if r['valid'] and r['repriced'] is not None and r['objective'] > r['repriced']+1e-6])
    review = dict(planned=len(rows), terminal=len(rows), fresh_audits=len(audits),
        matched_price_triplets=len(rows)//3, source_families=len(set(families.values())),
        analysis_source_sha256=driver.sha(Path(__file__)), solver_called=False)
    driver.write(study/'price_final_review.json', review)
    print(review)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--study', required=True)
    report(parser.parse_args().study)
