"""An actual TIME_LIMIT without a native bound retains an existing bound."""
import gurobipy as gp
import pytest

from study import master_lower_bound


def test_time_limit_without_bound_is_not_infeasibility_or_error():
    m = gp.Model()
    try:
        m.Params.OutputFlag = 0
        m.Params.Presolve = 0
        m.Params.Heuristics = 0
        m.Params.TimeLimit = 0
        x = m.addVars(100, vtype=gp.GRB.BINARY)
        cost = m.addVar(lb=-gp.GRB.INFINITY)
        m.addConstr(gp.quicksum((i+1)*x[i] for i in range(100)) <= 2391)
        m.addConstr(x.sum() >= 20)
        m.addConstr(cost >= gp.quicksum((i*i+3)*x[i] for i in range(100)))
        m.setObjective(cost)
        m.optimize()
        assert m.Status == gp.GRB.TIME_LIMIT
        assert master_lower_bound(m, 12.5) == 12.5
    finally:
        m.dispose()


def test_infeasible_master_still_reports_a_contradiction():
    m = gp.Model()
    try:
        m.Params.OutputFlag = 0
        x = m.addVar(lb=0, ub=1)
        m.addConstr(x >= 2)
        m.optimize()
        assert m.Status == gp.GRB.INFEASIBLE
        with pytest.raises(RuntimeError, match='contradicts'):
            master_lower_bound(m, 0.)
    finally:
        m.dispose()
