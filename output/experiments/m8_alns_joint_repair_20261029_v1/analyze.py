"""Offline scoring only. Never imported by the ALNS experiment workers."""
import hashlib
import json
from pathlib import Path
import statistics

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
PHASES = ('pilot', 'retained_probe', 'beam_probe', 'fixed_probe', 'final')
DRIVERS = dict(pilot='pilot_driver.py', retained_probe='retained_driver.py',
               beam_probe='beam_driver.py', fixed_probe='fixed_driver.py', final='bench.py')


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def save(path, payload):
    Path(path).write_text(json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')


def metrics(rows):
    gaps = [r['gap_percent'] for r in rows]
    return dict(cases=len(rows), mean_gap_percent=statistics.mean(gaps),
                median_gap_percent=statistics.median(gaps), worst_gap_percent=max(gaps),
                known_optimum_hits=sum(abs(r['objective']-r['exact_objective']) <= 1e-6 for r in rows),
                within_one_percent=sum(g <= 1.+1e-9 for g in gaps),
                mean_cpu_seconds=statistics.mean(r['cpu_seconds'] for r in rows),
                mean_wall_seconds=statistics.mean(r['wall_seconds'] for r in rows))


def analyze():
    references, reference_hashes = {}, {}
    for case in [f'S{i:02}' for i in range(1, 10)]:
        path = ROOT/f'output/experiments/m8_all_small_rerun_20261029_v1/runs/{case}/s42/summary.json'
        ref = read(path)
        assert ref['proven_optimal'] and ref['business_audit_valid'] and ref['periodic_replay_valid']
        assert abs(ref['audited_upper_bound']-ref['global_lower_bound']) <= 1e-6
        references[case] = ref['audited_upper_bound']
        reference_hashes[path.relative_to(ROOT).as_posix()] = sha(path)
    phases = {}
    for phase in PHASES:
        plan = read(HERE/f'{phase}_plan.json')
        assert plan['seed'] == 42 and plan['concurrency'] == 1 and not plan['exact_reference_used']
        assert sha(HERE/DRIVERS[phase]) == plan['driver_sha256']
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
            assert row['seed'] == 42 and row['seconds'] == 20.
            assert row['original_K'] == list(range(1, 8))
            assert not row['external_initial_solution_used'] and not row['exact_reference_used']
            assert not result['info']['proven_optimal'] and result['info']['best_bound'] is None
            assert abs(row['objective']-audit['objective']) <= 1e-8
            exact = references[row['case']]
            assert row['objective'] >= exact-1e-6
            row.update(exact_objective=exact, gap_percent=100.*(row['objective']/exact-1.))
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


if __name__ == '__main__':
    analyze()
