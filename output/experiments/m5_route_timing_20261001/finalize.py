"""Verify the frozen evidence and save only stage sixteen's reviewable edits."""
import difflib
import hashlib
import json
import math
from pathlib import Path
import re
import sys

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT))


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read(path):
    return json.loads(path.read_text())


def close(a, b):
    assert math.isclose(a, b, rel_tol=1e-9, abs_tol=1e-6), (a, b)


value = read(HERE/'validation.json')
assert value['formal_runs'] == 34
assert len(value['complete_lp_checks']) == len(value['integer_cover_checks']) == 90
assert len(value['e1_checks']) == 34
assert len(value['joint_checks']) == len(value['recovery_checks']) == 32
assert len(value['timing_checks']) == 45
assert len(value['unique_independent_universe_checks']) == 25
assert len(value['unique_independent_timing_checks']) == 25
external_sources = set(value['external_source_paths'])
assert all(name.partition('/')[2] in external_sources for name in value['source_drift'])
assert all(r['reported_bound_supported'] for r in value['joint_checks'])
for field in ('current_source_sha256', 'test_source_sha256'):
    for name, digest in value[field].items():
        if name not in external_sources:
            assert sha(ROOT/name) == digest, name
for name, digest in value['validator_sha256'].items():
    assert sha(HERE/name) == digest, name

patch = []


def add(name, before, after):
    patch.extend(difflib.unified_diff(before.splitlines(True), after.splitlines(True),
        fromfile='a/'+name if before else '/dev/null', tofile='b/'+name))


for name in ('models/routing/solvers/exact/multibatch_joint_inventory.py',
             'models/routing/solvers/exact/multibatch_route_enumeration.py',
             'tests/routing/test_multibatch_joint_inventory.py'):
    add(name, (HERE/'before_source'/name).read_text(), (HERE/'final_source_snapshot'/name).read_text())
for name in ('models/routing/solvers/exact/multibatch_route_timing.py',
             'tests/routing/test_multibatch_route_timing.py',
             'tools/audit/benchmark_multibatch_route_timing.py', 'docs/m5_route_timing_20261001.md'):
    add(name, '', (ROOT/name).read_text())
for name, block in read(HERE/'documentation_blocks.json').items():
    after = (ROOT/name).read_text()
    assert after.count(block) == 1, name
    # Exclude simultaneous work by other chats from this stage's patch.
    add(name, after.replace(block, ''), after)
(HERE/'changes.patch').write_text(''.join(patch))
report = ROOT/'docs/m5_route_timing_20261001.md'
for link in re.findall(r'\]\(([^)]+)\)', report.read_text()):
    if '://' not in link:
        assert (report.parent/link).exists(), link

rows = read(HERE/'verified_120s/summary.json')
large = [r for r in rows if r['job']['customers'] == 50]
assert len(large) == 6
best_lower = max(large, key=lambda r: r['bound'])
best_upper = min(large, key=lambda r: r['upper_bound'])
combined = read(HERE/'combined_certificate.json')
close(combined['bound'], best_lower['bound'])
close(combined['upper_bound'], best_upper['upper_bound'])
close(combined['gap'], max(0, combined['upper_bound']-combined['bound'])/abs(combined['upper_bound']))
assert combined['lower_run'] == best_lower['job']['id']
assert combined['upper_run'] == best_upper['job']['id']
assert combined['case'] == best_lower['job']['case'] == best_upper['job']['case']
assert not combined['proven_optimal'] and not combined['proven_infeasible']
assert combined['scope'] == 'M5_combined_valid_lower_bound_and_audited_upper_across_formal_runs'
for r in large:
    assert r['job']['budget'] == 120

from configuration.loader import load_routing_case
from models.routing.common.cycle import load_instance
from models.routing.common.cycle.audit import audit_solution
problem = load_instance(*load_routing_case(combined['case']))
result = read(HERE/'best_full_result.json')
audit = audit_solution(problem, result)
assert audit['valid'] and audit['periodic_replay']['valid']
close(audit['objective'], best_upper['upper_bound'])
assert audit == result['audit']
assert not result['info']['proven_optimal'] and result['info']['best_bound'] is None
value.update(best_lower_run=best_lower['job']['id'], best_upper_run=best_upper['job']['id'],
    test_log_sha256=sha(HERE/'pytest.log'), test_invocation='.venv/bin/python -m pytest tests/routing -q',
    external_source_sha256_at_finalization={name: sha(ROOT/name) for name in external_sources},
    best_full_objective=audit['objective'], best_global_bound=combined['bound'],
    best_combined_gap=combined['gap'], combined_runs_not_single_budget=True,
    best_full_result_sha256=sha(HERE/'best_full_result.json'),
    combined_certificate_sha256=sha(HERE/'combined_certificate.json'), report_sha256=sha(report),
    changes_patch_sha256=sha(HERE/'changes.patch'), finalizer_sha256=sha(Path(__file__)),
    all_referenced_report_files_exist=True)
(HERE/'validation.json').write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)+'\n')
print(json.dumps(dict(formal_runs=value['formal_runs'], regression=value['test_log'],
    complete_LP_models=90, complete_integer_models=90, joint_models=32,
    reconstructed_schedules=32, timing_frontiers=45, independently_reenumerated_timing_domains=25,
    best_full_objective=audit['objective'], combined_gap=combined['gap']), ensure_ascii=False))
