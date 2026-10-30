"""Independent tiny-oracle checks for the research M1 transfer, seed 42 solves."""
from dataclasses import replace
import math
from pathlib import Path
import sys
import time

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[2]))

from candidate import strengthen
from models.common.cycle.instance import MultiBatchInstance
from models.common.cycle.state import empty_result
from models.common.cycle.audit import audit_solution
from models.routing.model_1.exact.model import build_model
from models.routing.model_1.exact.solution import extract_solution
from tests.routing.test_single_batch_solver_audit import tiny_problem, enumerate_tiny


@pytest.mark.parametrize('compact', [False, True])
@pytest.mark.parametrize('fixture', [0, 2, 3, 4])
def test_candidate_preserves_independent_directed_nonmetric_optimum(compact, fixture):
    import gurobipy as gp
    original = tiny_problem(1, fixture)
    problem = MultiBatchInstance(original.network,
        [replace(order, satellite='S') for order in original.orders], original.operating_policy, model=1)
    expected = enumerate_tiny(problem)
    model, context = build_model(problem, compress_routes=compact)
    try:
        strengthen(problem, model, context, deadline=time.perf_counter()+5, seed=42)
        model.Params.Seed = 42
        model.Params.TimeLimit = 10
        model.optimize()
        if math.isinf(expected):
            assert model.Status == gp.GRB.INFEASIBLE
        else:
            assert model.Status == gp.GRB.OPTIMAL
            assert model.ObjVal == pytest.approx(expected, abs=1e-6)
            result = empty_result(problem, 'exact')
            extract_solution(problem, model, context, result)
            audit = audit_solution(problem, result)
            assert audit['valid'] and audit['periodic_replay']['valid']
    finally:
        model.dispose()
