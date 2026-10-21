"""Linear disjunctive assembly, without solving or searching any block.

For a selected block z=1 we retain its original bounded MILP. For z=0 all
its variables and row right-hand sides are zero. The one-hot selectors make
the resulting single MILP exactly the union of the original feasible sets.
"""
from dataclasses import fields
import math
import time


def check_deadline(deadline):
    if deadline is not None and time.perf_counter() >= deadline:
        raise TimeoutError('Unified MILP construction reached its total budget')


def append_block(target, source, context, selector, *, prefix, deadline=None):
    """Copy bounded linear/indicator constraints and remap extraction handles.

    Constants in the objective and row RHS are multiplied by the selector.
    Indicators with either trigger value remain valid: in an inactive block
    their homogeneous consequent is 0 <=, >=, or == 0. Unsupported constructs
    fail explicitly instead of silently dropping part of the original model.
    """
    import gurobipy as gp
    from gurobipy import GRB

    source.update()
    if source.NumQConstrs or source.NumQNZs or source.NumSOS or source.NumObj != 1:
        raise ValueError('Disjunctive assembly requires a linear single-objective MILP')
    if source.ModelSense != GRB.MINIMIZE:
        raise ValueError('Expected a minimization model')
    old = source.getVars()
    mapped = {}
    for v in old:
        check_deadline(deadline)
        if (v.VType not in (GRB.BINARY, GRB.INTEGER, GRB.CONTINUOUS)
                or not all(math.isfinite(b) and abs(b) < GRB.INFINITY for b in (v.LB, v.UB))
                or source.getPWLObj(v)):
            raise ValueError('Each block variable needs finite bounds and a linear objective')
        new = target.addVar(lb=min(0., v.LB), ub=max(0., v.UB),
                            vtype=v.VType, name=prefix+v.VarName)
        mapped[v.index] = new
        target.addConstr(new >= v.LB*selector, name=prefix+f'lb_{v.index}')
        target.addConstr(new <= v.UB*selector, name=prefix+f'ub_{v.index}')

    def expression(expr, *, homogeneous=False):
        value = gp.LinExpr([expr.getCoeff(i) for i in range(expr.size())],
                           [mapped[expr.getVar(i).index] for i in range(expr.size())])
        value += expr.getConstant()*selector if homogeneous else expr.getConstant()
        return value

    for row in source.getConstrs():
        check_deadline(deadline)
        target.addLConstr(expression(source.getRow(row))-row.RHS*selector,
                          row.Sense, 0., name=prefix+row.ConstrName)
    for row in source.getGenConstrs():
        check_deadline(deadline)
        if row.GenConstrType != GRB.GENCONSTR_INDICATOR:
            raise ValueError('Only linear indicator constraints are supported in a block')
        binary, trigger, lhs, sense, rhs = source.getGenConstrIndicator(row)
        target.addGenConstrIndicator(mapped[binary.index], trigger,
                                    expression(lhs, homogeneous=True)-rhs*selector,
                                    sense, 0., name=prefix+row.GenConstrName)

    # Preserve shared dictionaries/lists in the context, while replacing only
    # Gurobi handles. Immutable business data and parameters stay unchanged.
    memo = {}
    old_variables = context.get('variables')

    def remap(value):
        if isinstance(value, gp.Var):
            return mapped[value.index]
        if isinstance(value, gp.LinExpr):
            return expression(value)
        if id(value) in memo:
            return memo[id(value)]
        if old_variables is not None and value is old_variables:
            result = type(value)(target)
            memo[id(value)] = result
            for field in fields(value):
                if field.name != 'model':
                    setattr(result, field.name, remap(getattr(value, field.name)))
            return result
        if isinstance(value, dict):
            result = {}
            memo[id(value)] = result
            result.update((key, remap(item)) for key, item in value.items())
            return result
        if isinstance(value, (list, tuple)):
            result = [remap(item) for item in value]
            result = tuple(result) if isinstance(value, tuple) else result
            memo[id(value)] = result
            return result
        return value

    return remap(context), expression(source.getObjective(), homogeneous=True)
