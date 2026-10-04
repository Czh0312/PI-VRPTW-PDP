"""Investigate the small-M4 regression and repeat large M4 at two new seeds."""
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

rows = []
path = OUT/'robustness_comparison.json'
if path.exists():
    raise FileExistsError(path)
jobs = [('zhou_Set1_12-2-1_M4', 42)]+[
    (f'zhou_Set2_100-5-1b_subset{size}_M4', seed) for size in (20, 30) for seed in (43, 44)]
for run, seed in jobs:
    p = load_instance(*load_routing_case(ROOT/f'output/experiments/single_batch_strengthening_20260930/cases/{run}.json'))
    initial_path = (OUT/'wall_before'/run/'result.json' if run.startswith('zhou_Set1') else
                    OUT/f'{run}_initial.json')
    initial = json.loads(initial_path.read_text())
    assert audit_solution(p, initial)['valid']
    for phase in ('before', 'site_flow'):
        spec = importlib.util.spec_from_file_location(
            f'models.routing.solvers.exact._robust_{phase}',
            OUT/phase/'source_snapshot/models/routing/solvers/exact/single_batch_formulation.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        def bounded(*args, **kwargs):
            if phase == 'site_flow':
                kwargs['site_flow'] = True
            m, ctx = module.build_model(*args, **kwargs)
            m.Params.WorkLimit = 30
            return m, ctx
        with patch.object(solver, 'build_model', side_effect=bounded), patch.object(
                solver, 'construct_incumbent', return_value=None):
            result = solver.solve_exact(p, time_limit=120, seed=seed, initial_result=initial)
        audit = audit_solution(p, result)
        rows.append(dict(run=run, phase=phase, seed=seed, initial_objective=initial['info']['objective'],
                         audit_valid=audit['valid'], periodic_replay=audit['periodic_replay']['valid'],
                         info=result['info']))
        (OUT/f'robust_{run}_seed{seed}_{phase}.json').write_text(json.dumps(result, indent=2))
        path.write_text(json.dumps(rows, indent=2))
        print(run, seed, phase, audit['valid'], {key: result['info'].get(key) for key in (
            'objective', 'best_bound', 'mip_gap', 'mip_work', 'mip_runtime', 'status')}, flush=True)
        assert audit['valid'] and audit['periodic_replay']['valid']
