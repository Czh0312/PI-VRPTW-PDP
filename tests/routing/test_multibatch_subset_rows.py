"""Independent integer covers and known complete optimum projections."""
from itertools import combinations, product
from types import SimpleNamespace
import pytest
from models.routing.model_5.exact.bounds.route_cover_cg.subset_rows import add_triangle_rows, separate_task_triangles
from models.routing.model_5.exact.bounds.inventory.joint_inventory import build_joint_inventory, set_joint_projection
from models.routing.model_5.exact.enhanced_solver import solve_exact
from tests.routing.test_multibatch_departure_loads import nonmetric_problem
from tests.routing.test_multibatch_joint_inventory import domains


def test_half_triangle_cut_and_independent_integer_cover():
    gp = pytest.importorskip('gurobipy')
    tasks = [('a', 'd'), ('b', 'd'), ('c', 'd')]
    sets = [set(keys) for keys in combinations(tasks, 2)]+[{key} for key in tasks]
    optimum = min(sum(bits) for bits in product((0, 1), repeat=6)
                  if all(sum(bit for bit, keys in zip(bits, sets) if key in keys)==1 for key in tasks))
    violations = separate_task_triangles(tasks, [(keys, .5) for keys in sets[:3]])
    assert len(violations)==1 and violations[0]['lhs']==pytest.approx(1.5)
    problem = SimpleNamespace(orders=[SimpleNamespace(id=oid, satellite='S') for oid, _ in tasks])
    model = gp.Model(); model.Params.OutputFlag=0; model.Params.Threads=1
    try:
        variables = [model.addVar(obj=1) for _ in sets]
        for key in tasks:model.addConstr(gp.quicksum(v for v, keys in zip(variables, sets) if key in keys)==1)
        context={'routes': {'S': {'rows': [{'keys': keys} for keys in sets],
                                  'choices': {(i, None): v for i, v in enumerate(variables)}}}}
        model.optimize(); assert model.ObjVal==pytest.approx(1.5)
        add_triangle_rows(problem, model, context, 'S', [tasks])
        model.optimize(); assert model.ObjVal==pytest.approx(optimum)==2
        assert model.NumBinVars==0
    finally:model.dispose()


@pytest.mark.parametrize('seed', range(6))
@pytest.mark.parametrize('timed', [False, True])
def test_all_triangles_preserve_known_full_optimum(seed, timed):
    gp=pytest.importorskip('gurobipy');problem=nonmetric_problem(seed)
    initial=solve_exact(problem,time_limit=5);assert initial['info']['proven_optimal']
    model,context=build_joint_inventory(problem,domains(problem,timed=timed))
    try:
        for site in context['routes']:
            tasks=[(o.id,k) for o in problem.orders if o.satellite==site for k in ('d','p')]
            add_triangle_rows(problem,model,context,site,list(combinations(tasks,3)),name='triangle_'+site)
        model.optimize();assert model.Status==gp.GRB.OPTIMAL
        assert model.ObjBound<=initial['info']['objective']+1e-6
        set_joint_projection(problem,context,initial,fix=True)
        model.optimize();assert model.Status==gp.GRB.OPTIMAL
        assert model.ObjVal<=initial['info']['objective']+1e-6
    finally:model.dispose()


def test_full_cover_with_one_three_task_trip_counts_once():
    tasks=[('a','d'),('b','d'),('c','d')]
    assert separate_task_triangles(tasks,[(tasks,1)])==[]
    assert separate_task_triangles(tasks,[([key],1) for key in tasks])==[]
