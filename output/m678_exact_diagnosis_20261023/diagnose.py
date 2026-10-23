"""Read saved S04 evidence, re-audit schedules and certify impossible K blocks.

No optimization runs. Old exact performance is explicitly historical evidence.
"""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from models.common.cycle import load_instance
from models.common.cycle.audit import audit_solution
from models.common.cycle.count_screening import infeasible_count_certificate
from models.common.cycle.pool_bound import necessary_pool_lower_bound
from tools.configuration.loader import load_routing_case

OUT = Path(__file__).resolve().parent
SAVED = ROOT / 'output/alns_small_scale_gap_20261021'


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    rows, inputs = [], {}
    for number in (6, 7, 8):
        case = ROOT / f'data/formal_benchmark27_spacetime_v2/model_{number}/small/S04/case.json'
        p = load_instance(*load_routing_case(case))
        saved = SAVED / f'alns/m{number}_S04/result.json'
        audit = audit_solution(p, json.loads(saved.read_text()))
        assert audit['valid'] and audit['periodic_replay']['valid']
        old = SAVED / f'exact/m{number}_S04/summary.json'
        old_info = json.loads(old.read_text())
        assert sha(case) == old_info['input_sha256']
        counts = []
        for k in p.batch_count_candidates:
            c = p.for_count(k)
            counts.append(dict(k=k, certificate=infeasible_count_certificate(c),
                possible_orders_per_batch={b.id: [o.id for o in c.orders if b.id in c.legal_batches[o.id]]
                                           for b in c.operating_policy.batches},
                necessary_pool_lower_bound=necessary_pool_lower_bound(c),
                source_pool_min=c.pool_min))
        rows.append(dict(model=number, case='S04', customers=len({o.customer for o in p.orders}),
            counts=counts, retained_counts=[r['k'] for r in counts if r['certificate'] is None],
            historical_exact=old_info, current_reaudit=audit))
        paths = list(case.parent.glob('*.json')) + [saved, old]
        for path in paths:
            inputs[str(path.relative_to(ROOT))] = sha(path)
    for path in (ROOT / 'data/formal_benchmark27_spacetime_v2/shared/small/S04').iterdir():
        if path.suffix in ('.csv', '.json'):
            inputs[str(path.relative_to(ROOT))] = sha(path)
    sources = [p for folder in ('models/common', 'models/cost', 'models/routing/model_6/exact',
        'models/routing/model_7/exact', 'models/routing/model_8/exact', 'tools/configuration')
        for p in (ROOT / folder).rglob('*.py')]
    historical_hashes = json.loads((SAVED / 'manifest.json').read_text())['source_sha256']
    source_hashes = {str(p.relative_to(ROOT)): sha(p) for p in sources}
    changed = [p for p, h in source_hashes.items() if p in historical_hashes and historical_hashes[p] != h]
    data = dict(kind='static_diagnosis_and_saved_schedule_reaudit',
        created_at=datetime.now(timezone.utc).isoformat(), optimization_runs=0,
        rows=rows, input_and_evidence_sha256=inputs, source_sha256=source_hashes,
        changed_existing_sources_since_historical_run=changed)
    (OUT / 'diagnostics.json').write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(dict(changed_existing_sources=changed,
        results=[dict(model=r['model'], retained_counts=r['retained_counts'],
                      reaudit_valid=r['current_reaudit']['valid'],
                      old_variables=r['historical_exact']['variables'],
                      old_constraints=r['historical_exact']['constraints']) for r in rows]),
        ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
