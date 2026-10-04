"""Independent set-cover counterexample and full optimum projections."""
from types import SimpleNamespace
import itertools
import pytest
from models.routing.solvers.exact.multibatch_task_pairs import (
    add_task_pair_integrality, project_task_pairs)
from models.routing.solvers.exact.multibatch_joint_inventory import build_joint_inventory, set_joint_projection
from models.routing.solvers.exact.solver import solve_exact
from tests.routing.test_multibatch_departure_loads import nonmetric_problem
from tests.routing.test_multibatch_joint_inventory import domains


def test_half_triangle_reaches_independently_enumerated_integer_cover():
    gp = pytest.importorskip('gurobipy')
    tasks = [('a', 'd'), ('b', 'd'), ('c', 'd')]
    sets = [set(pair) for pair in itertools.combinations(tasks, 2)] + [{key} for key in tasks]
    optimum = min(sum(bits) for bits in itertools.product((0, 1), repeat=len(sets))
                  if all(sum(bit for bit, row in zip(bits, sets) if key in row) == 1 for key in tasks))
    problem = SimpleNamespace(orders=[SimpleNamespace(id=oid, satellite='S') for oid, _ in tasks])
    model = gp.Model(); model.Params.OutputFlag = 0; model.Params.Threads = 1
    try:
        variables = [model.addVar(obj=1) for _ in sets]
        for key in tasks:
            model.addConstr(gp.quicksum(v for v, row in zip(variables, sets) if key in row) == 1)
        context = {'routes': {'S': {'rows': [{'keys': row} for row in sets],
                                   'choices': {(i, None): v for i, v in enumerate(variables)}}}}
        model.optimize(); assert model.ObjVal == pytest.approx(1.5)
        add_task_pair_integrality(problem, model, context, 'S', [tasks[:2]])
        model.optimize(); assert model.Status == gp.GRB.OPTIMAL
        assert model.ObjVal == pytest.approx(optimum) == 2
    finally:
        model.dispose()


@pytest.mark.parametrize('seed', range(6))
@pytest.mark.parametrize('timed', [False, True])
def test_all_pair_indicators_preserve_known_full_optimum(seed, timed):
    gp = pytest.importorskip('gurobipy')
    problem = nonmetric_problem(seed)
    initial = solve_exact(problem, time_limit=5)
    assert initial['info']['proven_optimal']
    model, context = build_joint_inventory(problem, domains(problem, timed=timed))
    try:
        records = []
        for site in context['routes']:
            tasks = [(o.id, k) for o in problem.orders if o.satellite == site for k in ('d', 'p')]
            records += add_task_pair_integrality(problem, model, context, site,
                list(itertools.combinations(tasks, 2)), name='pair_' + site)
        model.optimize(); assert model.Status == gp.GRB.OPTIMAL
        assert model.ObjBound <= initial['info']['objective'] + 1e-6
        set_joint_projection(problem, context, initial, fix=True)
        project_task_pairs(problem, initial, records, fix=True)
        model.optimize(); assert model.Status == gp.GRB.OPTIMAL
        assert model.ObjVal <= initial['info']['objective'] + 1e-6
    finally:
        model.dispose()


@pytest.mark.parametrize('pair', [[], [('a', 'd')], [('a', 'd'), ('a', 'd')],
                                 [('a', 'd'), ('missing', 'p')]])
def test_invalid_pair_rejected_before_model_mutation(pair):
    gp = pytest.importorskip('gurobipy')
    model = gp.Model(); model.Params.OutputFlag = 0
    problem = SimpleNamespace(orders=[SimpleNamespace(id='a', satellite='S')])
    try:
        with pytest.raises(ValueError, match='two distinct'):
            add_task_pair_integrality(problem, model, {'routes': {'S': {}}}, 'S', [pair])
        model.update(); assert model.NumVars == model.NumConstrs == 0
    finally:
        model.dispose()
