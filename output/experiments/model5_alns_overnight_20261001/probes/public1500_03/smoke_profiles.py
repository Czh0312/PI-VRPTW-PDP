"""Actual public entry with its own ALNS, no injected incumbent."""
import json, sys
from pathlib import Path
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / 'source_snapshot'))
sys.path.insert(1, str(HERE.parents[4]))
from tests.routing.test_multibatch_cycle import fixture
from models.routing.common.cycle.audit import audit_solution
import models.routing.service as service
from models.routing.variants.model_5.exact import solve_hybrid
p = fixture(pool=2)
original_service = service.solve_routing
out = solve_hybrid(p, time_limit=2, alns_time_limit=.1, primal_time_limit=0,
    cover_time_limit=1.25, conditional_time_limit=.3, seed=53, adaptive_pricing_mode='resume',
    count_dual_repair=True, capacity_dual_repair=True, extra_slots_per_vehicle=1,
    route_pool_max_columns=64, max_supply_conjunctions=1, subset_time_fraction=0)
assert service.solve_routing is original_service
assert audit_solution(p, out['initial_result'])['valid']
audit = audit_solution(p, out['result'])
assert audit['valid'] and audit['periodic_replay']['valid']
assert out['bound'] <= out['upper_bound'] + 1e-6
row = dict(status='PASSED', direct_public_entry=True, service_not_monkeypatched=True,
    upper=out['upper_bound'], lower=out['bound'], gap=out['gap'], physical_and_periodic_audit=True)
(HERE / 'smoke_profiles.json').write_text(json.dumps(row, indent=2) + '\n')
print(row, flush=True)
