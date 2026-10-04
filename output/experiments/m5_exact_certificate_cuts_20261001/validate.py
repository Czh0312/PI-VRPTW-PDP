"""Reaudit frozen M5 distance-row runs and verify bounds, rows and provenance."""
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


def m5_source(name, path):
    source = path.read_text()
    if name in ('models/routing/solvers/exact/solver.py', 'models/routing/solvers/exact/multibatch_cuts.py'):
        # Final telemetry names describe an observed callback, not the
        # completion time of the initial LP (which precedes that callback).
        source = source.replace('root_relaxation_first_complete_seconds', 'root_optimal_callback_seconds')
        source = source.replace('root_relaxation_completed', 'root_optimal_callback_observed')
    if name == 'models/routing/solvers/exact/solver.py':
        # The other active B1 chat added only these inactive metadata
        # fields between snapshots. Every other byte must still match.
        source = source.replace("                    e2_tight_route_bounds=ctx.get('tight_e2_route_bounds', False),\n", '')
        source = source.replace("                    e2_route_identity_ranges_reduced=sum(v.LB > 1 or v.UB < len(ctx['e2'])\n"
                                "                                                        for v in ctx.get('e2_route_identities', {}).values()),\n", '')
        source = source.replace("                        (['B1_task_route_identity_bounds'] if ctx.get('tight_e2_route_bounds') else [])+\n", '')
    return source


core = ['models/routing/solvers/exact/'+name for name in (
    'formulation.py', 'solver.py', 'multibatch_cuts.py', 'primal.py', 'multibatch_primal.py', 'multibatch_bounds.py')]
entry = 'models/routing/variants/model_5/exact/solver.py'
benchmark = 'tools/audit/benchmark_multibatch_primal.py'
sources = core+[entry, benchmark]
current = {name: sha(ROOT/name) for name in sources}
current_m5 = {name: hashlib.sha256(m5_source(name, ROOT/name).encode()).hexdigest() for name in sources}
for name in sources:
    compile((ROOT/name).read_text(), str(ROOT/name), 'exec')
    destination = HERE/'final_source_snapshot'/name
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(ROOT/name, destination)
    assert sha(destination) == current[name]

groups, problems, certificates, materials, artifacts, formal_sources = {}, {}, {}, {}, {}, {}
total = 0
for group, expected in [('probe_15s', 6), ('final_15s', 18), ('final_5s', 20), ('long_60s', 2)]:
    directory = HERE/group
    manifest = read(directory/'manifest.json')
    for name, digest in manifest['artifact_sha256'].items():
        assert sha(directory/name) == digest, (group, name)
    rows = read(directory/'summary.json')
    assert len(rows) == len(manifest['jobs']) == expected
    assert {r['job']['id'] for r in rows} == {j['id'] for j in manifest['jobs']}
    m5_hashes = {p: hashlib.sha256(m5_source(p, directory/'source_snapshot'/p).encode()).hexdigest() for p in sources}
    assert all(m5_hashes[p] == current_m5[p] for p in core)
    if group != 'probe_15s':
        assert m5_hashes == current_m5
        formal_sources[group] = m5_hashes
    stats, pairs, initials = defaultdict(list), defaultdict(dict), defaultdict(set)
    for row in rows:
        job, info = row['job'], row['info']
        assert not row['failure'] and row['valid'], (group, job['id'])
        filename = directory/(job['id']+'.json')
        payload = read(filename)
        assert payload['job'] == job and payload['result']['info'] == info
        case = Path(job['case'])
        if str(case) not in problems:
            problem = load_instance(*load_routing_case(str(case)))
            problems[str(case)] = problem
            certificates[str(case.relative_to(HERE))] = distance_certificate(problem)
            config = read(case)
            for key in ('data', 'orders', 'distance_matrix', 'duration_matrix', 'dataset_metadata', 'routing_config'):
                if config.get(key):
                    config[key] = Path(config[key]).name
            content = {p.name: sha(p) for p in case.parent.iterdir() if p.is_file()
                       and p.name not in ('case.json', 'initial_result.json', 'initial_audit.json')}
            content['canonical_case'] = hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()
            materials[group, case.parent.name] = content
        problem = problems[str(case)]
        certificate = certificates[str(case.relative_to(HERE))]
        independent = audit_solution(problem, payload['result'])
        assert independent['valid'] and payload['independent_audit']['valid']
        assert math.isclose(independent['objective'], info['objective'], rel_tol=1e-9, abs_tol=1e-7)
        initial_path = Path(job['initial_result'])
        initial = read(initial_path)
        assert audit_solution(problem, initial)['valid']
        assert info['objective'] <= initial['info']['objective']+1e-7
        assert info['mip_start_supplied'] and not info['proven_infeasible']
        initials[job['case'], job['seed']].add(sha(initial_path))
        assert payload['result']['distance_certificate'] == certificate
        assert info['distance_certificate_bound'] == certificate['bound'] <= independent['objective']+1e-6
        mode = 'none' if job['variant'] == 'reference' else job['variant']
        assert info['distance_cut_mode'] == mode
        cuts = (sum(row['bound']-1e-7*max(1, row['bound']) > 0 for row in certificate['components'])
                if mode == 'components' else int(certificate['bound'] > 0) if mode == 'total' else 0)
        assert info['distance_cuts_added'] == cuts
        assert ('M5_distance_certificate_cuts' in info['formulation_features']) == (cuts > 0)
        assert info['e2_departure_load_variables'] == 0
        assert not info.get('e2_tight_route_bounds') and 'B1_task_route_identity_bounds' not in info['formulation_features']
        assert not info.get('e2_route_identity_ranges_reduced')
        lower, raw = info.get('best_bound'), info.get('gurobi_best_bound')
        candidates = [v for v in (raw, certificate['bound']) if v is not None and math.isfinite(v)]
        if candidates:
            assert lower == max(candidates)
        if lower is not None:
            assert math.isfinite(lower) and lower <= info['objective']+1e-6
            gap = max(0, info['objective']-lower)/max(1e-9, abs(info['objective']))
            assert math.isclose(gap, info['mip_gap'], rel_tol=1e-7, abs_tol=1e-7)
        if info['best_bound_source'] == 'distance_certificate':
            assert lower == certificate['bound'] and (raw is None or lower > raw)
        if info['proven_optimal']:
            assert info['status'] == 'OPTIMAL' and raw is not None
            assert math.isclose(raw, independent['objective'], rel_tol=1e-9, abs_tol=1e-7)
        log = (directory/(job['id']+'.log')).read_text()
        fingerprint = re.search(r'Model fingerprint: (\S+)', log)
        assert fingerprint
        root = re.search(r'Root relaxation: objective ([\d.e+\-]+), (\d+) iterations, ([\d.]+) seconds', log)
        measured = dict(info)
        measured['logged_root_lp_objective'] = float(root.group(1)) if root else None
        measured['logged_root_lp_seconds'] = float(root.group(3)) if root else None
        if info['root_relaxation_completed']:
            assert info['root_relaxation_first_complete_seconds'] is not None
            assert 0 <= info['root_relaxation_first_complete_seconds'] <= info['mip_runtime']+1e-6
            if job['customers'] >= 20:
                assert root, (group, job['id'])
        else:
            assert info['root_relaxation_first_complete_seconds'] is None
        stats[job['customers'], job['variant']].append(measured)
        pairs[job['case'], job['seed']][job['variant']] = dict(info=info, fingerprint=fingerprint.group(1))
        artifacts[str(filename.relative_to(HERE))] = sha(filename)
        artifacts[str((directory/(job['id']+'.log')).relative_to(HERE))] = sha(directory/(job['id']+'.log'))
    assert all(len(digests) == 1 for digests in initials.values())
    for variants in pairs.values():
        reference = variants['reference']['info']
        for variant, record in variants.items():
            info = record['info']
            assert info['variables'] == reference['variables']
            assert info['constraints']-reference['constraints'] == info['distance_cuts_added']
            assert info['general_constraints'] == reference['general_constraints']
            assert info['linear_nonzeros'] >= reference['linear_nonzeros']
            if reference['proven_optimal']:
                assert info['proven_optimal'] and math.isclose(info['objective'], reference['objective'], abs_tol=1e-7)
    aggregates = []
    for (customers, variant), infos in sorted(stats.items()):
        means = {}
        for key in ('objective', 'best_bound', 'gurobi_best_bound', 'mip_gap', 'build_time', 'mip_runtime',
                    'mip_work', 'mip_iterations', 'distance_certificate_runtime',
                    'root_relaxation_first_complete_seconds', 'logged_root_lp_objective', 'logged_root_lp_seconds'):
            values = [i.get(key) for i in infos]
            means[key] = statistics.mean(values) if all(v is not None and math.isfinite(v) for v in values) else None
        aggregates.append(dict(customers=customers, variant=variant, runs=len(infos), means=means,
            optimal=sum(i['proven_optimal'] for i in infos),
            root_optimal_observed=sum(bool(i['root_relaxation_completed']) for i in infos),
            variables=sorted({i['variables'] for i in infos}),
            constraints=sorted({i['constraints'] for i in infos}),
            distance_rows=sorted({i['distance_cuts_added'] for i in infos}),
            linear_nonzeros=sorted({i['linear_nonzeros'] for i in infos})))
    groups[group] = dict(runs=len(rows), reaudited=len(rows), failures=0, valid=len(rows),
        manifest_artifacts_verified=len(manifest['artifact_sha256']), same_initial_result=True,
        unchanged_variable_count=True, aggregates=aggregates)
    total += len(rows)
    for suffix in ('summary.json', 'manifest.json'):
        artifacts[str((directory/suffix).relative_to(HERE))] = sha(directory/suffix)

assert formal_sources['final_5s'] == formal_sources['final_15s'] == formal_sources['long_60s']
for small_index, large_index in [('7', '0'), ('8', '1'), ('9', '2')]:
    assert materials['final_5s', small_index] == materials['final_15s', large_index]
for large_index, probe_index in [('1', '0'), ('2', '1')]:
    assert materials['final_15s', large_index] == materials['probe_15s', probe_index]
assert materials['final_15s', '2'] == materials['long_60s', '0']
for path, digest in read(HERE/'before_sha256.json').items():
    assert sha(HERE/'before_source'/path) == digest

test_sources = ['tests/routing/test_multibatch_certificate_cuts.py']
test_hashes = {name: sha(ROOT/name) for name in test_sources}
for name in test_sources:
    destination = HERE/'test_source_snapshot'/name
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(ROOT/name, destination)
changed = ['models/routing/solvers/exact/formulation.py', 'models/routing/solvers/exact/solver.py',
           'models/routing/solvers/exact/multibatch_cuts.py', entry, benchmark]+test_sources
patch = []
for name in changed:
    before = HERE/'before_source'/name
    original = before.read_text().splitlines(True) if before.exists() else []
    after = m5_source(name, ROOT/name)
    if name == 'models/routing/solvers/exact/solver.py':
        # B1 warehouse-flow telemetry appeared after the initial save and
        # before the first probe. It belongs to the other chat, not this diff.
        after = after.replace("                    e2_site_flow=ctx.get('e2_site_flow', False),\n", '')
        after = after.replace("                    e2_site_flow_arcs=len(ctx.get('e2_site_arcs', {})),\n", '')
        after = after.replace("                    e2_site_flow_variables=sum(v.VType == GRB.CONTINUOUS\n"
                              "                                               for v in ctx.get('e2_site_arcs', {}).values()),\n", '')
        after = after.replace("                        (['B1_warehouse_conservation'] if ctx.get('e2_site_flow') else [])+\n", '')
    patch.extend(difflib.unified_diff(original, after.splitlines(True),
                                      fromfile='before/'+name, tofile='after/'+name))
(HERE/'changes.patch').write_text(''.join(patch))
test_log = (HERE/'pytest.log').read_text().splitlines()[-1]
assert test_log.startswith('260 passed'), test_log
artifacts['pytest.log'] = sha(HERE/'pytest.log')
report = ROOT/'docs/m5_exact_certificate_cuts_20261001.md'
for target in re.findall(r'\]\(([^)]+)\)', report.read_text()):
    if '://' not in target and not target.endswith('validation.json'):
        assert (report.parent/target).exists(), target
write(HERE/'validation.json', dict(
    groups=groups, formal_runs=38, extended_runs=2, exploratory_runs=6, all_reaudited=total, all_valid=True,
    certificate_recomputed=True, certificates=certificates, identical_formal_m5_source=True,
    unchanged_variable_count=True, material_inputs_identical=True, current_source_sha256=current,
    m5_source_sha256_with_declared_metadata_normalization=current_m5,
    formal_full_source_sha256={group: {p: read(HERE/group/'manifest.json')['artifact_sha256']['source_snapshot/'+p]
                                     for p in sources} for group in formal_sources},
    test_source_sha256=test_hashes, test_log=test_log, artifact_sha256=artifacts,
    report_sha256=sha(report), changes_patch_sha256=sha(HERE/'changes.patch'),
    validation_script_sha256=sha(Path(__file__)), formal_driver_sha256=sha(HERE/'run_formal.py'),
    note='Rows are optional; default M5 still reports the certificate without adding distance rows. '
         'Source checks normalize only the declared inactive B1 metadata and the final callback telemetry rename. '
         'A callback timestamp is the first observed optimal MIPNODE relaxation, not initial LP solve time. '
         'This wall-clock diagnostic does not establish a general speedup.'))
print(json.dumps(dict(formal_runs=38, extended_runs=2, exploratory_runs=6, all_reaudited=total,
                      all_valid=True, certificate_recomputed=True, test_log=test_log), ensure_ascii=False))
