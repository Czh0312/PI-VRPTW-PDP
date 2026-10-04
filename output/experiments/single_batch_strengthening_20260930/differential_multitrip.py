"""Differential proof check against frozen pre-change MILP, with 3 E2 vehicles."""
import importlib.util
import json
from dataclasses import replace
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
OUT = Path(__file__).resolve().parent
from models.routing.common.cycle import MultiBatchInstance
from models.routing.common.cycle.audit import audit_solution
from models.routing.solvers.exact import solver
from tests.routing.test_single_batch_solver_audit import tiny_problem

spec = importlib.util.spec_from_file_location('models.routing.solvers.exact._frozen_before',
    OUT/'before/source_snapshot/models/routing/solvers/exact/formulation.py')
reference = importlib.util.module_from_spec(spec)
spec.loader.exec_module(reference)
current = solver.build_model
rows = []
for number in (1, 2, 3, 4):
    for seed in range(6):
        base = tiny_problem(number, seed)
        base.fleet.e1_count = 2
        base.fleet.e2_per_sat_count = 3
        p = MultiBatchInstance(base.network, base.orders,
            replace(base.operating_policy, e2_max_trips_per_vehicle=3), model=number)
        results = []
        for builder in (reference.build_model, current):
            solver.build_model = builder
            try:
                result = solver.solve_exact(p, time_limit=5)
            finally:
                solver.build_model = current
            if result.get('audit', {}).get('valid'):
                assert audit_solution(p, result)['valid']
            results.append(result['info'])
        before, after = results
        passed = (before['proven_infeasible'] and after['proven_infeasible']) or (
            before['proven_optimal'] and after['proven_optimal']
            and abs(before['objective']-after['objective']) < 1e-7)
        rows.append(dict(model=number, seed=seed, before=before, after=after, passed=passed))
        (OUT/'multitrip_differential.json').write_text(json.dumps(rows, indent=2)+'\n')
        print(number, seed, passed, before['objective'], after['objective'], flush=True)
        assert passed, rows[-1]
