"""Compare two frozen E1c studies without discarding failed primary attempts."""
from __future__ import annotations

import argparse
from collections import defaultdict
from pathlib import Path
import statistics

from experiments.analysis.analyze_thesis_stages import primary_rows, diagnostics, table
from experiments.runs import run_thesis_completion as driver


def key(row):
    return tuple(row[name] for name in ('case', 'model', 'method', 'seed', 'budget'))


def indexed(items):
    result = {}
    for item in items:
        if key(item) in result:
            raise ValueError('Duplicate matched job; compare one version per study')
        result[key(item)] = item
    return result


def normalized_case(study, manifest, job):
    # Frozen cases contain absolute references. Compare their actual registered
    # input hashes rather than the location of the two independent freezes.
    paths = {}
    for root in (study, Path(manifest['original_root'])):
        for relative, digest in manifest['input_hashes'].items():
            paths[str(root / relative)] = digest

    def visit(value):
        if isinstance(value, dict):
            return {k: visit(v) for k, v in value.items()}
        if isinstance(value, list):
            return [visit(v) for v in value]
        if isinstance(value, str) and value in paths:
            return {'frozen_input_sha256': paths[value]}
        return value
    return visit(driver.read(study / job['case_path']))


def verify_matching(left_study, left, right_study, right):
    a, b = indexed(left['jobs']), indexed(right['jobs'])
    if set(a) != set(b):
        raise ValueError('Candidate must retain every registered case/model/seed/budget')
    for match in a:
        for field in ('counts', 'iterations', 'family', 'split', 'diagnostics'):
            if a[match].get(field) != b[match].get(field):
                raise ValueError(f'Matched job differs in {field}: {match}')
        if normalized_case(left_study, left, a[match]) != normalized_case(right_study, right, b[match]):
            raise ValueError(f'Frozen physical/configuration inputs differ: {match}')


def per_k_diagnostics(rows, jobs, label):
    """Sum every visit to a K; absent observations are not zero-time solves."""
    planned = indexed(jobs)
    output = []
    for row in rows:
        path = row.get('_path')
        file = path / 'candidate_diagnostics.json' if path is not None else None
        observed = file is not None and file.exists()
        candidates = driver.read(file) if observed else []
        allowed = planned[key(row)]['counts']
        if any(c['batch_count'] not in allowed for c in candidates):
            raise ValueError('Diagnostic K outside registered domain')
        for count in allowed:
            visits = [c for c in candidates if c['batch_count'] == count]
            elapsed = [c['returned'] - c['started'] for c in visits]
            if any(t < 0 for t in elapsed):
                raise ValueError('Candidate return time precedes its start')
            costs = [c['info']['objective'] for c in visits
                     if c.get('child_audit_valid') and c['info'].get('objective') is not None]
            output.append(dict(case=row['case'], model=row['model'], seed=row['seed'],
                budget=row['budget'], split=row['split'], version_label=label, k=count,
                call_status=row['status'], diagnostics_observed=observed, visits=len(visits),
                measured_visit_seconds=sum(elapsed) if observed else None,
                first_started=visits[0]['started'] if visits else None,
                last_returned=visits[-1]['returned'] if visits else None,
                best_audited_child_cost=min(costs) if costs else None,
                infeasibility_certificates=sum(bool(c['info'].get('proven_infeasible')) for c in visits),
                statuses=';'.join(c['info']['status'] for c in visits)))
    return output


def compare(baseline, candidate, output):
    baseline, candidate, output = map(lambda p: Path(p).resolve(), (baseline, candidate, output))
    ma, a = primary_rows(baseline)
    mb, b = primary_rows(candidate)
    verify_matching(baseline, ma, candidate, mb)
    ia, ib = indexed(a), indexed(b)
    da = {(r['case'], r['model'], r['seed']): r for r in diagnostics(a)}
    db = {(r['case'], r['model'], r['seed']): r for r in diagnostics(b)}
    pairs, groups = [], defaultdict(list)
    for match in sorted(ia):
        first, second = ia[match], ib[match]
        both = first['valid'] and second['valid']
        delta = first['objective'] - second['objective'] if both else None
        record = dict(zip(('case', 'model', 'method', 'seed', 'budget'), match))
        record.update(split=first['split'], family=first['family'],
                      baseline_status=first['status'], candidate_status=second['status'],
                      baseline_valid=first['valid'], candidate_valid=second['valid'], both_valid=both,
                      baseline_cost=first['objective'] if first['valid'] else None,
                      candidate_cost=second['objective'] if second['valid'] else None,
                      cost_improvement=delta,
                      relative_improvement=delta / first['objective'] if both and first['objective'] else None)
        diagnostic_key = first['case'], first['model'], first['seed']
        for label, source in (('baseline', da), ('candidate', db)):
            diag = source.get(diagnostic_key, {})
            for metric in ('first_candidate_seconds', 'construction_seconds', 'evaluation_seconds',
                           'valid_evaluation_fraction', 'construction_successes'):
                record[f'{label}_{metric}'] = diag.get(metric)
        pairs.append(record)
        groups[first['split'], first['model']].append(record)
    summary = []
    for (split, model), members in sorted(groups.items()):
        valid = [r for r in members if r['both_valid']]
        improvements = [r['relative_improvement'] for r in valid if r['relative_improvement'] is not None]
        summary.append(dict(split=split, model=model, planned=len(members),
            baseline_valid=sum(r['baseline_valid'] for r in members),
            candidate_valid=sum(r['candidate_valid'] for r in members), both_valid=len(valid),
            better=sum(r['cost_improvement'] > 1e-8 for r in valid),
            worse=sum(r['cost_improvement'] < -1e-8 for r in valid),
            tied=sum(abs(r['cost_improvement']) <= 1e-8 for r in valid),
            mean_relative_improvement=statistics.mean(improvements) if improvements else None))
    output.mkdir(parents=True, exist_ok=True)
    table(output / 'matched_candidates.csv', pairs)
    table(output / 'split_summary.csv', summary)
    table(output / 'per_k_diagnostics.csv', per_k_diagnostics(a, ma['jobs'], 'baseline')
          + per_k_diagnostics(b, mb['jobs'], 'candidate'))
    finished = all(r['status'] not in ('NOT_RUN', 'RUNNING') for r in a + b)
    evidence = dict(baseline=str(baseline), candidate=str(candidate), complete=finished,
                    pairs=len(pairs), baseline_manifest_sha256=driver.sha(baseline / 'manifest.json'),
                    candidate_manifest_sha256=driver.sha(candidate / 'manifest.json'), summary=summary)
    driver.write(output / 'comparison.json', evidence)
    return evidence


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--baseline', required=True)
    parser.add_argument('--candidate', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    print(compare(args.baseline, args.candidate, args.output))
