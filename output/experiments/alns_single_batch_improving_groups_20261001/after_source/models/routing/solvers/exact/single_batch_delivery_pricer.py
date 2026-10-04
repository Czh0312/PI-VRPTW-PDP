"""Reusable exact MILP pricing alternative to the elementary delivery labeler."""
import math
import time

from .single_batch_route_pricing import evaluate_delivery_column
from .single_batch_time_budget import arc_work, node_work, reduced_route_cost, validate_work_prices


class DeliveryMipPricer:
    def __init__(self, problem, context, *, seed=42, deadline=None, continuous_time=False):
        if continuous_time and (problem.model_number not in (1, 2) or problem.reusable or len(problem.operating_policy.batches) != 1):
            raise ValueError('Continuous delivery pricing supports only single-batch M1/M2')
        import gurobipy as gp
        from gurobipy import GRB
        self.problem, self.context = problem, context
        self.work_expressions = {}
        m = self.model = gp.Model('B1_elementary_delivery_pricing')
        m.Params.OutputFlag = 0
        m.Params.Threads = 1
        m.Params.Seed = int(seed)
        m.Params.MIPGap = m.Params.MIPGapAbs = 0
        m.Params.IntFeasTol = 1e-9
        m.Params.DualReductions = 0
        nodes, f, p, site = context['nodes'], problem.fleet, problem.operating_policy, context['satellite']
        n = len(nodes)
        try:
            def tick(value):
                if continuous_time and (not math.isfinite(value) or value != int(value)):
                    raise ValueError('Continuous pricing requires integer tick bounds and time lags')
            # With integral route bits, every clock row is a difference bound
            # with integral RHS. Ceiling ALL clocks preserves these rows, so
            # removing clock integrality retains the same elementary routes.
            # The objective depends only on route bits; returned columns still
            # undergo raw integer replay, never rounded solver-clock extraction.
            for value in (context['release'], context['end_limit']):
                tick(value)
            for node in nodes:
                for value in (*node['window'], node['order'].service_time):
                    tick(value)
            y = self.visits = m.addVars(n, vtype=GRB.BINARY, name='visit')
            times = self.times = {i: m.addVar(vtype=GRB.CONTINUOUS if continuous_time else GRB.INTEGER,
                     lb=node['window'][0], ub=node['window'][1], name=f't_{i}')
                     for i, node in enumerate(nodes)}
            self.continuous_time = continuous_time
            ranks = m.addVars(n, ub=n, name='rank')
            self.schedule_variables = tuple(times.values())+tuple(ranks.values())
            for i in range(n):
                m.addConstr(ranks[i] >= y[i])
                m.addConstr(ranks[i] <= n*y[i])
            for resource, cap in (('weight', f.e2_weight_cap), ('volume', f.e2_volume_cap)):
                m.addConstr(gp.quicksum(node[resource]*y[i] for i, node in enumerate(nodes)) <= cap+1e-6)
            edges = []
            for i, a in enumerate(nodes):
                if deadline is not None and time.perf_counter() >= deadline:
                    raise TimeoutError('Delivery pricing build reached its budget')
                customer = a['order'].customer
                if context['release']+problem.travel(site, customer) <= a['window'][1]:
                    edges.append((-1, i))
                if a['window'][0]+a['order'].service_time+problem.travel(customer, site)+p.e2_return_unload_time <= context['end_limit']:
                    edges.append((i, -2))
                for j, b in enumerate(nodes):
                    if i != j and a['window'][0]+a['order'].service_time+problem.travel(customer, b['order'].customer) <= b['window'][1]:
                        edges.append((i, j))
            x = self.arcs = m.addVars(edges, vtype=GRB.BINARY, name='arc')
            incoming, outgoing = {i: [] for i in range(n)}, {i: [] for i in range(n)}
            for (a, b), variable in x.items():
                if a >= 0:
                    outgoing[a].append(variable)
                if b >= 0:
                    incoming[b].append(variable)
            m.addConstr(gp.quicksum(v for (a, b), v in x.items() if a == -1) == 1)
            m.addConstr(gp.quicksum(v for (a, b), v in x.items() if b == -2) == 1)
            for i in range(n):
                m.addConstr(gp.quicksum(incoming[i]) == y[i])
                m.addConstr(gp.quicksum(outgoing[i]) == y[i])
            self.cost = gp.LinExpr()
            for (a, b), active in x.items():
                left = site if a == -1 else nodes[a]['order'].customer
                right = site if b == -2 else nodes[b]['order'].customer
                self.cost += problem.distance(left, right)*active
                if a == -1:
                    release = context['release']+problem.travel(left, right)
                    tick(release)
                    m.addConstr(times[b] >= release-max(0, release-nodes[b]['window'][0])*(1-active))
                elif b == -2:
                    tail = nodes[a]['order'].service_time+problem.travel(left, right)+p.e2_return_unload_time
                    tick(tail)
                    m.addConstr(times[a]+tail <= context['end_limit']
                        +max(0, nodes[a]['window'][1]+tail-context['end_limit'])*(1-active))
                else:
                    lag = nodes[a]['order'].service_time+problem.travel(left, right)
                    tick(lag)
                    m.addConstr(times[b] >= times[a]+lag
                        -max(0, nodes[a]['window'][1]+lag-nodes[b]['window'][0])*(1-active))
                    m.addConstr(ranks[b] >= ranks[a]+1-(n+1)*(1-active))
            m.update()
        except Exception:
            m.dispose()
            raise

    def price(self, prices, fleet_price=0, *, phase_one=False, deadline=None, work_prices=(),
              column_limit=1, stop_after_negative=False):
        import gurobipy as gp
        from gurobipy import GRB
        m, context = self.model, self.context
        if isinstance(column_limit, bool) or not isinstance(column_limit, int) or column_limit < 1:
            raise ValueError('Pricing column limit must be a positive integer')
        work_prices = validate_work_prices(work_prices)
        problem, nodes, site = self.problem, context['nodes'], context['satellite']
        for b, penalty in work_prices:
            if b in self.work_expressions:
                continue
            expression = gp.quicksum(node_work(problem, n['order'], b)*self.visits[i] for i, n in enumerate(nodes))
            expression += gp.quicksum(arc_work(problem, site,
                None if a == -1 else nodes[a]['order'], None if j == -2 else nodes[j]['order'], b)*v
                for (a, j), v in self.arcs.items())
            self.work_expressions[b] = expression
        offset = -min(0.0, fleet_price)
        m.setObjective((0 if phase_one else self.cost)
            +gp.quicksum(v*self.work_expressions[b] for b, v in work_prices)
            -gp.quicksum(prices.get(node['key'], 0)*self.visits[i] for i, node in enumerate(nodes))+offset)
        norm = math.fsum(abs(prices.get(node['key'], 0)) for node in context['nodes'])
        time_norm = math.fsum(v*max(1, problem.operating_policy.cycle_end-problem.operating_policy.cycle_start) for b, v in work_prices)
        margin = 1e-7*max(1.0, norm, time_norm, abs(offset))
        # A negative incumbent is sufficient to discover a column. Its bound
        # remains conservative, but early termination cannot close pricing.
        m.Params.BestObjStop = -max(1e-5, 10*margin) if stop_after_negative else -GRB.INFINITY
        m.Params.PoolSolutions = max(10, column_limit)
        # Different waiting/rank values do not define different route columns.
        # Ignore them only for pool diversity; retain every model constraint.
        for variable in self.schedule_variables:
            variable.PoolIgnore = int(column_limit > 1)
        m.Params.TimeLimit = max(0.0, deadline-time.perf_counter()) if deadline is not None else 60
        m.optimize()
        infeasible = m.Status == GRB.INFEASIBLE
        analytic = -math.fsum(max(0.0, prices.get(n['key'], 0)-math.fsum(v*node_work(problem, n['order'], b)
            for b, v in work_prices)) for n in nodes)+offset
        raw_bound = m.ObjBound if m.Status not in (GRB.INFEASIBLE, GRB.INF_OR_UNBD) else -math.inf
        lower = max(analytic, raw_bound-margin) if math.isfinite(raw_bound) else analytic
        minimum, columns, known = None, [], set()
        inspected = min(column_limit, m.SolCount)
        try:
            for number in range(inspected):
                m.Params.SolutionNumber = number
                selected = {a: b for (a, b), v in self.arcs.items() if v.Xn > .5}
                indices, seen, current = [], set(), selected.get(-1)
                while current is not None and current != -2 and current not in seen:
                    indices.append(current)
                    seen.add(current)
                    current = selected.get(current)
                if current != -2 or len(indices) != sum(v.Xn > .5 for v in self.visits.values()):
                    raise RuntimeError('Pricing MILP did not produce one elementary trip')
                column = evaluate_delivery_column(self.problem, context, [context['nodes'][i]['key'] for i in indices])
                if column is None:
                    raise RuntimeError('Pricing MILP route failed independent replay')
                reduced = reduced_route_cost(problem, site, column, prices, fleet_price,
                    phase_one=phase_one, work_prices=work_prices)
                if not math.isclose(reduced, m.PoolObjVal, rel_tol=1e-7, abs_tol=max(1e-5, margin)):
                    raise RuntimeError(f'Pricing MILP reduced cost differs from raw replay: '
                                       f'replay={reduced}, model={m.PoolObjVal}, solution={number}, norm={norm}')
                if number == 0:
                    minimum = reduced
                if reduced < -1e-7 and column['tasks'] not in known:
                    known.add(column['tasks'])
                    columns.append(column)
        finally:
            m.Params.SolutionNumber = 0
        return dict(complete=m.Status in (GRB.OPTIMAL, GRB.INFEASIBLE),
            infeasible=infeasible, status=str(m.Status), minimum_reduced_cost=minimum,
            lower_bound=0.0 if infeasible else lower, margin=margin, columns=columns,
            generated_labels=0, expanded_labels=0, runtime=m.Runtime,
            work=m.Work, variables=m.NumVars, constraints=m.NumConstrs,
            time_domain='continuous' if self.continuous_time else 'integer',
            integer_time_variables=0 if self.continuous_time else len(self.times),
            pool_solutions=m.SolCount, pool_inspected=inspected,
            negative_columns=len(columns), stopped_on_negative=m.Status == GRB.USER_OBJ_LIMIT)

    def dispose(self):
        self.model.dispose()
