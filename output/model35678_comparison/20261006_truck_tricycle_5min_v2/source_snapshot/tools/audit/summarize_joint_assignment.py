"""Strictly pair registered exact runs by instance, model, seed and budget."""
import argparse
from collections import Counter
import json
from pathlib import Path
import statistics


def mean_or_none(values):
    values = list(values)
    return statistics.mean(values) if values else None


def summarize(folder):
    manifest = json.loads((folder/'benchmark_manifest.json').read_text())
    key = lambda row: (row['family'], row['model'], row['solver'], row['seed'], row['budget'])
    expected = {key(row) for row in manifest['jobs']}
    if len(expected) != len(manifest['jobs']):
        raise ValueError('Duplicate registered jobs')
    phases = {}
    hashes = {}
    for phase in ('before', 'after'):
        rows = json.loads((folder/phase/'summary.json').read_text())
        phases[phase] = {key(row): row for row in rows}
        if len(rows) != len(expected) or set(phases[phase]) != expected:
            raise ValueError(f'{phase}: missing or duplicate registered runs')
        hashes[phase] = json.loads((folder/phase/'hashes.json').read_text())
    if hashes['before']['inputs'] != hashes['after']['inputs']:
        raise ValueError('Frozen input hashes differ')
    pairs = []
    for job in manifest['jobs']:
        before, after = [phases[phase][key(job)] for phase in ('before', 'after')]
        paired = before['valid'] and after['valid']
        delta = (100*(after['objective']/before['objective']-1) if paired and before['objective'] else
                 0 if paired and before['objective'] == after['objective'] else None)
        pairs.append(dict(family=job['family'], model=job['model'], seed=job['seed'], budget=job['budget'],
                          held_out=job['family'] in manifest['unseen_families'], before=before, after=after,
                          objective_change_pct=delta))
    groups = []
    for scope in ('all', 'tuning', 'held_out'):
        for budget in sorted({job['budget'] for job in manifest['jobs']}):
            for model in sorted({job['model'] for job in manifest['jobs']}):
                rows = [r for r in pairs if r['budget'] == budget and r['model'] == model
                        and (scope == 'all' or r['held_out'] == (scope == 'held_out'))]
                changes = [r['objective_change_pct'] for r in rows if r['objective_change_pct'] is not None]
                common = [r for r in rows if all(r[p]['valid'] and r[p].get('optimal') for p in ('before', 'after'))]
                groups.append(dict(scope=scope, budget=budget, model=model, runs=len(rows),
                    valid_before=sum(r['before']['valid'] for r in rows),
                    valid_after=sum(r['after']['valid'] for r in rows),
                    periodic_after=sum(r['after'].get('periodic', False) for r in rows),
                    optimal_before=sum(bool(r['before'].get('optimal') and r['before']['valid']) for r in rows),
                    optimal_after=sum(bool(r['after'].get('optimal') and r['after']['valid']) for r in rows),
                    paired_valid=len(changes), improved=sum(d < -1e-7 for d in changes),
                    unchanged=sum(abs(d) <= 1e-7 for d in changes), worsened=sum(d > 1e-7 for d in changes),
                    mean_objective_change_pct=statistics.mean(changes) if changes else None,
                    mean_gap_before=mean_or_none(r['before']['gap'] for r in rows if r['before'].get('gap') is not None),
                    mean_gap_after=mean_or_none(r['after']['gap'] for r in rows if r['after'].get('gap') is not None),
                    common_optimal_count=len(common),
                    common_optimal_speedup_median=statistics.median(r['before']['wall']/r['after']['wall'] for r in common) if common else None,
                    strategies=dict(Counter(r['after'].get('assignment_strategy', 'unknown') for r in rows))))
    return dict(registered_runs=len(expected), input_hashes_equal=True, groups=groups, pairs=pairs,
                failures=[r for r in pairs if not r['before']['valid'] or not r['after']['valid']],
                regressions=[r for r in pairs if r['objective_change_pct'] is not None and r['objective_change_pct'] > 1e-7],
                limitations='One solver seed, two budgets, sequential timing; diagnostics, not a significance or universal speed claim.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    report = summarize(args.output)
    (args.output/'comparison.json').write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n')
    for row in report['groups']:
        print(json.dumps(row, ensure_ascii=False))


if __name__ == '__main__':
    main()
