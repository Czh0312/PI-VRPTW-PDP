"""Offline scoring of frozen experiments, never imported by search workers."""
import difflib
import hashlib
import json
from pathlib import Path
import statistics

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
DRIVERS = {'pilot': 'pilot_driver.py', 'final': 'bench.py'}


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def save(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')


def metrics(rows):
    gaps = [r['gap_percent'] for r in rows]
    return dict(cases=len(rows), mean_gap_percent=statistics.mean(gaps),
                median_gap_percent=statistics.median(gaps), worst_gap_percent=max(gaps),
                known_optimum_hits=sum(abs(r['objective']-r['exact_objective']) <= 1e-6 for r in rows),
                within_one_percent=sum(g <= 1.+1e-9 for g in gaps),
                mean_cpu_seconds=statistics.mean(r['cpu_seconds'] for r in rows),
                mean_wall_seconds=statistics.mean(r['wall_seconds'] for r in rows),
                wall_seconds_range=[min(r['wall_seconds'] for r in rows), max(r['wall_seconds'] for r in rows)])


def main():
    references, reference_hashes = {}, {}
    for case in [f'S{i:02}' for i in range(1, 10)]:
        path = ROOT/f'output/experiments/m8_all_small_rerun_20261029_v1/runs/{case}/s42/summary.json'
        ref = read(path)
        assert ref['proven_optimal'] and ref['business_audit_valid'] and ref['periodic_replay_valid']
        assert abs(ref['audited_upper_bound']-ref['global_lower_bound']) <= 1e-6
        references[case] = ref['audited_upper_bound']
        reference_hashes[path.relative_to(ROOT).as_posix()] = sha(path)
    phases = {}
    for phase, driver in DRIVERS.items():
        plan = read(HERE/f'{phase}_plan.json')
        assert plan['seed'] == 42 and plan['concurrency'] == 1 and not plan['exact_reference_used']
        assert sha(HERE/driver) == plan['driver_sha256']
        assert all(sha(path) == digest for path, digest in plan['input_hashes'].items())
        for variant, expected in plan['variants'].items():
            source = HERE/'sources'/variant
            actual = {p.relative_to(source).as_posix(): sha(p) for p in source.rglob('*.py')}
            assert actual == expected, (phase, variant)
        rows = []
        for job in plan['jobs']:
            folder = HERE/'runs'/phase/job['case']/job['variant']
            row = read(folder/'summary.json')
            audit, result = read(folder/'audit.json'), read(folder/'result.json')
            assert row['valid'] and row['periodic_valid'] and audit['valid'] and audit['periodic_replay']['valid']
            assert row['seed'] == 42 and row['seconds'] == 5.
            assert row['original_K'] == list(range(1, 8))
            assert not row['external_initial_solution_used'] and not row['exact_reference_used']
            assert not result['info']['proven_optimal'] and result['info']['best_bound'] is None
            assert abs(row['objective']-audit['objective']) <= 1e-8
            exact = references[row['case']]
            assert row['objective'] >= exact-1e-6
            visits = result.get('count_search_visits', [])
            row.update(exact_objective=exact, gap_percent=100.*(row['objective']/exact-1.),
                       chain_candidates=result['info'].get('chain_candidates', 0),
                       chain_improvements=result['info'].get('chain_improvements', 0),
                       certified_infeasible_K=sorted({v['batch_count'] for v in visits if v.get('domain_certificate')}))
            history = [(v['started']+h['time'], h['objective']) for v in visits
                       for h in v.get('local_history', []) if h.get('objective') is not None]
            by_five = [cost for tick, cost in history if tick <= 5.]
            row['native_best_cost_by_5s'] = min(by_five) if by_five else None
            row['native_gap_by_5s_percent'] = 100.*(min(by_five)/exact-1.) if by_five else None
            rows.append(row)
        variants = {v: metrics([r for r in rows if r['variant'] == v]) for v in plan['variants']}
        contender = next(v for v in variants if v != 'baseline')
        paired = []
        for case in sorted({r['case'] for r in rows}):
            before = next(r for r in rows if r['case'] == case and r['variant'] == 'baseline')
            after = next(r for r in rows if r['case'] == case and r['variant'] == contender)
            delta = after['objective']-before['objective']
            paired.append(dict(case=case, baseline=before['objective'], candidate=after['objective'],
                baseline_gap_percent=before['gap_percent'], candidate_gap_percent=after['gap_percent'],
                exact=references[case], baseline_K=before['K'], candidate_K=after['K'],
                baseline_N=before['N'], candidate_N=after['N'],
                change='better' if delta < -1e-6 else 'worse' if delta > 1e-6 else 'tie', delta=delta))
        phases[phase] = dict(contender=contender, runs=len(rows), variants=variants, pairs=paired,
            better=sum(p['change'] == 'better' for p in paired),
            tie=sum(p['change'] == 'tie' for p in paired),
            worse=sum(p['change'] == 'worse' for p in paired), rows=rows)
    output = dict(root_seeds=[42], independently_randomized_replicates=1,
        references_used_only_for_offline_scoring=True, reference_hashes=reference_hashes, phases=phases)
    save(HERE/'analysis.json', output)
    for phase, data in phases.items():
        print(json.dumps(dict(phase=phase, variants=data['variants'],
                              better=data['better'], tie=data['tie'], worse=data['worse']), ensure_ascii=False))
    baseline, candidate = HERE/'sources/baseline', HERE/'sources/bounded'
    changed, diff = [], []
    for path in candidate.rglob('*.py'):
        relative = path.relative_to(candidate)
        before = baseline/relative
        if before.exists() and sha(before) == sha(path):
            continue
        changed.append(relative.as_posix())
        # Refuse to overwrite any concurrent changes to the production source.
        current = ROOT/relative
        if before.exists():
            assert current.exists() and sha(current) in (sha(before), sha(path)), str(current)
        else:
            assert not current.exists() or sha(current) == sha(path), str(current)
        diff.extend(difflib.unified_diff(before.read_text(encoding='utf-8').splitlines(True) if before.exists() else [],
                    path.read_text(encoding='utf-8').splitlines(True), fromfile='baseline/'+relative.as_posix(),
                    tofile='bounded/'+relative.as_posix()))
    save(HERE/'changed_sources.json', changed)
    (HERE/'review.diff').write_text(''.join(diff), encoding='utf-8')
    print(json.dumps(dict(changed_sources=changed), ensure_ascii=False))


if __name__ == '__main__':
    main()
