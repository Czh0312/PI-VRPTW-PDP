"""Apply immutable registered quality/mechanism gates and scoped reversal."""
from pathlib import Path
import hashlib,json,shutil
OUT=Path(__file__).resolve().parent;ROOT=OUT.parents[2]
OWN='models/routing/solvers/alns/single_batch.py'
TEST='tests/routing/test_single_batch_descent_queue.py'
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def hashes(base):return {str(p.relative_to(base)):sha(p) for p in sorted(base.rglob('*')) if p.is_file() and '__pycache__' not in p.parts}
quality=json.loads((OUT/'quality_comparison.json').read_text())
rows=json.loads((OUT/'quality/summary.json').read_text())
candidate=[r for r in rows if r['source']=='after_source']
mechanism={str(budget):dict(runs=sum(r['budget']==budget for r in candidate),
    expanded_runs=sum(r['budget']==budget and (r.get('descent_e2_queue_peak') or 0)>256 for r in candidate),
    maximum_peak=max((r.get('descent_e2_queue_peak') or 0 for r in candidate if r['budget']==budget),default=0)) for budget in (30,60)}
mechanism_passed=all(r['expanded_runs']>=1 for r in mechanism.values())
accepted=quality['eligible'] and quality['all16_valid'] and mechanism_passed
decision=dict(decision='ACCEPTED_REGISTERED_30_60_MATRIX' if accepted else 'REJECTED_REGISTERED_30_60_MATRIX',
    quality_gate_passed=quality['eligible'] and quality['all16_valid'],mechanism_gate_passed=mechanism_passed,
    mechanism=mechanism,verification='PENDING',reversal_required=not accepted,
    evidence_limit='Finite8pairs16runs30/60s; no stable/universal/hourscale/cause/speed/optimality/trueGAP claim')
(OUT/'decision.json').write_text(json.dumps(decision,indent=2))
expected=json.loads((OUT/'owned_hashes.json').read_text())
assert all(sha(ROOT/f)==h for f,h in expected.items()), 'Do not overwrite another edit'
if accepted:
    shutil.copytree(OUT/'after_source',OUT/'adopted_source')
    (OUT/'adopted_source_manifest.json').write_text(json.dumps(hashes(OUT/'adopted_source'),indent=2))
else:
    archive=OUT/'rejected_tests';archive.mkdir()
    shutil.copy2(ROOT/TEST,archive/Path(TEST).name);assert sha(archive/Path(TEST).name)==expected[TEST]
    shutil.copy2(OUT/'before_source'/OWN,ROOT/OWN)
    (ROOT/TEST).unlink()
    shutil.copytree(OUT/'before_source',OUT/'restored_source')
    (OUT/'restored_source_manifest.json').write_text(json.dumps(hashes(OUT/'restored_source'),indent=2))
    (OUT/'restored_owned_hashes.json').write_text(json.dumps({OWN:sha(ROOT/OWN)},indent=2))
assert sha(ROOT/'tests/routing/test_single_batch_late_pickup.py')==json.loads((OUT/'accepted_owned_hashes.json').read_text())['tests/routing/test_single_batch_late_pickup.py']
print(json.dumps(decision,indent=2))
