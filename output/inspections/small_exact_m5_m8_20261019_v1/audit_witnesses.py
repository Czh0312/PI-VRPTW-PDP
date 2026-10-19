"""Replay stored physical schedules without passing them to any solver."""
import hashlib
import json
from pathlib import Path
import sys
import zipfile

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[2]
sys.path.insert(0, str(ROOT))
from models.common.cycle import load_instance
from models.common.cycle.audit import audit_solution
from tools.configuration.loader import load_routing_case


def main():
    data = ROOT/'data/formal_benchmark27_v1'
    evidence = json.loads((data/'validation/feasibility.json').read_text(encoding='utf-8'))
    archive = data/'validation/witnesses.zip'
    assert hashlib.sha256(archive.read_bytes()).hexdigest() == evidence['archive_sha256']
    rows = []
    with zipfile.ZipFile(archive) as zipped:
        for case in ('S01', 'S04', 'S07'):
            for model in range(5, 9):
                member = f'{case}/model_{model}.json'
                payload = zipped.read(member)
                assert hashlib.sha256(payload).hexdigest() == evidence['witness_sha256'][member]
                config, fleet = load_routing_case(data/f'model_{model}/small/{case}/case.json', use_pool_config=False)
                problem = load_instance(config, fleet)
                witness = json.loads(payload)
                audit = audit_solution(problem, witness)
                rows.append(dict(case=case, model=model, valid=bool(audit['valid']),
                                 periodic_replay_valid=audit.get('periodic_replay', {}).get('valid'),
                                 errors=audit.get('errors'), objective=audit.get('objective'),
                                 sha256=hashlib.sha256(payload).hexdigest()))
    result = dict(valid=all(row['valid'] for row in rows), selected=12, rows=rows,
                  scope='Stored physical schedules, independently replayed against current inputs and audit code',
                  optimization_calls_in_this_script=0, supplied_to_exact_solver=False)
    (OUT/'witness_audit.json').write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
    print(json.dumps(dict(valid=result['valid'], passed=sum(row['valid'] for row in rows), failed=sum(not row['valid'] for row in rows))))
    return 0 if result['valid'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
