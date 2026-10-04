"""Alternate build order; verify identical models while timing coverage cache."""
import importlib.util
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
OUT = Path(__file__).resolve().parent
from configuration.loader import load_routing_case
from models.routing.common.cycle import load_instance
from models.routing.common.cycle.precheck import travel_lower_bounds
from models.routing.solvers.exact.domains import assignment_domains

builders = {}
for phase in ('before', 'tight_bounds'):
    spec = importlib.util.spec_from_file_location(f'models.routing.solvers.exact._build_{phase}',
        OUT/phase/'source_snapshot/models/routing/solvers/exact/single_batch_formulation.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    builders[phase] = module.build_model
rows = []
for number in (1, 2, 3, 4):
    run = f'zhou_Set2_100-5-1b_subset30_M{number}'
    p = load_instance(*load_routing_case(ROOT/f'output/experiments/single_batch_strengthening_20260930/cases/{run}.json'))
    travel = travel_lower_bounds(p)
    domains = assignment_domains(p, travel) if p.free_assignment else None
    fingerprints = set()
    for repeat in range(3):
        for phase in (('before', 'tight_bounds') if repeat % 2 == 0 else ('tight_bounds', 'before')):
            kwargs = dict(travel_bounds=travel, domain_check=domains)
            if phase != 'before':
                kwargs['tight_route_bounds'] = False
            start = time.perf_counter()
            m, ctx = builders[phase](p, **kwargs)
            elapsed = time.perf_counter()-start
            try:
                fingerprints.add(m.Fingerprint)
                rows.append(dict(run=run, repeat=repeat, phase=phase, build_seconds=elapsed,
                    fingerprint=m.Fingerprint, variables=m.NumVars, linear_rows=m.NumConstrs,
                    general_constraints=m.NumGenConstrs, nonzeros=m.NumNZs))
            finally:
                m.dispose()
            (OUT/'build_comparison.json').write_text(json.dumps(rows, indent=2))
            print(run, repeat, phase, round(elapsed, 4), flush=True)
    assert len(fingerprints) == 1, (run, fingerprints)
