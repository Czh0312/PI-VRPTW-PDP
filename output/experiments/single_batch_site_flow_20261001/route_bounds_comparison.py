"""Same-start, same-work contrast of task-specific route identity bounds."""
import importlib.util
import json
from pathlib import Path
import sys
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
OUT = Path(__file__).resolve().parent
from configuration.loader import load_routing_case
from models.routing.common.cycle import load_instance
from models.routing.common.cycle.audit import audit_solution
from models.routing.solvers.exact import solver
from experiments.runs.run_multibatch_cycle import source_snapshot, write_json

stage = OUT/'tight_bounds'
stage.mkdir()
write_json(stage/'source_hashes.json', source_snapshot(stage))
rows = []
path = OUT/'route_bounds_comparison.json'
for size, number in ((20, 4), (30, 2), (30, 3), (30, 4)):
    run = f'zhou_Set2_100-5-1b_subset{size}_M{number}'
    p = load_instance(*load_routing_case(ROOT/f'output/experiments/single_batch_strengthening_20260930/cases/{run}.json'))
    initial = json.loads((ROOT/f'output/experiments/single_batch_capacity_20261001/{run}_initial.json').read_text())
    assert audit_solution(p, initial)['valid']
    for phase in ('before', 'tight_bounds'):
        spec = importlib.util.spec_from_file_location(
            f'models.routing.solvers.exact._identity_{phase}',
            OUT/phase/'source_snapshot/models/routing/solvers/exact/single_batch_formulation.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        def bounded(*args, **kwargs):
            m, ctx = module.build_model(*args, **kwargs)
            m.Params.WorkLimit = 30
            return m, ctx
        with patch.object(solver, 'build_model', side_effect=bounded), patch.object(
                solver, 'construct_incumbent', return_value=None):
            result = solver.solve_exact(p, time_limit=120, seed=42, initial_result=initial)
        audit = audit_solution(p, result)
        rows.append(dict(run=run, phase=phase, audit_valid=audit['valid'],
                         periodic_replay=audit['periodic_replay']['valid'], info=result['info']))
        (OUT/f'identity_{run}_{phase}.json').write_text(json.dumps(result, indent=2))
        path.write_text(json.dumps(rows, indent=2))
        print(run, phase, {key: result['info'].get(key) for key in (
            'objective', 'best_bound', 'mip_gap', 'mip_work', 'mip_runtime', 'status')}, flush=True)
        assert audit['valid'] and audit['periodic_replay']['valid']
