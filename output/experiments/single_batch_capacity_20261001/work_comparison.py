"""Paired work-limit experiments; all phases use the same saved audited starts."""
import importlib.util
import json
from pathlib import Path
import sys
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
OUT = Path(__file__).resolve().parent
PREVIOUS = ROOT/'output/experiments/single_batch_routes_20261001'
from configuration.loader import load_routing_case
from models.routing.common.cycle import load_instance
from models.routing.common.cycle.audit import audit_solution
from models.routing.solvers.exact import solver


def frozen_builder(phase):
    spec = importlib.util.spec_from_file_location(
        f'models.routing.solvers.exact._capacity_{phase}',
        OUT/phase/'source_snapshot/models/routing/solvers/exact/single_batch_formulation.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.build_model


variants = sys.argv[1:] or ['before', 'all']
extended = '--extended' in variants
variants = [variant for variant in variants if variant != '--extended']
rows = []
path = OUT/('work_'+'_'.join(variants)+('_extended' if extended else '')+'.json')
if path.exists():
    raise FileExistsError(path)
for size, number in (((30, 4),) if extended else ((20, 3), (30, 3), (20, 4), (30, 2))):
    run = f'zhou_Set2_100-5-1b_subset{size}_M{number}'
    p = load_instance(*load_routing_case(ROOT/f'output/experiments/single_batch_strengthening_20260930/cases/{run}.json'))
    if extended:
        from models.routing.common.cycle.assignment import lift_result, input_assignment
        initial = json.loads((PREVIOUS/'work_zhou_Set2_100-5-1b_subset30_M3_initial.json').read_text())
        initial = lift_result(p, initial, input_assignment(p))
    else:
        initial = json.loads((PREVIOUS/f'work_{run}_initial.json').read_text())
    assert audit_solution(p, initial)['valid']
    (OUT/f'{run}_initial.json').write_text(json.dumps(initial, indent=2))
    for variant in variants:
        builder = frozen_builder('before' if variant == 'before' else 'candidate')
        def bounded(*args, **kwargs):
            if variant != 'before':
                kwargs['capacity_cuts'] = variant
            model, context = builder(*args, **kwargs)
            model.Params.WorkLimit = 15
            return model, context
        with patch.object(solver, 'build_model', side_effect=bounded), patch.object(
                solver, 'construct_incumbent', return_value=None):
            result = solver.solve_exact(p, time_limit=60, seed=42, initial_result=initial)
        audit = audit_solution(p, result)
        row = dict(run=run, variant=variant, initial_objective=initial['info']['objective'],
                   audit_valid=audit['valid'], info=result['info'])
        rows.append(row)
        (OUT/f'work_{run}_{variant}.json').write_text(json.dumps(result, indent=2))
        path.write_text(json.dumps(rows, indent=2))
        print(run, variant, audit['valid'], {key: result['info'].get(key) for key in (
            'objective', 'best_bound', 'mip_gap', 'mip_work', 'mip_runtime', 'status',
            'capacity_cut_candidates', 'capacity_cuts_added')}, flush=True)
        assert audit['valid']
