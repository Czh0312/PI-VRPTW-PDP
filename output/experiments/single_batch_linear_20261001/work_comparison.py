"""Use identical audited starts and a deterministic 15-work-unit MIP budget."""
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


def frozen_builder(phase):
    spec = importlib.util.spec_from_file_location(
        f'models.routing.solvers.exact._linear_{phase}',
        OUT/phase/'source_snapshot/models/routing/solvers/exact/single_batch_formulation.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.build_model


variants = sys.argv[1:] or ['before', 'identity', 'internal', 'all']
path = OUT/('work_'+'_'.join(variants)+'.json')
if path.exists():
    raise FileExistsError(path)
rows = []
for size, number in ((20, 3), (30, 3), (20, 4), (30, 2), (30, 4)):
    run = f'zhou_Set2_100-5-1b_subset{size}_M{number}'
    p = load_instance(*load_routing_case(ROOT/f'output/experiments/single_batch_strengthening_20260930/cases/{run}.json'))
    initial = json.loads((ROOT/f'output/experiments/single_batch_capacity_20261001/{run}_initial.json').read_text())
    assert audit_solution(p, initial)['valid']
    (OUT/f'{run}_initial.json').write_text(json.dumps(initial, indent=2))
    for variant in variants:
        builder = frozen_builder('before' if variant == 'before' else
                                 variant if variant in ('complete', 'factored') else 'candidate')
        def bounded(*args, **kwargs):
            if variant != 'before':
                kwargs['linear_e2_links'] = 'complete' if variant == 'factored' else variant
            if variant == 'factored':
                kwargs['factor_e2_loads'] = True
            m, ctx = builder(*args, **kwargs)
            m.Params.WorkLimit = 15
            return m, ctx
        with patch.object(solver, 'build_model', side_effect=bounded), patch.object(
                solver, 'construct_incumbent', return_value=None):
            result = solver.solve_exact(p, time_limit=60, seed=42, initial_result=initial)
        audit = audit_solution(p, result)
        rows.append(dict(run=run, variant=variant, initial_objective=initial['info']['objective'],
                         audit_valid=audit['valid'], info=result['info']))
        (OUT/f'work_{run}_{variant}.json').write_text(json.dumps(result, indent=2))
        path.write_text(json.dumps(rows, indent=2))
        print(run, variant, audit['valid'], {key: result['info'].get(key) for key in (
            'objective', 'best_bound', 'mip_gap', 'mip_work', 'mip_runtime', 'status',
            'general_constraints', 'capacity_cuts_added')}, flush=True)
        assert audit['valid']
