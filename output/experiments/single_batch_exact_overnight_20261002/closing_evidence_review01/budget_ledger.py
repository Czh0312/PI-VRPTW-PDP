"""Aggregate registered comparison budgets without mixing proof scopes."""
from pathlib import Path
from datetime import datetime, timezone
import json
import math
import hashlib

OUT = Path(__file__).resolve().parent
NIGHT = OUT.parent


def main():
    assert not (OUT/'budget_ledger.json').exists()
    targets = [
        ('label_bound01/full60', 'complete_hybrid_M2', 'wall_time'),
        ('label_bound01/full60_v2', 'complete_hybrid_M2', 'wall_time'),
        ('certificate_ablation01/full30', 'offline_complete_M2_kernel', 'wall_time'),
        ('pickup_route_relaxation01/strength15_30', 'M3_M4_root_relaxation_only', 'runtime'),
        ('pickup_column_supply01/paired15_30', 'M4_root_relaxation_only', 'runtime'),
        ('pickup_column_supply01/independent15_30', 'M4_root_relaxation_only', 'runtime'),
        ('e1_route_cover01/paired15_30', 'M3_root_relaxation_only', 'runtime'),
        ('e1_component_kernel01/full15_30', 'complete_M3_kernel_with_all_component_cost', 'runtime'),
        ('e1_clocked_component_kernel01/full15_30', 'complete_M3_kernel_with_all_component_cost', 'runtime'),
    ]
    groups = []
    for batch, scope, field in targets:
        path = NIGHT/batch/'comparison.json'
        rows = json.loads(path.read_text())
        assert rows and all(r['budget'] > 0 and r[field] >= 0 for r in rows)
        groups.append(dict(batch=batch, proof_scope=scope, rows=len(rows),
                           allocated_seconds=math.fsum(r['budget'] for r in rows),
                           measured_seconds=math.fsum(r[field] for r in rows),
                           measured_field=field, budgets=sorted({r['budget'] for r in rows}),
                           seeds=sorted({r['seed'] for r in rows}),
                           cases=sorted({r['case'] for r in rows}),
                           raw_comparison_sha256=hashlib.sha256(path.read_bytes()).hexdigest()))
    ledger = dict(observed_utc=datetime.now(timezone.utc).isoformat(), groups=groups,
                  total_comparison_jobs=sum(g['rows'] for g in groups),
                  total_registered_comparison_budget=math.fsum(g['allocated_seconds'] for g in groups),
                  total_measured_comparison_seconds=math.fsum(g['measured_seconds'] for g in groups),
                  known_original_integer_setup_jobs=2, setup_registered_seconds_each=20,
                  no_solver_created=True,
                  limitation='Sums of logged stage counters, not overnight elapsed wall time or a fair cross-method speed metric. Imports/process overhead/external waits, correctness oracles, tests, setup preparation and untimed mechanism diagnostics are not included. Scope differs by group; root LP is not original integer proof. M4 price balance still pending and excluded.')
    (OUT/'budget_ledger.json').write_text(json.dumps(ledger, indent=2)+'\n')
    print(json.dumps(ledger, indent=2))


if __name__ == '__main__':
    main()
