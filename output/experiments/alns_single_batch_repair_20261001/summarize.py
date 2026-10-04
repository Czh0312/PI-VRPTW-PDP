"""Retain every registered pair and apply the predeclared model-wise rule."""
import csv
import json
import statistics
import sys
from pathlib import Path

OUT = Path(__file__).resolve().parent


def main():
    prefix = sys.argv[1] if len(sys.argv) > 1 else ''
    candidate = sys.argv[2] if len(sys.argv) > 2 else 'after_source'
    jobs = json.loads((OUT/(prefix+'quality_manifest.json')).read_text())['jobs']
    rows = json.loads((OUT/(prefix+'quality')/'summary.json').read_text())
    assert len(rows) == 2*len(jobs)
    pairs = []
    for index, job in enumerate(jobs):
        sources = {r['source']: r for r in rows if r['index'] == index}
        assert set(sources) == {'before_source', candidate}
        a, b = sources['before_source'], sources[candidate]
        comparable = a['valid'] and b['valid'] and a.get('objective') is not None and b.get('objective') is not None
        pairs.append(dict(family=job['family'], model=job['model'], seed=job['seed'], budget=job['budget'],
            before_valid=a['valid'], after_valid=b['valid'], before_periodic=a.get('periodic'),
            after_periodic=b.get('periodic'), before_objective=a.get('objective'), after_objective=b.get('objective'),
            change_percent=100*(b['objective']/a['objective']-1) if comparable else None,
            before_wall=a.get('wall'), after_wall=b.get('wall'),
            before_iterations=a.get('iterations'), after_iterations=b.get('iterations')))
    with (OUT/(prefix+'paired_results.csv')).open('w') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(pairs[0]))
        writer.writeheader(); writer.writerows(pairs)
    summary = []
    for budget in sorted({p['budget'] for p in pairs}):
        for model in sorted({p['model'] for p in pairs}):
            group = [p for p in pairs if p['model'] == model and p['budget'] == budget]
            ds = [p['change_percent'] for p in group if p['change_percent'] is not None]
            summary.append(dict(model=model, budget=budget, pairs=len(group),
                mean_change_percent=statistics.mean(ds) if ds else None,
                improved=sum(d < -1e-7 for d in ds), equal=sum(abs(d) <= 1e-7 for d in ds),
                worse=sum(d > 1e-7 for d in ds), maximum_regression_percent=max(0, max(ds)) if ds else None,
                all_valid=len(ds) == len(group) and all(p['after_periodic'] and p['before_periodic'] for p in group)))
    decisions = []
    for model in sorted({p['model'] for p in pairs}):
        short = next(r for r in summary if r['model'] == model and r['budget'] == 5)
        long = next(r for r in summary if r['model'] == model and r['budget'] == 15)
        met = (short['all_valid'] and long['all_valid'] and short['improved'] >= short['pairs']/2
            and short['mean_change_percent'] <= 0 and long['mean_change_percent'] <= 0
            and max(short['maximum_regression_percent'], long['maximum_regression_percent']) <= 5)
        decisions.append(dict(model=model, adopt=met))
    result = dict(pairs=len(pairs), valid_before=sum(p['before_valid'] for p in pairs),
        valid_after=sum(p['after_valid'] for p in pairs), periodic_after=sum(p['after_periodic'] is True for p in pairs),
        summary=summary, decisions=decisions,
        max_wall_overrun=max(p['after_wall']-p['budget'] for p in pairs if p['after_wall'] is not None),
        scope=('Periodic destruction of audited incumbent' if prefix else 'Random repair ordering added to operator pool'),
        limitations=['Descriptive results only; three short and two long seeds.',
            'Validation uses different seeds and mother source than the development pilot; mother geometries were observed in earlier rounds.',
            'Nested50/100 are dependent. Model-wise adoption uses these validation observations and is not an unbiased population estimate.',
            'Alternated serial processes; machine load not completely controlled.',
            'No global optimum, significance, full-dataset or longer-than-15-second claims.'])
    (OUT/(prefix+'comparison.json')).write_text(json.dumps(result, indent=2))
    print(json.dumps(summary, indent=2)); print('decisions', decisions)


if __name__ == '__main__':
    main()
