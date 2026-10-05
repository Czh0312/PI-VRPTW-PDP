"""Reaudit both trials with a fresh snapshot of the externally updated common code."""
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
import gzip
import hashlib
import json
import shutil
import sys

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[2]

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')

def freeze():
    files = [p for base in ('models', 'configuration', 'config') for p in sorted((ROOT / base).rglob('*'))
             if p.is_file() and p.suffix in ('.py', '.json', '.md') and '__pycache__' not in p.parts]
    before = {str(p.relative_to(ROOT)): sha(p) for p in files}
    target = OUT / 'current_source'
    target.mkdir()
    for p in files:
        dest = target / p.relative_to(ROOT)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(p, dest)
    checks = {rel: sha(ROOT / rel) == digest == sha(target / rel) for rel, digest in before.items()}
    write(OUT / 'current_source_manifest.json', before)
    write(OUT / 'freeze_verification.json', dict(time=datetime.now(ZoneInfo('Asia/Shanghai')).isoformat(),
          files=len(files), source_stable_during_copy=all(checks.values()), checks=checks))
    assert all(checks.values()), 'External source changed during snapshot; preserved attempt, no validation claim'

def audit():
    source = OUT / 'current_source'
    sys.path.insert(0, str(source))
    from configuration.loader import load_routing_case
    from models.routing.common.cycle import load_instance
    from models.routing.common.cycle.assignment import assigned_problem, lift_result
    from models.routing.common.cycle.audit import audit_solution
    from models.routing.solvers.alns.evaluator import evaluate
    from models.routing.solvers.alns.neighborhoods import state_from_result
    assert Path(evaluate.__code__.co_filename).resolve().is_relative_to(source)
    output = OUT / 'current_fresh_decodes'
    output.mkdir()
    checks = []
    for trial in ('alns_single_batch_restart_credit_20261005', 'alns_single_batch_diverse_descent_20261005'):
        folder = OUT.parent / trial
        rows = json.loads((folder / 'pilot/summary.json').read_text())
        for row in rows:
            name = '%03d_%s.json' % (row['index'], row['source'])
            raw = json.loads((folder / 'pilot' / name).read_text())
            config, fleet = load_routing_case(row['case'])
            original = load_instance({**config, 'model': row['model']}, fleet)
            fixed = assigned_problem(original, raw['result']['assignment']) if original.free_assignment else original
            _, decoded, issues = evaluate(fixed, state_from_result(fixed, raw['result']))
            fixed_audit = audit_solution(fixed, decoded)
            if original.free_assignment:
                decoded = lift_result(original, decoded, raw['result']['assignment'])
            checked = audit_solution(original, decoded)
            record = dict(trial=trial, name=name, model=row['model'], issues=issues,
                valid=not issues and fixed_audit['valid'] and fixed_audit['periodic_replay']['valid']
                       and checked['valid'] and checked['periodic_replay']['valid'],
                equal=abs(decoded['info']['objective']-row['objective']) <= 1e-8,
                original=row['objective'], decoded=decoded['info']['objective'])
            with gzip.open(output / (trial + '_' + name + '.gz'), 'wt') as handle:
                json.dump(dict(check=record, decoded=decoded, fixed_audit=fixed_audit, audit=checked), handle)
            checks.append(record)
    write(OUT / 'current_fresh_audit.json', checks)
    passed = len(checks) == 80 and all(r['valid'] and r['equal'] for r in checks)
    write(OUT / 'audit_decision.json', dict(passed=passed, quality_results=len(checks),
          valid_equal=sum(r['valid'] and r['equal'] for r in checks),
          scope='Feasibility/objective reconstruction only against captured current context, not a new quality comparison or solver adoption.'))
    print('Updated-context full audits:', sum(r['valid'] and r['equal'] for r in checks), '/', len(checks))
    assert passed

if __name__ == '__main__':
    {'freeze': freeze, 'audit': audit}[sys.argv[1]]()
