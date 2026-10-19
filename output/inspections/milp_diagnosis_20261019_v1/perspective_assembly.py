"""Experimental bounded indicator linearization for disjunctive blocks.

This is an isolated formulation experiment, not a production solver change.
For source h=1 => L(x)<=0, L's bound U gives L(x^k)<=max(0,U)*(z-h).
For h=0 the inactive multiplier is h. Equalities use both bound directions.
At z=1 this is the original bounded indicator; at z=0 all terms vanish.
"""
from dataclasses import fields
import math
from models.common.gurobi_disjunction import check_deadline


def append_block(target, source, context, selector, *, prefix, deadline=None):
    import gurobipy as gp
    from gurobipy import GRB
    source.update()
    if source.NumQConstrs or source.NumQNZs or source.NumSOS or source.NumObj != 1:
        raise ValueError('Only bounded linear single-objective blocks are supported')
    if source.ModelSense != GRB.MINIMIZE:
        raise ValueError('Expected minimization')
    old = source.getVars()
    mapped = {}
    for var in old:
        check_deadline(deadline)
        if (var.VType not in (GRB.BINARY, GRB.INTEGER, GRB.CONTINUOUS)
                or not all(math.isfinite(bound) and abs(bound) < GRB.INFINITY for bound in (var.LB, var.UB))
                or source.getPWLObj(var)):
            raise ValueError('Every source variable needs finite bounds and a linear objective')
        new = target.addVar(lb=min(0., var.LB), ub=max(0., var.UB), vtype=var.VType, name=prefix+var.VarName)
        mapped[var.index] = new
        target.addConstr(new >= var.LB*selector, name=prefix+f'lb_{var.index}')
        target.addConstr(new <= var.UB*selector, name=prefix+f'ub_{var.index}')

    def expression(expr, homogeneous=False):
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
            raise ValueError('Only indicators are supported')
        binary, trigger, lhs, sense, rhs = source.getGenConstrIndicator(row)
        lower = upper = lhs.getConstant()-rhs
        for i in range(lhs.size()):
            var, coeff = lhs.getVar(i), lhs.getCoeff(i)
            lower += coeff*(var.LB if coeff >= 0 else var.UB)
            upper += coeff*(var.UB if coeff >= 0 else var.LB)
        if not (math.isfinite(lower) and math.isfinite(upper)):
            raise ValueError('Unbounded indicator expression')
        off = selector-mapped[binary.index] if trigger else mapped[binary.index]
        scaled = expression(lhs, homogeneous=True)-rhs*selector
        if sense in (GRB.LESS_EQUAL, GRB.EQUAL):
            target.addLConstr(scaled-max(0., upper)*off, GRB.LESS_EQUAL, 0.,
                              name=prefix+row.GenConstrName+'_upper')
        if sense in (GRB.GREATER_EQUAL, GRB.EQUAL):
            target.addLConstr(scaled-min(0., lower)*off, GRB.GREATER_EQUAL, 0.,
                              name=prefix+row.GenConstrName+'_lower')

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
