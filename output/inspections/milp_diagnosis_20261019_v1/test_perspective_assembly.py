"""Independent native-indicator vs experimental integer feasible sets."""
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import gurobipy as gp
import pytest

spec = spec_from_file_location('perspective', Path(__file__).with_name('perspective_assembly.py'))
perspective = module_from_spec(spec)
spec.loader.exec_module(perspective)


@pytest.mark.parametrize('trigger', [0, 1])
@pytest.mark.parametrize('sense', ['<', '=', '>'])
@pytest.mark.parametrize('coefficient', [-2, 3])
@pytest.mark.parametrize('active', [0, 1])
def test_every_integer_assignment_matches_native_indicator(trigger, sense, coefficient, active):
    source = gp.Model(); source.Params.OutputFlag = 0
    master = gp.Model(); master.Params.OutputFlag = 0
    try:
        x = source.addVar(lb=-3, ub=4, vtype=gp.GRB.INTEGER, name='signed_x')
        h = source.addVar(vtype=gp.GRB.BINARY, name='trigger')
        source.addGenConstrIndicator(h, trigger, coefficient*x+2, sense, 3)
        source.setObjective(x+9)
        z = master.addVar(vtype=gp.GRB.BINARY)
        mapped, cost = perspective.append_block(master, source, {'x': x, 'h': h}, z, prefix='test_')
        master.setObjective(cost)
        fixed_z = master.addConstr(z == active)
        for h_value in (0, 1):
            fixed_h = master.addConstr(mapped['h'] == h_value)
            for x_value in (range(-3, 5) if active else (0,)):
                fixed_x = master.addConstr(mapped['x'] == x_value)
                master.optimize()
                lhs = coefficient*x_value+2
                indicator_ok = (lhs <= 3 if sense == '<' else lhs >= 3 if sense == '>' else lhs == 3)
                expected = (h_value <= active and (not active or h_value != trigger or indicator_ok))
                assert (master.Status == gp.GRB.OPTIMAL) == expected
                if expected:
                    assert master.ObjVal == pytest.approx(x_value+9*active)
                master.remove(fixed_x)
            master.remove(fixed_h)
    finally:
        source.dispose(); master.dispose()


def test_unselected_infeasible_block_is_harmless():
    source = gp.Model(); source.Params.OutputFlag = 0
    master = gp.Model(); master.Params.OutputFlag = 0
    try:
        x = source.addVar(lb=-3, ub=-1, name='negative')
        h = source.addVar(vtype=gp.GRB.BINARY)
        source.addConstr(h == 0)
        source.addConstr(x >= 0)
        source.addGenConstrIndicator(h, 0, x >= -2)
        z = master.addVar(vtype=gp.GRB.BINARY)
        context, cost = perspective.append_block(master, source, {'x': x}, z, prefix='off_')
        master.addConstr(z == 0)
        master.setObjective(cost)
        master.optimize()
        assert master.Status == gp.GRB.OPTIMAL
        assert context['x'].X == 0
    finally:
        source.dispose(); master.dispose()
