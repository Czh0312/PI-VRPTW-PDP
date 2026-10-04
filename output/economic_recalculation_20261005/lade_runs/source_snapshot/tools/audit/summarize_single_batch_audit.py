"""Summarize paired diagnostic runs without dropping failures or regressions."""
import argparse
import json
from pathlib import Path
import statistics


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--after', default='verified')
    args = parser.parse_args(argv)
    read = lambda path: json.loads((args.output/path).read_text())
    before, after = read(Path('before/summary.json')), read(Path(args.after)/'summary.json')
    manifest = read(Path('benchmark_manifest.json'))
    assert len(before) == len(after) == len(manifest['jobs']), 'Incomplete benchmark'
    assert read(Path('before/hashes.json'))['inputs'] == read(Path(args.after)/'hashes.json')['inputs']
    paired, groups = [], []
    for old, new in zip(before, after):
        keys = ('family', 'model', 'solver', 'seed', 'budget', 'iterations')
        # The summary's iterations field is actual work, not the preset cap.
        assert all(old[k] == new[k] for k in keys if k != 'iterations')
        change = (100*(new['objective']-old['objective'])/old['objective']
                  if old['valid'] and new['valid'] and old['objective'] else None)
        paired.append(dict(family=old['family'], model=old['model'], solver=old['solver'], seed=old['seed'],
            before_status=old['status'], after_status=new['status'],
            before_valid=old['valid'], after_valid=new['valid'],
            before_objective=old.get('objective'), after_objective=new.get('objective'),
            distance_change_percent=change, before_wall=old.get('wall'), after_wall=new.get('wall'),
            before_optimal=old.get('optimal'), after_optimal=new.get('optimal'),
            before_variables=old.get('variables'), after_variables=new.get('variables'),
            before_constraints=old.get('constraints'), after_constraints=new.get('constraints')))
    for model in range(1, 5):
        for solver in ('exact', 'alns'):
            rows = [r for r in paired if r['model'] == model and r['solver'] == solver]
            changes = [r['distance_change_percent'] for r in rows if r['distance_change_percent'] is not None]
            speeds = [r['before_wall']/r['after_wall'] for r in rows
                      if r['before_optimal'] and r['after_optimal'] and
                      abs(r['before_objective']-r['after_objective']) <= 1e-6]
            groups.append(dict(model=model, solver=solver, runs=len(rows),
                before_valid=sum(r['before_valid'] for r in rows), after_valid=sum(r['after_valid'] for r in rows),
                before_optimal=sum(bool(r['before_optimal']) for r in rows),
                after_optimal=sum(bool(r['after_optimal']) for r in rows),
                better=sum(c < -1e-6 for c in changes), equal=sum(abs(c) <= 1e-6 for c in changes),
                worse=sum(c > 1e-6 for c in changes),
                mean_distance_change_percent=statistics.mean(changes) if changes else None,
                common_optimal_count=len(speeds), median_common_optimal_speedup=statistics.median(speeds) if speeds else None))
    payload = dict(input_hashes_match=True, before='before', after=args.after, groups=groups, pairs=paired,
                   limitations='Paired diagnostic cases, three ALNS seeds, single timing run; no significance claim.')
    (args.output/'comparison.json').write_text(json.dumps(payload, ensure_ascii=False, indent=2))
    print(json.dumps(groups, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
