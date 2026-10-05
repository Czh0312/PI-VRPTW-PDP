"""Read-only verification of historical best artifacts; write only this run's QA."""
import hashlib
import json
from pathlib import Path
import sys

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[2]
OLD = ROOT/'output/experiments/m5_overnight_20261001'
sys.path.insert(0,str(OUT/'early_repair30/source_snapshot'))
from configuration.loader import load_routing_case
from models.routing.common.cycle import load_instance
from models.routing.common.cycle.audit import audit_solution
from models.routing.solvers.exact.multibatch_logic_benders import business_fingerprint,source_fingerprint


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


checks = {}
baseline = json.loads((OUT/'20_cold/protected_before.json').read_text())
for path,expected in baseline.items():
    assert sha(path) == expected,path
    checks[path] = dict(sha256=expected,evidence='task_start_protected_sha',unchanged=True)
for name in ('manifest_overnight_stop.json','manifest_phase6_final.json',
             'manifest_phase11_final.json','manifest_phase14_final.json'):
    manifest = json.loads((OLD/name).read_text())
    entries = manifest.get('sha256',manifest.get('source_and_input_sha256',manifest))
    wanted = {'checkpoint_at_stop.json','m5_overnight_20261002.md',
              'no_good.mps.gz','master.mps.gz','combined_certificate.json'}
    for path,expected in entries.items():
        if not isinstance(expected,str) or len(expected)!=64:continue
        p=Path(path)
        if not p.is_absolute():
            p=(ROOT if path.startswith(('output/','models/','tests/','configuration/','config/','docs/')) else OLD)/p
        if p.name not in wanted:continue
        assert sha(p)==expected,p
        checks[str(p)]=dict(sha256=expected,evidence=name,unchanged=True)
assert (OLD/'checkpoint.json').read_bytes()==(OLD/'checkpoint_at_stop.json').read_bytes()
upper_sources = [(OLD/'combined_certificate.json','upper_sha256',OLD/'best_full_result.json'),
    (OLD/'master49/combined_certificate.json','full_upper_sha256',OLD/'medium25/0/inputs/initial_result.json'),
    (OLD/'medium25/1/combined_certificate.json','upper_sha256',OLD/'medium25/1/best_full_result.json')]
for certificate,key,path in upper_sources:
    expected=json.loads(certificate.read_text())[key]
    assert sha(path)==expected,path
    checks[str(path)]=dict(sha256=expected,evidence=str(certificate),unchanged=True)
problem=load_instance(*load_routing_case(OLD/'inputs/case.json'))
result=json.loads((OLD/'best_full_result.json').read_text())
audit=audit_solution(problem,result)
assert audit['valid'] and abs(audit['objective']-2235.813359002067)<1e-9
config=Path('/Users/chenzeheng/.codex/automations/m5/automation.toml').read_text()
automation_status=next(line.strip() for line in config.splitlines() if line.strip().startswith('status'))
assert 'PAUSED' in automation_status
report=dict(protected=checks,all_official_best_artifacts_unchanged=True,
    original_50_upper_reaudit=audit,business_fingerprint=business_fingerprint(problem),
    audit_source_fingerprint=source_fingerprint(),audit_source_snapshot=str(OUT/'early_repair30/source_snapshot'),
    automation_status=automation_status,automation_changed=False,lower_replay_not_repeated=True,
    full_proven_optimal=False)
(OUT/'diagnostics/protected_best_reaudit.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
print(json.dumps(dict(protected_files=len(checks),upper_50_valid=audit['valid'],upper_50=audit['objective'],
    automation_status=automation_status)))
