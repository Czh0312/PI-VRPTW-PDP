"""Opt-in starts remain feasible, preserve the domain, and carry no proofs."""
from copy import deepcopy
from importlib import import_module

import pytest

from experiments.analysis.single_batch_mip_start import apply_mip_start, canonical_start
from models.common.cycle.audit import audit_solution
from models.common.cycle.state import empty_result
from models.routing.service import solve_routing
from tests.routing.test_single_batch_comparison import problem


@pytest.mark.parametrize('number', [1, 2, 3, 4])
@pytest.mark.parametrize('compact', [False, True])
def test_audited_start_is_accepted_without_fixing_free_assignment(number, compact):
    from gurobipy import GRB
    p = problem(number)
    source_number = number-1 if number in (2, 4) else number
    initial = solve_routing(model=source_number, method='exact', problem=problem(source_number), exact_time=5)
    before = deepcopy(initial)
    model, context = import_module(f'models.routing.model_{number}.exact.formulation').build_model(p, compress_routes=compact)
    try:
        original = (model.NumVars, model.NumConstrs, model.NumGenConstrs,
                    [(v.LB, v.UB) for v in model.getVars()])
        mapped = apply_mip_start(p, model, context, initial)
        model.update()
        assert original == (model.NumVars, model.NumConstrs, model.NumGenConstrs,
                            [(v.LB, v.UB) for v in model.getVars()])
        assert not mapped['canonical_result']['info']['proven_optimal']
        assert mapped['canonical_result']['info']['status'] == 'INITIAL_FEASIBLE'
        assert initial == before
        model.Params.TimeLimit = 5
        messages = []
        model.Params.OutputFlag = 1
        model.Params.LogToConsole = 0
        def observe(m, where):
            if where == GRB.Callback.MESSAGE:
                messages.append(m.cbGet(GRB.Callback.MSG_STRING))
        model.optimize(observe)
        assert any('Loaded user MIP start' in line or 'User MIP start produced solution' in line
                   for line in messages)
        assert model.Status == 2
        result = empty_result(p, 'trial')
        result['info']['objective'] = model.ObjVal
        import_module(f'models.routing.model_{number}.exact.solution').extract_solution(p, model, context, result)
        assert audit_solution(p, result)['valid']
        if number in (2, 4):
            assert result['assignment'] == {'C': 'T'}
            assert model.ObjVal < initial['info']['objective']
    finally:
        model.dispose()


def test_rejects_a_corrupted_initial_plan_before_mapping():
    p = problem(3)
    initial = solve_routing(model=3, method='exact', problem=p, exact_time=5)
    initial['e2_trips'][0]['events'].pop()
    with pytest.raises(ValueError, match='Invalid initial plan'):
        canonical_start(p, initial)


def test_rejects_cross_packaging_start():
    initial = solve_routing(model=1, method='exact', problem=problem(1), exact_time=5)
    with pytest.raises(ValueError, match='Only same-model'):
        canonical_start(problem(4), initial)
