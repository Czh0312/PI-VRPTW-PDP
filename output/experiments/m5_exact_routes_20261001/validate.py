"""Recheck frozen M5 route-ablation artifacts without rerunning the solver."""
from collections import defaultdict
import difflib
import hashlib
import json
import math
from pathlib import Path
import re
import statistics
import sys

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT))
from configuration.loader import load_routing_case
from models.routing.common.cycle import load_instance
from models.routing.common.cycle.audit import audit_solution


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read(path):
    return json.loads(path.read_text())


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)+'\n')


sources = ['models/routing/solvers/exact/'+name for name in (
    'formulation.py', 'solver.py', 'primal.py', 'multibatch_primal.py', 'multibatch_cuts.py')]
current_sources = {name: sha(ROOT/name) for name in sources}
for name in sources+['tools/audit/benchmark_multibatch_primal.py']:
    compile((ROOT/name).read_text(), str(ROOT/name), 'exec')

groups = {}
material_inputs = {}
problems = {}
record_hashes = {}
for name, expected_count in [('final_5s', 60), ('final_15s', 18), ('matched_50_15s', 6)]:
    directory = HERE/name
    manifest = read(directory/'manifest.json')
    for relative, expected in manifest['artifact_sha256'].items():
        assert sha(directory/relative) == expected, (name, relative)
    assert all(manifest['artifact_sha256']['source_snapshot/'+path] == digest
               for path, digest in current_sources.items()), name
    rows = read(directory/'summary.json')
    assert len(rows) == len(manifest['jobs']) == expected_count
    assert {r['job']['id'] for r in rows} == {j['id'] for j in manifest['jobs']}
    stats = defaultdict(list)
    for row in rows:
        job = row['job']
        assert not row['failure'] and row['valid'], job['id']
        result_file = directory/(job['id']+'.json')
        payload = read(result_file)
        assert payload['job'] == job
        result, info = payload['result'], row['info']
        assert result['info'] == info
        case = Path(job['case'])
        if str(case) not in problems:
            problems[str(case)] = load_instance(*load_routing_case(str(case)))
            frozen_dir = case.parent
            materials = {p.name: sha(p) for p in sorted(frozen_dir.iterdir())
                         if p.is_file() and p.name not in ('case.json', 'initial_result.json', 'initial_audit.json')}
            config = read(case)
            for key in ('data', 'orders', 'distance_matrix', 'duration_matrix', 'dataset_metadata', 'routing_config'):
                if config.get(key):
                    config[key] = Path(config[key]).name
            materials['canonical_case'] = hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()
            previous = material_inputs.setdefault(job['source_case'], materials)
            assert previous == materials, (name, job['source_case'])
        problem = problems[str(case)]
        independent = audit_solution(problem, result)
        assert independent['valid'] and payload['independent_audit']['valid'], job['id']
        assert math.isclose(independent['objective'], info['objective'], rel_tol=1e-9, abs_tol=1e-7)
        assert not info['proven_infeasible']
        if info['proven_optimal']:
            assert info['status'] == 'OPTIMAL'
        bound = info.get('best_bound')
        if bound is not None:
            assert math.isfinite(bound) and bound <= info['objective']+1e-6
            gap = max(0, info['objective']-bound)/max(1e-9, abs(info['objective']))
            assert math.isclose(gap, info['mip_gap'], rel_tol=1e-7, abs_tol=1e-7)
        if job['variant'] == 'adaptive':
            assert bool(info['e2_global_internal_arcs']) == (info['e2_indexed_internal_arcs'] >= 10000)
        else:
            assert info['e2_global_internal_arcs'] == 0
        if name == 'matched_50_15s':
            initial = read(Path(job['initial_result']))
            assert audit_solution(problem, initial)['valid']
            assert info['mip_start_supplied'] and info['objective'] <= initial['info']['objective']+1e-7
            assert math.isclose(job['initial_objective'], initial['info']['objective'])
        stats[job['customers'], job['variant']].append(info)
        record_hashes[str(result_file.relative_to(HERE))] = sha(result_file)
    aggregates = []
    for (customers, variant), infos in sorted(stats.items()):
        means = {}
        for key in ('objective', 'best_bound', 'mip_gap', 'build_time', 'mip_runtime', 'mip_work',
                    'mip_node_count', 'primal_runtime'):
            values = [info.get(key) for info in infos]
            means[key] = statistics.mean(values) if all(v is not None and math.isfinite(v) for v in values) else None
        aggregates.append(dict(customers=customers, variant=variant, runs=len(infos), means=means,
                               optimal=sum(i['proven_optimal'] for i in infos),
                               variables=sorted({i['variables'] for i in infos}),
                               finite_bound_runs=sum(i.get('best_bound') is not None for i in infos)))
    groups[name] = dict(runs=len(rows), valid=len(rows), failures=0, reaudited=len(rows),
                       manifest_artifacts_verified=len(manifest['artifact_sha256']),
                       matched_current_core_source=True, aggregates=aggregates)
    for suffix in ('summary.json', 'manifest.json'):
        record_hashes[str((directory/suffix).relative_to(HERE))] = sha(directory/suffix)

matched = read(HERE/'matched_50_15s/summary.json')
assert len({sha(Path(row['job']['initial_result'])) for row in matched}) == 1
before_hashes = read(HERE/'before_sha256.json')
for path, digest in before_hashes.items():
    assert sha(HERE/'before_source'/path) == digest
changed_sources = ['models/routing/solvers/exact/formulation.py', 'models/routing/solvers/exact/solver.py',
                   'tools/audit/benchmark_multibatch_primal.py']
changes = []
for path in changed_sources:
    changes.extend(difflib.unified_diff((HERE/'before_source'/path).read_text().splitlines(True),
                                       (ROOT/path).read_text().splitlines(True),
                                       fromfile='before/'+path, tofile='after/'+path))
(HERE/'changes.patch').write_text(''.join(changes))

logs = {name: (HERE/name).read_text().splitlines()[-1] for name in ('pytest.log', 'pytest_targeted.log')}
assert logs['pytest.log'].startswith('167 passed')
assert logs['pytest_targeted.log'].startswith('14 passed')
for name in logs:
    record_hashes[name] = sha(HERE/name)
report = ROOT/'docs/m5_exact_routes_20261001.md'
for target in re.findall(r'\]\(([^)]+)\)', report.read_text()):
    if '://' not in target and not target.endswith('validation.json'):
        assert (report.parent/target).exists(), target
write(HERE/'validation.json', dict(
    groups=groups, formal_runs=84, independently_reaudited=84, all_valid=True,
    material_inputs_identical_across_groups=material_inputs,
    same_matched_initial_result=True, current_core_source_sha256=current_sources,
    benchmark_sha256=sha(ROOT/'tools/audit/benchmark_multibatch_primal.py'),
    test_source_sha256=sha(ROOT/'tests/routing/test_multibatch_route_compression.py'),
    test_logs=logs, artifact_sha256=record_hashes,
    report_sha256=sha(report), changes_patch_sha256=sha(HERE/'changes.patch'),
    limitations='Wall-clock runs. 50-customer proof progress remains unstable; matched-start gaps are 100%. '
                'No optimality proof for 20/30/50 customers at the tested budgets. '
                'Original 50-customer seed 42 has unequal timed primal objectives; retain both results.'))
print(json.dumps(dict(formal_runs=84, independently_reaudited=84, all_valid=True,
                     matched_current_core_source=True, test_logs=logs), ensure_ascii=False))
