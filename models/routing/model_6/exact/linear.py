"""Small sparse linear-model builder shared by two native MILP backends.

This contains no branching algorithm, custom cuts, or heuristic incumbent.
"""
import math
import time


class Expr:
    def __init__(self, terms=None, constant=0.):
        self.terms, self.constant = dict(terms or {}), float(constant)

    def __add__(self, other):
        other = expr(other)
        terms = self.terms.copy()
        for i, v in other.terms.items():
            terms[i] = terms.get(i, 0.)+v
        return Expr({i:v for i,v in terms.items() if v}, self.constant+other.constant)

    __radd__ = __add__

    def __mul__(self, number):
        return Expr({i:v*number for i,v in self.terms.items()}, self.constant*number)

    __rmul__ = __mul__

    def __neg__(self):
        return self*(-1)

    def __sub__(self, other):
        return self+-expr(other)

    def __rsub__(self, other):
        return expr(other)+-self

    def value(self, x):
        return self.constant+sum(v*x[i] for i,v in self.terms.items())


def expr(value):
    return value if isinstance(value, Expr) else Expr(constant=value)


class LinearModel:
    def __init__(self, deadline=None):
        self.variables, self.rows = [], []
        self.objective = Expr()
        self.deadline = deadline

    def budget(self):
        if self.deadline is not None and time.perf_counter() >= self.deadline:
            raise TimeoutError('MILP construction exhausted the wall-clock budget')

    def var(self, name, lo=0, hi=1, integer=True):
        if len(self.variables) % 256 == 0:
            self.budget()
        i = len(self.variables)
        self.variables.append((name, float(lo), float(hi), integer))
        return Expr({i:1.})

    def add(self, expression, lo=-math.inf, hi=math.inf):
        if len(self.rows) % 1024 == 0:
            self.budget()
        e = expr(expression)
        self.rows.append((e.terms, lo-e.constant, hi-e.constant))

    def eq(self, a, b=0):
        self.add(expr(a)-b, 0, 0)

    def le(self, a, b=0):
        self.add(expr(a)-b, hi=0)

    def ge(self, a, b=0):
        self.add(expr(a)-b, lo=0)

    def conditional_ge(self, switch, a, b, big_m):
        self.ge(a, expr(b)-big_m*(1-switch))

    def conditional_eq(self, switch, a, b, big_m):
        self.conditional_ge(switch, a, b, big_m)
        self.conditional_ge(switch, b, a, big_m)

    def solve(self, backend, time_limit, seed=42, threads=1, verbose=False):
        entered = time.perf_counter()
        def remaining():
            value=time_limit-(time.perf_counter()-entered)
            if value<=0:raise TimeoutError('Backend preparation exhausted the wall-clock budget')
            return value
        if backend == 'highs':
            import numpy as np
            from scipy.optimize import Bounds, LinearConstraint, milp
            from scipy.sparse import coo_matrix
            rr, cc, vv, low, high = [], [], [], [], []
            for r, (terms, lo, hi) in enumerate(self.rows):
                for c, v in terms.items():
                    rr.append(r); cc.append(c); vv.append(v)
                low.append(lo); high.append(hi)
            matrix = coo_matrix((vv, (rr, cc)), shape=(len(self.rows), len(self.variables))).tocsc()
            cost = np.zeros(len(self.variables))
            for i,v in self.objective.terms.items():
                cost[i] = v
            # SciPy forwards these native HiGHS options; suppress only that notice.
            import warnings
            with warnings.catch_warnings():
                warnings.filterwarnings('ignore', message='Unrecognized options detected.*')
                out = milp(cost, integrality=np.array([int(v[3]) for v in self.variables]),
                           bounds=Bounds([v[1] for v in self.variables], [v[2] for v in self.variables]),
                           constraints=LinearConstraint(matrix, low, high),
                           options=dict(time_limit=remaining(), mip_rel_gap=0.,
                                        threads=threads, random_seed=seed, disp=verbose))
            bound = getattr(out, 'mip_dual_bound', None)
            gap = getattr(out, 'mip_gap', None)
            if bound is not None:
                bound += self.objective.constant
                if out.x is not None and self.objective.constant and math.isfinite(bound):
                    value = self.objective.value(out.x)
                    gap = max(0., (value-bound)/max(abs(value), 1e-10))
            return dict(x=out.x, status={0:'OPTIMAL',1:'TIME_LIMIT',2:'INFEASIBLE',3:'UNBOUNDED'}.get(out.status, 'SOLVER_ERROR'),
                        message=out.message, bound=bound, gap=gap, backend='highs')
        if backend != 'gurobi':
            raise ValueError('M6 exact backend must be gurobi or highs')
        import gurobipy as gp
        m = gp.Model('model6_no_batch')
        try:
            m.Params.OutputFlag = int(verbose)
            m.Params.Threads = threads
            m.Params.Seed = seed
            m.Params.MIPGap = 0
            m.Params.MIPGapAbs = 0
            variables = [m.addVar(lb=lo, ub=hi, vtype=gp.GRB.INTEGER if integ else gp.GRB.CONTINUOUS, name=name)
                         for name,lo,hi,integ in self.variables]
            for terms,lo,hi in self.rows:
                e = gp.LinExpr(list(terms.values()), [variables[i] for i in terms])
                if lo == hi:
                    m.addConstr(e == lo)
                else:
                    if math.isfinite(lo): m.addConstr(e >= lo)
                    if math.isfinite(hi): m.addConstr(e <= hi)
            m.setObjective(gp.LinExpr(list(self.objective.terms.values()),
                                     [variables[i] for i in self.objective.terms]) + self.objective.constant,
                           gp.GRB.MINIMIZE)
            m.Params.TimeLimit = remaining()
            m.optimize()
            return dict(x=[v.X for v in variables] if m.SolCount else None,
                        status={gp.GRB.OPTIMAL:'OPTIMAL',gp.GRB.INFEASIBLE:'INFEASIBLE',gp.GRB.TIME_LIMIT:'TIME_LIMIT'}.get(m.Status, 'SOLVER_STOPPED'),
                        bound=m.ObjBound if m.Status != gp.GRB.INFEASIBLE else None,
                        gap=m.MIPGap if m.SolCount else None, backend='gurobi')
        finally:
            m.dispose()
