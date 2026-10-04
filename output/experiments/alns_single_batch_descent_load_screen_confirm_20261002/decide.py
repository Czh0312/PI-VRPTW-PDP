"""Apply the immutable quality and mechanism gates to the complete matrix."""
from pathlib import Path
import hashlib, json, shutil
OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[2]
OWN = ['models/routing/solvers/alns/single_batch.py',
       'models/routing/solvers/alns/single_batch_descent.py']
TEST = 'tests/routing/test_single_batch_descent_load_screen.py'
def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()
def hashes(base):
    return {str(p.relative_to(base)): sha(p) for p in sorted(base.rglob('*'))
            if p.is_file() and '__pycache__' not in p.parts}
quality = json.loads((OUT/'quality_comparison.json').read_text())
rows = json.loads((OUT/'quality/summary.json').read_text())
assert len(rows) == 16
candidate = [r for r in rows if r['source'] == 'after_source']
mechanism = {str(b): dict(runs=sum(r['budget'] == b for r in candidate),
    triggered_runs=sum(r['budget'] == b and r['descent_load_screen_rejections'] > 0 for r in candidate),
    checks=sum(r['descent_load_screen_checks'] for r in candidate if r['budget'] == b),
    rejections=sum(r['descent_load_screen_rejections'] for r in candidate if r['budget'] == b)) for b in (60, 120)}
mechanism_passed = all(r['triggered_runs'] >= 1 for r in mechanism.values())
replay = json.loads((OUT/'fresh_decode.json').read_text())
fresh_passed = len(replay) == 16 and all(r['valid'] and r['equal'] for r in replay)
accepted = quality['eligible'] and quality['all16_valid'] and mechanism_passed and fresh_passed
decision = dict(decision='RETAINED_AFTER_REGISTERED_60_120_CONFIRMATION' if accepted else 'WITHDRAWN_AFTER_REGISTERED_60_120_CONFIRMATION',
    quality_gate_passed=quality['eligible'] and quality['all16_valid'], mechanism_gate_passed=mechanism_passed,
    mechanism=mechanism, fresh_decode_gate_passed=fresh_passed, reversal_required=not accepted, verification='PENDING',
    original_30_60_raw_and_historical_decision_preserved=True, evidence_limit='Finite registered paired sample, not stable/causal/speed/optimality/trueGAP improvement')
(OUT/'decision.json').write_text(json.dumps(decision, indent=2))
assert all(sha(ROOT/f) == h for f, h in json.loads((OUT/'current_owned_hashes.json').read_text()).items()), 'Never overwrite other edits'
if accepted:
    shutil.copytree(OUT/'after_source', OUT/'adopted_source')
    (OUT/'adopted_source_manifest.json').write_text(json.dumps(hashes(OUT/'adopted_source'), indent=2))
else:
    archive = OUT/'rejected_tests'; archive.mkdir()
    shutil.copy2(ROOT/TEST, archive/Path(TEST).name)
    for f in OWN: shutil.copy2(OUT/'before_source'/f, ROOT/f)
    (ROOT/TEST).unlink()
    shutil.copytree(OUT/'before_source', OUT/'restored_source')
    (OUT/'restored_source_manifest.json').write_text(json.dumps(hashes(OUT/'restored_source'), indent=2))
    (OUT/'restored_owned_hashes.json').write_text(json.dumps({f: sha(ROOT/f) for f in OWN}, indent=2))
assert sha(ROOT/'tests/routing/test_single_batch_late_pickup.py') == json.loads((OUT/'accepted_owned_hashes.json').read_text())['tests/routing/test_single_batch_late_pickup.py']
print(json.dumps(decision, indent=2))
