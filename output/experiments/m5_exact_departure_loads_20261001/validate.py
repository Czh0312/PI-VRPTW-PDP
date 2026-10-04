"""Reaudit frozen M5 experiments, recompute certificates and check provenance."""
from collections import defaultdict
import difflib
import hashlib
import json
import math
from pathlib import Path
import re
import shutil
import statistics
import sys

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT))
from configuration.loader import load_routing_case
from models.routing.common.cycle import load_instance
from models.routing.common.cycle.audit import audit_solution
from models.routing.solvers.exact.multibatch_bounds import distance_certificate


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read(path):
    return json.loads(path.read_text())


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)+'\n')


sources = ['models/routing/solvers/exact/'+name for name in (
    'formulation.py', 'solver.py', 'primal.py', 'multibatch_primal.py', 'multibatch_cuts.py', 'multibatch_bounds.py')]
current = {name: sha(ROOT/name) for name in sources}
for name in sources+['tools/audit/benchmark_multibatch_primal.py']:
    compile((ROOT/name).read_text(), str(ROOT/name), 'exec')

groups, problems, certificates, artifacts, materials = {}, {}, {}, {}, {}
formal_sources = {}
for group, expected in [('probe_15s', 6), ('final_5s', 20), ('final_15s', 18)]:
    directory = HERE/group
    manifest = read(directory/'manifest.json')
    for name, digest in manifest['artifact_sha256'].items():
        assert sha(directory/name) == digest, (group, name)
    rows = read(directory/'summary.json')
    assert len(rows) == len(manifest['jobs']) == expected
    assert {r['job']['id'] for r in rows} == {j['id'] for j in manifest['jobs']}
    if group != 'probe_15s':
        assert all(manifest['artifact_sha256']['source_snapshot/'+p] == digest for p, digest in current.items())
        formal_sources[group] = {p: manifest['artifact_sha256']['source_snapshot/'+p] for p in current}
    stats, fingerprints, initial_hashes = defaultdict(list), defaultdict(dict), defaultdict(set)
    for row in rows:
        job, info = row['job'], row['info']
        assert not row['failure'] and row['valid'], (group, job['id'])
        filename = directory/(job['id']+'.json')
        payload = read(filename)
        assert payload['job'] == job and payload['result']['info'] == info
        case = Path(job['case'])
        if str(case) not in problems:
            problems[str(case)] = load_instance(*load_routing_case(str(case)))
            certificate = distance_certificate(problems[str(case)])
            certificates[str(case.relative_to(HERE))] = certificate
            config = read(case)
            for key in ('data', 'orders', 'distance_matrix', 'duration_matrix', 'dataset_metadata', 'routing_config'):
                if config.get(key):
                    config[key] = Path(config[key]).name
            content = {p.name: sha(p) for p in case.parent.iterdir() if p.is_file()
                       and p.name not in ('case.json', 'initial_result.json', 'initial_audit.json')}
            content['canonical_case'] = hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()
            materials[group, case.parent.name] = content
        problem = problems[str(case)]
        independent = audit_solution(problem, payload['result'])
        assert independent['valid'] and payload['independent_audit']['valid']
        assert math.isclose(independent['objective'], info['objective'], rel_tol=1e-9, abs_tol=1e-7)
        initial = read(Path(job['initial_result']))
        assert audit_solution(problem, initial)['valid']
        assert info['objective'] <= initial['info']['objective']+1e-7
        assert info['mip_start_supplied'] and not info['proven_infeasible']
        initial_hashes[job['case'], job['seed']].add(sha(Path(job['initial_result'])))
        lower = info.get('best_bound')
        if lower is not None:
            assert math.isfinite(lower) and lower <= info['objective']+1e-6
            gap = max(0, info['objective']-lower)/max(1e-9, abs(info['objective']))
            assert math.isclose(gap, info['mip_gap'], rel_tol=1e-7, abs_tol=1e-7)
        if group != 'probe_15s':
            certificate = certificates[str(case.relative_to(HERE))]
            assert certificate['bound'] <= independent['objective']+1e-6
            if job['variant'] == 'bounded':
                assert payload['result']['distance_certificate'] == certificate
                assert info['distance_certificate_bound'] == certificate['bound']
            else:
                assert info['distance_certificate_bound'] == 0
                assert payload['result']['distance_certificate']['scope'] == 'disabled'
            raw = info.get('gurobi_best_bound')
            alternatives = [v for v in (raw, info['distance_certificate_bound']) if v is not None and v > 0]
            if alternatives:
                assert lower == max(alternatives)
            if info['best_bound_source'] == 'distance_certificate':
                assert lower == certificate['bound'] and (raw is None or lower > raw)
            assert info['e2_departure_load_variables'] == 0  # rejected factor option stays off
            log = (directory/(job['id']+'.log')).read_text()
            fingerprint = re.search(r'Model fingerprint: (\S+)', log)
            assert fingerprint
            fingerprints[job['case'], job['seed']][job['variant']] = fingerprint.group(1)
            if info['proven_optimal']:
                assert info['status'] == 'OPTIMAL' and raw is not None
                assert math.isclose(raw, independent['objective'], rel_tol=1e-9, abs_tol=1e-7)
        stats[job['customers'], job['variant']].append(info)
        artifacts[str(filename.relative_to(HERE))] = sha(filename)
    assert all(len(digests) == 1 for digests in initial_hashes.values())
    if group != 'probe_15s':
        assert all(len(set(variants.values())) == 1 and len(variants) == 2 for variants in fingerprints.values())
    aggregates = []
    for (customers, variant), infos in sorted(stats.items()):
        means = {}
        for key in ('objective', 'best_bound', 'gurobi_best_bound', 'mip_gap', 'build_time', 'mip_runtime',
                    'mip_work', 'mip_iterations', 'distance_certificate_runtime'):
            values = [i.get(key) for i in infos]
            means[key] = statistics.mean(values) if all(v is not None and math.isfinite(v) for v in values) else None
        aggregates.append(dict(customers=customers, variant=variant, runs=len(infos), means=means,
                               optimal=sum(i['proven_optimal'] for i in infos),
                               variables=sorted({i['variables'] for i in infos}),
                               linear_nonzeros=sorted({i['linear_nonzeros'] for i in infos})))
    groups[group] = dict(runs=len(rows), reaudited=len(rows), failures=0, valid=len(rows),
                         manifest_artifacts_verified=len(manifest['artifact_sha256']),
                         same_initial_result=True,
                         identical_model_fingerprints=group != 'probe_15s', aggregates=aggregates)
    for suffix in ('summary.json', 'manifest.json'):
        artifacts[str((directory/suffix).relative_to(HERE))] = sha(directory/suffix)

assert formal_sources['final_5s'] == formal_sources['final_15s']
for small_index, large_index in [('7', '0'), ('8', '1'), ('9', '2')]:
    assert materials['final_5s', small_index] == materials['final_15s', large_index]
    assert materials['final_15s', large_index] == materials['probe_15s', large_index]
for path, digest in read(HERE/'before_sha256.json').items():
    assert sha(HERE/'before_source'/path) == digest

test_sources = ['tests/routing/test_multibatch_departure_loads.py',
                'tests/routing/test_multibatch_distance_certificate.py',
                'tests/routing/test_multibatch_strengthening.py']
test_hashes = {name: sha(ROOT/name) for name in test_sources}
for name in test_sources:
    destination = HERE/'test_source_snapshot'/name
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(ROOT/name, destination)

changed = ['models/routing/solvers/exact/formulation.py', 'models/routing/solvers/exact/solver.py',
           'models/routing/solvers/exact/multibatch_bounds.py', 'tools/audit/benchmark_multibatch_primal.py']
patch = []
for name in changed:
    before = HERE/'before_source'/name
    original = before.read_text().splitlines(True) if before.exists() else []
    patch.extend(difflib.unified_diff(original, (ROOT/name).read_text().splitlines(True),
                                      fromfile='before/'+name, tofile='after/'+name))
(HERE/'changes.patch').write_text(''.join(patch))
test_log = (HERE/'pytest.log').read_text().splitlines()[-1]
assert test_log.startswith('232 passed'), test_log
artifacts['pytest.log'] = sha(HERE/'pytest.log')
report = ROOT/'docs/m5_exact_bound_certificate_20261001.md'
for target in re.findall(r'\]\(([^)]+)\)', report.read_text()):
    if '://' not in target and not target.endswith('validation.json'):
        assert (report.parent/target).exists(), target
write(HERE/'validation.json', dict(
    groups=groups, formal_runs=38, formal_valid=38, exploratory_runs=6, all_reaudited=44,
    all_valid=True, certificate_recomputed=True, certificates=certificates,
    identical_formal_model_fingerprints=True, identical_formal_core_source=True,
    material_inputs_identical=True, current_core_source_sha256=current,
    test_source_sha256=test_hashes, test_log=test_log, artifact_sha256=artifacts,
    report_sha256=sha(report), changes_patch_sha256=sha(HERE/'changes.patch'),
    note='Bound improvement from a separate full-cycle certificate is not Gurobi root progress or a speedup. '
         'All 20/30/50-customer formal runs remain unproven at the tested budgets. '
         'Departure-load factoring remains optional after its exploratory regressions.'))
print(json.dumps(dict(formal_runs=38, formal_valid=38, all_reaudited=44, all_valid=True,
                      certificate_recomputed=True, identical_formal_model_fingerprints=True,
                      test_log=test_log), ensure_ascii=False))
