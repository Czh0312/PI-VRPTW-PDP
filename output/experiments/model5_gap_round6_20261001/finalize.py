"""Bind completed proof replay to frozen inputs, production and all artifacts."""
import hashlib
import json
from pathlib import Path
import re

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
OLD = HERE.parent / 'model5_gap_round5_20261001'


def read(path):
    return json.loads(path.read_text())


def sha(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024*1024), b''):
            h.update(block)
    return h.hexdigest()


proof = read(HERE/'validation_final.json')
assert proof['status'] == 'VERIFIED'
assert len(proof['cases']) == 2 and all(row['target_reached'] for row in proof['cases'])
for row in proof['cases']:
    assert 0 <= row['lower'] <= row['upper']
    assert abs(row['gap_percent']/100-(row['upper']-row['lower'])/abs(row['upper'])) < 1e-12
    assert row['gap_percent'] <= 5

test_match = re.search(r'(?m)^181 passed in ([0-9.]+)s$', (HERE/'pytest_final.log').read_text())
assert test_match
production = read(HERE/'production_manifest.json')['production_files_sha256']
for name, digest in production.items():
    assert sha(ROOT/name) == digest
    if name.startswith('models/'):
        assert sha(HERE/'conjunction/source_snapshot'/name) == digest

for prefix in ['', 'conjunction/']:
    plan = read(HERE/(prefix+'plan.json'))
    assert plan['registered_before_run']
    for name, digest in plan['source_sha256'].items():
        assert sha(HERE/(prefix+'source_snapshot')/name) == digest
    for name, digest in plan['input_sha256'].items():
        path = Path(name) if Path(name).is_absolute() else HERE/name
        assert sha(path) == digest
    assert sha(HERE/('conjunction.py' if prefix else 'run.py')) == plan['runner_sha256']

plan = read(HERE/'plan.json')
summary = read(HERE/'summary.json')
assert [row['job'] for row in summary] == plan['jobs']
assert all(row['failure'] is None for row in summary)
assert all(row['wall_seconds'] <= plan['time_limit']+.5 for row in summary)
refinement = read(HERE/'refine_plan.json')
assert refinement['registered_before_run']
assert sha(HERE/'refine.py') == refinement['runner_sha256']
assert sha(HERE/refinement['input_path']) == refinement['input_sha256']
assert sha(HERE/'plan.json') == refinement['source_plan_sha256']
assert read(HERE/'full_refined.json')['wall_seconds'] <= refinement['time_limit']+.5
conjunction = read(HERE/'conjunction/plan.json')
assert read(HERE/'conjunction/result.json')['wall_seconds'] <= conjunction['pricing_seconds']+2*conjunction['joint_seconds_per_pool']+1

dependency = read(HERE/'dependency_binding.json')
manifest = Path(dependency['original_manifest_path'])
assert sha(manifest) == dependency['original_manifest_sha256']
original = read(manifest)['artifact_sha256']
external = {}
for row in dependency['records']:
    path = Path(row['path'])
    assert sha(path) == row['sha256'] == original[row['original_round4_artifact']]
    external[str(path)] = row['sha256']

prior_manifest = read(OLD/'final_verification.json')['artifacts_sha256']
assert sha(OLD/'validation_final.json') == proof['prior_proof_sha256'] == prior_manifest['validation_final.json']
assert sha(HERE/'inputs/previous.json') == prior_manifest['retry/result.json']
assert sha(Path(conjunction['prior_conditional_path'])) == conjunction['prior_conditional_sha256'] == prior_manifest['full.json']
for row in proof['model_checks']:
    path = Path(row['path'])
    if path.is_relative_to(OLD):
        assert sha(path) == prior_manifest[str(path.relative_to(OLD))]
        external[str(path)] = sha(path)
for label, number in [('full', '2'), ('tight', '3')]:
    name = f'fresh/0{number}_zhou_Set2_100-5-1b_{label}_43_models/reference/e1_inventory.mps'
    path = manifest.parent/name
    assert sha(path) == original[name]
    external[str(path)] = sha(path)
for name in ['validation_final.json', 'retry/result.json', 'full.json']:
    external[str(OLD/name)] = prior_manifest[name]

artifacts = {str(path.relative_to(HERE)): sha(path) for path in sorted(HERE.rglob('*'))
             if path.is_file() and '__pycache__' not in path.parts
             and path.name != 'final_verification.json'}
out = dict(status='VERIFIED', target='REACHED_FOR_BOTH_OBSERVED_DEVELOPMENT_CONFIGURATIONS',
           regression_tests=181, regression_seconds=float(test_match[1]),
           whole_cycle_audits=proof['whole_cycle_audits'],
           independently_replayed_columns=proof['independently_replayed_columns'],
           repaired_dual_checks=proof['repaired_dual_checks'],
           model_checks=len(proof['model_checks']), case_results=proof['cases'],
           production_files_sha256=production,
           document_sha256=sha(ROOT/'docs/model5_gap_round6_20261001.md'),
           external_proof_files_sha256=external,
           artifact_count=len(artifacts), artifacts_sha256=artifacts,
           interpretation=proof['interpretation'])
(HERE/'final_verification.json').write_text(json.dumps(out, ensure_ascii=False, indent=2, allow_nan=False)+'\n')
print({k:out[k] for k in ['status', 'target', 'regression_tests', 'model_checks', 'artifact_count', 'case_results']})
