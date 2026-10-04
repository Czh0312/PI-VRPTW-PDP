"""Check validated source and links; save only this stage's reviewable patch."""
import difflib
import hashlib
import json
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

value = read(HERE/'validation.json')
assert value['formal_runs'] == 34
assert len(value['complete_lp_checks']) == 90 and len(value['integer_cover_checks']) == 90
assert len(value['e1_checks']) == 34 and len(value['joint_checks']) == len(value['recovery_checks']) == 16
assert len(value['unique_independent_universe_checks']) == 25
for name, digest in value['current_source_sha256'].items():
    assert sha(ROOT/name) == digest, name
assert sha(ROOT/'tests/routing/test_multibatch_joint_inventory.py') == value['test_source_sha256']
patch = []

def add(name, before, after):
    patch.extend(difflib.unified_diff(before.splitlines(True), after.splitlines(True),
        fromfile='a/'+name if before else '/dev/null', tofile='b/'+name))

for name in ('models/routing/variants/model_5/exact/bounds.py',
             'models/routing/variants/model_5/exact/__init__.py'):
    add(name, (HERE/'before_source'/name).read_text(), (HERE/'final_source_snapshot'/name).read_text())
for name in ('models/routing/solvers/exact/multibatch_joint_inventory.py',
             'tests/routing/test_multibatch_joint_inventory.py',
             'tools/audit/benchmark_multibatch_joint_inventory.py', 'docs/m5_joint_inventory_20261001.md'):
    add(name, '', (ROOT/name).read_text())
for name, block in read(HERE/'documentation_blocks.json').items():
    after = (ROOT/name).read_text()
    assert after.count(block) == 1, name
    add(name, after.replace(block, ''), after)
(HERE/'changes.patch').write_text(''.join(patch))
report = ROOT/'docs/m5_joint_inventory_20261001.md'
for link in re.findall(r'\]\(([^)]+)\)', report.read_text()):
    if '://' not in link:
        assert (report.parent/link).exists(), link
rows = read(HERE/'final_60s/summary.json')
large = [r for r in rows if r['job']['customers'] == 50 and r['job']['variant'] == 'joint']
assert len(large) == 3
best = min(large, key=lambda r: r['upper_bound'])
payload = read(HERE/'final_60s'/(best['job']['id']+'.json'))
from configuration.loader import load_routing_case
from models.routing.common.cycle import load_instance
from models.routing.common.cycle.audit import audit_solution
problem = load_instance(*load_routing_case(best['job']['case']))
result = read(HERE/'best_full_result.json')
audit = audit_solution(problem, result)
assert audit['valid'] and audit['periodic_replay']['valid']
assert abs(audit['objective']-best['upper_bound']) < 1e-6
assert audit == result['audit']
assert not result['info']['proven_optimal'] and result['info']['best_bound'] is None
value.update(best_run=best['job']['id'], best_full_objective=audit['objective'],
    best_global_bound=best['bound'], best_gap=best['gap'],
    best_full_result_sha256=sha(HERE/'best_full_result.json'), report_sha256=sha(report),
    changes_patch_sha256=sha(HERE/'changes.patch'), finalizer_sha256=sha(Path(__file__)),
    all_referenced_report_files_exist=True)
(HERE/'validation.json').write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)+'\n')
print(json.dumps(dict(formal_runs=value['formal_runs'], regression=value['test_log'],
    complete_LP_models=90, complete_integer_models=90, joint_models=16, reconstructed_schedules=16,
    best_full_objective=audit['objective'], best_gap=best['gap']), ensure_ascii=False))
