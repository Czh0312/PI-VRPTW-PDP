"""Pair every registered run; keep failures, regressions and held-out cases."""
import argparse
import json
from pathlib import Path
import statistics


def summarize(folder):
    manifest = json.loads((folder/'benchmark_manifest.json').read_text())
    phases = {phase: json.loads((folder/phase/'summary.json').read_text()) for phase in ('before', 'after')}
    key = lambda row: (row['family'], row['model'], row['solver'], row['seed'], row['budget'])
    expected = {key(row) for row in manifest['jobs']}
    indexed = {}
    for phase, rows in phases.items():
        indexed[phase] = {key(row): row for row in rows}
        if len(rows) != len(expected) or set(indexed[phase]) != expected:
            raise ValueError(f'{phase}: incomplete or duplicate registered runs')
    hashes = {phase: json.loads((folder/phase/'hashes.json').read_text()) for phase in phases}
    if hashes['before']['inputs'] != hashes['after']['inputs']:
        raise ValueError('Frozen input hashes differ')
    pairs = []
    for job in manifest['jobs']:
        a, b = indexed['before'][key(job)], indexed['after'][key(job)]
        valid = a['valid'] and b['valid']
        delta = (100*(b['objective']/a['objective']-1) if valid and a['objective'] else
                 0 if valid and a['objective'] == b['objective'] else None)
        pairs.append(dict(family=job['family'], model=job['model'], solver=job['solver'], seed=job['seed'],
                          held_out=job['family'] in manifest['unseen_families'],
                          before=a, after=b, objective_change_pct=delta))
    groups = []
    for scope in ('all', 'tuning', 'held_out'):
        for method in ('exact', 'alns'):
            for model in range(1, 5):
                rows = [r for r in pairs if r['model'] == model and r['solver'] == method
                        and (scope == 'all' or r['held_out'] == (scope == 'held_out'))]
                changes = [r['objective_change_pct'] for r in rows if r['objective_change_pct'] is not None]
                speed = [r['before']['wall']/r['after']['wall'] for r in rows
                         if r['before'].get('optimal') and r['after'].get('optimal')
                         and r['before']['valid'] and r['after']['valid']]
                groups.append(dict(scope=scope, solver=method, model=model, runs=len(rows),
                    valid_before=sum(r['before']['valid'] for r in rows), valid_after=sum(r['after']['valid'] for r in rows),
                    periodic_after=sum(r['after'].get('periodic', False) for r in rows),
                    optimal_before=sum(r['before'].get('optimal', False) for r in rows),
                    optimal_after=sum(r['after'].get('optimal', False) for r in rows),
                    paired_valid=len(changes), improved=sum(d < -1e-7 for d in changes),
                    unchanged=sum(abs(d) <= 1e-7 for d in changes), worsened=sum(d > 1e-7 for d in changes),
                    mean_objective_change_pct=statistics.mean(changes) if changes else None,
                    common_optimal_speedup_median=statistics.median(speed) if speed else None))
    variability = []
    for family in dict.fromkeys(job['family'] for job in manifest['jobs']):
        for model in range(1, 5):
            rows = [r for r in pairs if r['family'] == family and r['model'] == model and r['solver'] == 'alns']
            entry = dict(family=family, model=model, held_out=family in manifest['unseen_families'])
            for phase in phases:
                values = [r[phase]['objective'] for r in rows if r[phase]['valid']]
                mean = statistics.mean(values) if values else None
                std = statistics.pstdev(values) if values else None
                entry[phase] = dict(valid=len(values), mean=mean, std=std,
                                    cv=std/mean if mean else 0 if mean == 0 else None,
                                    best=min(values) if values else None, worst=max(values) if values else None)
            variability.append(entry)
    return dict(input_hashes_equal=True, registered_runs=len(expected), groups=groups,
                variability=variability, pairs=pairs,
                limitations='Three seeds, sequential timing, exploratory tuning; no significance or universal-speed claim.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    report = summarize(args.output)
    (args.output/'comparison.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
    for row in report['groups']:
        print(json.dumps(row, ensure_ascii=False))


if __name__ == '__main__':
    main()
