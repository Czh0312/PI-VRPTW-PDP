"""Final E2 matched contrasts, family summaries and fresh physical audits."""
import argparse
import json
import math
from pathlib import Path
from statistics import mean

from experiments.analysis.analyze_thesis_stages import primary_rows, ablation_contrasts, table
from experiments.runs import run_thesis_completion as driver
from models.common.cycle.audit import audit_solution
from models.common.cycle.instance import load_instance
from tools.configuration.loader import load_routing_case


CONTRASTS = ('A_minus_B', 'A_minus_C', 'C_minus_D', 'B_minus_D', 'A_minus_D',
             'interaction_A_minus_B_minus_C_plus_D')


def summarize(members, contrast):
    valid = [r for r in members if r.get(contrast) is not None]
    values = [r[contrast] for r in valid]
    relative = [100*r[contrast]/r['A_upper'] for r in valid if r.get('A_upper')]
    return dict(planned=len(members), valid=len(valid),
                positive=sum(v > 1e-6 for v in values),
                negative=sum(v < -1e-6 for v in values),
                tied=sum(abs(v) <= 1e-6 for v in values),
                mean_difference=mean(values) if values else None,
                mean_pct_of_A=mean(relative) if relative else None)


def common_config(config):
    config = dict(config)
    for key in ('data', 'orders', 'routing_config', 'operating_policy', 'economic_config',
                'dataset_metadata', 'distance_matrix', 'duration_matrix'):
        value = config.get(key)
        if isinstance(value, str):
            config[key] = driver.sha(Path(value))
    options = dict(config['joint_batch_options'])
    for key in ('fixed_assignment', 'pool_min', 'pool_max'):
        options.pop(key, None)
    config['joint_batch_options'] = options
    return config


def source_family(registered, metadata):
    # LaDe 20/30 AOI are related selections from the same spatial source block.
    # Keep the registered labels in the mapping, but aggregate conservatively.
    if metadata.get('source_kind') == 'lade_derived_scenario':
        return metadata['spatial_block']
    return registered


def report(study, evidence_path):
    study = Path(study).resolve()
    manifest, rows = primary_rows(study)
    if any(r['status'] in ('RUNNING', 'NOT_RUN') for r in rows):
        raise ValueError('Final report requires all first attempts')
    evidence = {r['case']: r for r in driver.read(Path(evidence_path))}
    common, domains, audit_rows, metrics, families, lineage = {}, {}, [], [], {}, {}
    for row in rows:
        case, group = row['case'], row['group']
        config = driver.read(study/row['case_path'])
        metadata = driver.read(Path(config['dataset_metadata'])) if config.get('dataset_metadata') else {}
        families[case] = source_family(row['family'], metadata)
        lineage[case] = dict(case=case, registered_family=row['family'],
            analysis_family=families[case], source_kind=metadata.get('source_kind'),
            spatial_block=metadata.get('spatial_block'))
        normalized = common_config(config)
        if case in common and normalized != common[case]:
            raise ValueError(f'Unmatched physical/economic/calendar input: {case}')
        common[case] = normalized
        domain = (row['counts'], row['model'], row['version'], row['budget'], row['method'])
        if case in domains and domain != domains[case]:
            raise ValueError(f'Unmatched comparison domain: {case}')
        domains[case] = domain
        options, ref = config['joint_batch_options'], evidence[case]
        if options.get('fixed_assignment') != (ref['assignment'] if group in 'AC' else None):
            raise ValueError('Fixed assignment differs from preregistered reference')
        if options['pool_min'] != (ref['reference_pool'] if group in 'AB' else 1):
            raise ValueError('Pool lower bound differs from design')
        if options['pool_max'] != ref['reference_pool']:
            raise ValueError('Pool upper bound differs from design')
        if driver.sha(Path(ref['reference_result'])) != ref['reference_sha256']:
            raise ValueError('Reference result hash changed')
        if not row['valid']:
            audit_rows.append(dict(id=row['id'], valid=False, status=row['status']))
            continue
        result_path = row['_path']/'result.json'
        result = driver.read(result_path)
        if group in 'AC' and result['order_batch_assignment'] != ref['assignment']:
            raise ValueError('Result violates fixed reference assignment')
        if group in 'AB' and result['container_pool_size'] != ref['reference_pool']:
            raise ValueError('Result violates fixed reference pool')
        saved = driver.read(row['_path']/'audit.json')
        if saved['result_sha256'] != driver.sha(result_path):
            raise ValueError('Audit/result hash mismatch')
        cfg, fleet = load_routing_case(study/row['case_path'], use_pool_config=False)
        problem = load_instance(cfg, fleet)
        audited = audit_solution(problem, result)
        if not audited['valid'] or not audited['periodic_replay']['valid']:
            raise ValueError(f'Fresh physical audit failed: {row["id"]}')
        if not math.isclose(audited['objective'], row['objective'], rel_tol=1e-7, abs_tol=1e-6):
            raise ValueError('Fresh cost disagrees with recorded objective')
        driver.write(study/'final_audits'/f'{row["id"]}.json', dict(
            result_sha256=driver.sha(result_path), case_sha256=driver.sha(study/row['case_path']),
            solver_called=False, audit=audited))
        audit_rows.append(dict(id=row['id'], case=case, group=group, valid=True,
            result_sha256=driver.sha(result_path), periodic_valid=True,
            reference_pool=ref['reference_pool'], reference_minimum_pool=ref['minimum_pool_for_schedule']))
        metrics.append(dict(case=case, group=group, seed=row['seed'], family=row['family'],
            method=row['method'], objective=row['objective'], pool=row['pool'],
            minimum_pool=row['minimum_pool'], reference_pool=ref['reference_pool'],
            reference_minimum_pool=ref['minimum_pool_for_schedule'],
            delivery_batches=row['delivery_batches'], iterations=row.get('iterations'),
            solve_wall_seconds=row.get('solve_wall_seconds'),
            e1_work_minutes=audited['time_metrics']['working_minutes']['e1'],
            e2_work_minutes=audited['time_metrics']['working_minutes']['e2'],
            e1_trip_count=audited['e1_trip_count'], e2_trip_count=audited['e2_trip_count'],
            **row['cost_components']))
    table(study/'tables/final_audit_index.csv', audit_rows)
    table(study/'tables/ablation_resources.csv', metrics)
    pairs = ablation_contrasts(rows)
    if any(v for r in pairs for k, v in r.items() if k.endswith('_certificate_conflict')):
        raise ValueError('Inclusion certificate conflict requires investigation')
    table(study/'tables/ablation_family_mapping.csv', list(lineage.values()))
    case_summaries, family_summaries, overall = [], [], []
    for contrast in CONTRASTS:
        for case in sorted(common):
            rs = [r for r in pairs if r['case'] == case]
            case_summaries.append(dict(case=case, family=families[case], method=rs[0]['method'],
                contrast=contrast, **summarize(rs, contrast)))
        for method in ('exact', 'alns'):
            rs = [r for r in pairs if r['method'] == method]
            overall.append(dict(method=method, contrast=contrast, **summarize(rs, contrast)))
            for family in sorted({families[r['case']] for r in rs}):
                cs = [r for r in case_summaries if r['family'] == family and r['method'] == method and r['contrast'] == contrast]
                good = [r for r in cs if r['mean_pct_of_A'] is not None]
                family_summaries.append(dict(family=family, method=method, contrast=contrast,
                    cases=len(cs), valid_cases=len(good), planned_pairs=sum(r['planned'] for r in cs),
                    valid_pairs=sum(r['valid'] for r in cs),
                    mean_case_pct_of_A=mean(r['mean_pct_of_A'] for r in good) if good else None))
    table(study/'tables/ablation_instance_summary.csv', case_summaries)
    table(study/'tables/ablation_family_summary.csv', family_summaries)
    table(study/'tables/ablation_pair_summary.csv', overall)
    reversals = []
    for pair in pairs:
        for a, b in ('AB', 'AC', 'CD', 'BD', 'AD'):
            if pair[f'{a}_to_{b}_incumbent_reversal']:
                reversals.append(dict(case=pair['case'], seed=pair['seed'], method=pair['method'],
                    contrast=f'{a}_minus_{b}', restricted_upper=pair[f'{a}_upper'],
                    relaxed_upper=pair[f'{b}_upper'], difference=pair[f'{a}_minus_{b}'],
                    diagnosis='independent_cold_start_incumbent_reversal_not_certificate_conflict'))
    table(study/'tables/ablation_reversals.csv', reversals)
    # Family summaries give every case equal weight within its original family.
    from experiments.analysis.analyze_thesis_mechanisms import plotting
    plt = plotting()
    fig, ax = plt.subplots(figsize=(10, 4.5), constrained_layout=True)
    labels = ('A-B', 'A-C', 'C-D', 'B-D', 'A-D', 'A-B-C+D')
    for family in sorted({r['family'] for r in family_summaries if r['method'] == 'alns'}):
        points = [next(r for r in family_summaries if r['method'] == 'alns' and r['family'] == family and r['contrast'] == c)['mean_case_pct_of_A'] for c in CONTRASTS]
        ax.plot(range(6), points, marker='o', label=family)
    ax.axhline(0, color='gray', lw=.8)
    ax.set(xticks=range(6), xticklabels=labels, ylabel='Mean contrast / matched A cost (%)',
           title='E2 ALNS: within-case seed means, then within-family case means')
    ax.legend(fontsize=8)
    for suffix in ('png', 'pdf'):
        fig.savefig(study/'figures'/f'e2_family_contrasts.{suffix}', dpi=170)
    plt.close(fig)
    summary = dict(planned=len(rows), valid=sum(r['valid'] for r in rows),
        fresh_audits=sum(r['valid'] for r in audit_rows), matched_quadruples=len(pairs),
        certificate_conflicts=0, incumbent_reversals=len(reversals),
        method_summary=overall, reference_evidence_sha256=driver.sha(Path(evidence_path)),
        analysis_source_sha256=driver.sha(Path(__file__)), solver_called=False)
    driver.write(study/'ablation_review.json', summary)
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--study', required=True)
    parser.add_argument('--reference-evidence', required=True)
    args = parser.parse_args()
    report(args.study, args.reference_evidence)
