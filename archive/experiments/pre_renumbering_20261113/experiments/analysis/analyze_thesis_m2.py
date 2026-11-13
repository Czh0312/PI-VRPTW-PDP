"""E6 reference errors and actual public-entry assignment diagnostics."""
from __future__ import annotations

import argparse
from collections import defaultdict
import math
from pathlib import Path
import statistics

from experiments.analysis.analyze_thesis_stages import primary_rows, table, grouped_statistics, paired_versions
from experiments.runs.run_thesis_completion import read, write, sha
from models.common.cycle.instance import load_instance
from tools.configuration.loader import load_routing_case
from tools.data.build_thesis_m2_reference import physical_signature


def reference_error(row, reference):
    both = row['valid'] and reference is not None and reference['valid']
    proven = both and reference['proven_optimal']
    optimum = reference['objective'] if proven else None
    return dict(reference_status=reference['status'] if reference else 'MISSING',
        reference_proven_optimal=bool(reference and reference['proven_optimal']),
        reference_upper=reference['objective'] if reference and reference['valid'] else None,
        reference_lower=reference.get('best_bound') if reference else None,
        absolute_optimal_error=row['objective']-optimum if proven else None,
        relative_optimal_error=(row['objective']-optimum)/optimum if proven and optimum else None,
        difference_from_reference_incumbent=row['objective']-reference['objective'] if both else None)


def assignment_diagnostics(path):
    data = read(path/'search_diagnostics.json')
    result = read(path/'result.json')
    info = result['info']
    proposals = data.get('assignment_proposals', [])
    partitions = {tuple(sorted(p['assignment'].items())) for p in proposals}
    subsolves = data.get('assignment_subsolves', [])
    valid = [s for s in subsolves if s['valid']]
    best, changes, first, last = math.inf, 0, None, None
    for solved in valid:
        if solved['info']['objective'] < best-1e-8:
            best, changes, last = solved['info']['objective'], changes+1, solved['elapsed']
            if first is None:
                first = solved['elapsed']
    initial = proposals[0]['assignment'] if proposals else {}
    final = result.get('assignment', {})
    attempts = data.get('projection_attempts', 0)
    return dict(public_algorithm=info.get('algorithm'), proposal_calls=len(proposals),
        unique_partitions=len(partitions), repeated_partition_proposals=len(proposals)-len(partitions),
        projection_attempts=attempts, successful_projections=data.get('projection_successes', 0),
        projection_success_fraction=data.get('projection_successes', 0)/attempts if attempts else None,
        projection_seconds=data.get('projection_seconds'), subsolves=len(subsolves), valid_subsolves=len(valid),
        subsolve_wall_seconds=sum(s['wall_seconds'] for s in subsolves),
        incumbent_discoveries=changes, first_valid_subsolve_return=first, best_subsolve_return=last,
        final_assignment_changes=sum(initial.get(c) != s for c, s in final.items()) if initial and final else None,
        initial_route_objective=info.get('initial_objective'), final_route_objective=info.get('objective'),
        route_operator_attempts=str(info.get('operator_attempts', {})),
        route_operator_improvements=str(info.get('operator_improvements', {})))


def analyze(studies, reference_study, output):
    out = Path(output).resolve()
    out.mkdir(parents=True, exist_ok=True)
    reference_study = Path(reference_study).resolve()
    _, references = primary_rows(reference_study)
    if len(references) != 30 or len({r['case'] for r in references}) != 30:
        raise ValueError('E6 requires exactly 30 registered reference cases')
    signatures = {}

    def signature(study, row):
        path = study/row['case_path']
        if path not in signatures:
            signatures[path] = physical_signature(load_instance(*load_routing_case(path, use_pool_config=False)))
        return signatures[path]

    reference_map = {r['case']: r for r in references}
    rows, errors, diagnostics, contradictions, sources = [], [], [], [], []
    matched_jobs, resources = None, None
    for name in studies:
        study = Path(name).resolve()
        manifest, members = primary_rows(study)
        job_fields = ('model', 'method', 'budget', 'iterations', 'counts', 'family', 'diagnostics')
        current_jobs = {(j['case'], j['seed']): {k: j.get(k) for k in job_fields} for j in manifest['jobs']}
        current_resources = {k: manifest.get(k) for k in ('workers', 'timeout_overhead', 'interpreter', 'ordering_seed')}
        if matched_jobs is not None and (matched_jobs != current_jobs or resources != current_resources):
            raise ValueError('E6 versions differ in registered call budgets, seeds, domains or resources')
        matched_jobs, resources = current_jobs, current_resources
        if len(members) != 150 or {r['case'] for r in members} != set(reference_map):
            raise ValueError('Every E6 version must retain all 30 cases and 150 planned seed calls')
        counts = defaultdict(list)
        for row in members:
            counts[row['case']].append(row['seed'])
        if any(sorted(seeds) != list(range(42, 47)) for seeds in counts.values()):
            raise ValueError('Every E6 case needs each seed 42..46 exactly once')
        sources.append(dict(study=str(study), manifest_sha256=sha(study/'manifest.json')))
        for row in members:
            reference = reference_map[row['case']]
            if signature(study, row) != signature(reference_study, reference):
                raise ValueError(f'Reference physics differs: {row["id"]}')
            errors.append(dict(case=row['case'], family=row['family'], version=row['version'], seed=row['seed'],
                status=row['status'], valid=row['valid'], objective=row['objective'], **reference_error(row, reference)))
            if row['valid'] and (reference['proven_infeasible'] or reference.get('best_bound') is not None
                    and reference['best_bound'] > row['objective']+1e-6+abs(row['objective'])*1e-7):
                contradictions.append(dict(id=row['id'], cost=row['objective'], reference_id=reference['id'],
                    reference_lower=reference.get('best_bound'), reference_infeasible=reference['proven_infeasible']))
            if '_path' in row and (row['_path']/'search_diagnostics.json').exists() and (row['_path']/'result.json').exists():
                diagnostics.append(dict(case=row['case'], version=row['version'], seed=row['seed'],
                    status=row['status'], valid=row['valid'], **assignment_diagnostics(row['_path'])))
        rows.extend(members)
    versions = list(dict.fromkeys(r['version'] for r in rows))
    if len(versions) != len(studies):
        raise ValueError('Each study must identify a different source version')
    table(out/'reference_errors.csv', errors)
    table(out/'assignment_diagnostics.csv', diagnostics)
    table(out/'per_case_statistics.csv', grouped_statistics(rows))
    pairs = paired_versions(rows, versions)
    table(out/'paired_versions.csv', pairs)
    write(out/'paired_outcomes.json', dict(planned=len(pairs), both_valid=sum(r['both_valid'] for r in pairs),
        improved=sum(r['both_valid'] and r['first_minus_second'] > 1e-6 for r in pairs),
        worsened=sum(r['both_valid'] and r['first_minus_second'] < -1e-6 for r in pairs),
        tied=sum(r['both_valid'] and abs(r['first_minus_second']) <= 1e-6 for r in pairs),
        absolute_tolerance=1e-6))
    summaries = []
    groups = defaultdict(list)
    for row in errors:
        groups[row['case'], row['version']].append(row)
    for (case, version), members in sorted(groups.items()):
        values = [r['relative_optimal_error'] for r in members if r['relative_optimal_error'] is not None]
        summaries.append(dict(case=case, version=version, planned=len(members), valid=sum(r['valid'] for r in members),
            optimal_reference_samples=len(values), mean=statistics.mean(values) if values else None,
            stdev=statistics.stdev(values) if len(values) > 1 else None, best=min(values) if values else None,
            worst=max(values) if values else None))
    table(out/'optimal_error_statistics.csv', summaries)
    descriptive_outputs(out, rows, errors, diagnostics, summaries, versions)
    result = dict(studies=sources, reference_manifest_sha256=sha(reference_study/'manifest.json'),
        planned=len(rows), terminal=sum(r['status'] not in ('NOT_RUN', 'RUNNING') for r in rows),
        valid=sum(r['valid'] for r in rows), proven_optimal_references=sum(r['valid'] and r['proven_optimal'] for r in references),
        certificate_contradictions=contradictions,
        limitations=['All first attempts remain in denominators; costs/errors conditional on audited feasibility.',
                    'Historical optima are not reused; limited exact incumbents are not called optima.',
                    'Projection fraction and unique partitions describe the instrumented public solver path.',
                    'Subsolve discovery times are measured at return, never backdated to internal local histories.'])
    write(out/'analysis.json', result)
    if contradictions:
        raise ValueError('E6 reference contradicts an audited feasible run; see analysis.json')
    lines = ['# E6 自由分仓结果', '', f'完成 {result["terminal"]}/{len(rows)}；审计有效 {result["valid"]}。',
        f'当前重新建立的精确参照中，{result["proven_optimal_references"]}/30 已证明最优。', '',
        '`optimal_error_statistics.csv` 仅统计已证明最优参照对应的有效调用；其余只列可行上界及差值。',
        '`assignment_diagnostics.csv` 记录实际公开入口的分仓提议、投影、子问题求解和路线算子。',
        '保留全部原 30 例及每例 5 个种子；同一源案例的情景变体不作为独立实例作显著性推断。', '']
    (out/'report.md').write_text('\n'.join(lines), encoding='utf-8')
    return result


def descriptive_outputs(out, rows, errors, diagnostics, summaries, versions):
    """Descriptive family summaries and plots; no independent-sample inference."""
    family_groups = defaultdict(list)
    for row in errors:
        family_groups[row['family'], row['version']].append(row)
    family_rows = []
    for (family, version), members in sorted(family_groups.items()):
        values = [r['relative_optimal_error'] for r in members if r['relative_optimal_error'] is not None]
        family_rows.append(dict(family=family, version=version, planned=len(members),
            valid=sum(r['valid'] for r in members), optimal_reference_samples=len(values),
            mean_optimal_error=statistics.mean(values) if values else None))
    table(out/'family_statistics.csv', family_rows)
    aggregates = []
    for version in versions:
        cases = [r for r in summaries if r['version'] == version]
        calls = [r for r in rows if r['version'] == version]
        observed = [r for r in diagnostics if r['version'] == version]
        attempts = sum(r['projection_attempts'] for r in observed)
        def avg(values):
            values = [v for v in values if v is not None]
            return statistics.mean(values) if values else None
        aggregates.append(dict(version=version, planned=len(calls), valid=sum(r['valid'] for r in calls),
            case_mean_optimal_error=avg(r['mean'] for r in cases),
            case_best_optimal_error=avg(r['best'] for r in cases),
            case_worst_optimal_error=avg(r['worst'] for r in cases),
            mean_within_case_stdev=avg(r['stdev'] for r in cases),
            mean_call_seconds=avg(r.get('solve_wall_seconds') for r in calls),
            diagnostics_calls=len(observed), mean_proposals=avg(r['proposal_calls'] for r in observed),
            mean_unique_partitions=avg(r['unique_partitions'] for r in observed),
            mean_first_valid_subsolve_return=avg(r['first_valid_subsolve_return'] for r in observed),
            pooled_projection_success_fraction=sum(r['successful_projections'] for r in observed)/attempts if attempts else None))
    table(out/'version_statistics.csv', aggregates)
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    directory = out/'figures'
    directory.mkdir(exist_ok=True)
    cases = sorted({r['case'] for r in summaries})
    lookup = {(r['case'], r['version']): r for r in summaries}
    fig, ax = plt.subplots(figsize=(11, 10), constrained_layout=True)
    for offset, version in enumerate(versions):
        selected = [(i, lookup[case, version]) for i, case in enumerate(cases)
                    if (case, version) in lookup and lookup[case, version]['mean'] is not None]
        ax.errorbar([100*r['mean'] for i, r in selected],
                    [i+(offset-(len(versions)-1)/2)*.22 for i, r in selected],
                    xerr=[100*(r['stdev'] or 0) for i, r in selected],
                    fmt='o', markersize=4, capsize=2, label=version)
    ax.set_yticks(range(len(cases)), [c.removeprefix('zhou_Set1_') for c in cases])
    ax.invert_yaxis()
    ax.set(xlabel='Optimal error (%) — mean +/- sample SD across five seeds', ylabel='Registered case')
    ax.legend()
    fig.savefig(directory/'optimal_errors.png', dpi=150)
    fig.savefig(directory/'optimal_errors.pdf')
    plt.close(fig)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--studies', nargs='+', required=True)
    parser.add_argument('--reference-study', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    result = analyze(args.studies, args.reference_study, args.output)
    print({key: result[key] for key in ('planned', 'terminal', 'valid', 'proven_optimal_references')})
