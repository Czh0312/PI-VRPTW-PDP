"""Reusable exact MILP pricing alternative to the elementary delivery labeler."""
import math
import time

from .single_batch_route_pricing import evaluate_delivery_column


class DeliveryMipPricer:
    def __init__(self, problem, context, *, seed=42, deadline=None):
        import gurobipy as gp
        from gurobipy import GRB
        self.problem, self.context = problem, context
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
            y = self.visits = m.addVars(n, vtype=GRB.BINARY, name='visit')
            times = {i: m.addVar(vtype=GRB.INTEGER, lb=node['window'][0], ub=node['window'][1], name=f't_{i}')
                     for i, node in enumerate(nodes)}
            ranks = m.addVars(n, ub=n, name='rank')
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
                    m.addConstr(times[b] >= release-max(0, release-nodes[b]['window'][0])*(1-active))
                elif b == -2:
                    tail = nodes[a]['order'].service_time+problem.travel(left, right)+p.e2_return_unload_time
                    m.addConstr(times[a]+tail <= context['end_limit']
                        +max(0, nodes[a]['window'][1]+tail-context['end_limit'])*(1-active))
                else:
                    lag = nodes[a]['order'].service_time+problem.travel(left, right)
                    m.addConstr(times[b] >= times[a]+lag
                        -max(0, nodes[a]['window'][1]+lag-nodes[b]['window'][0])*(1-active))
                    m.addConstr(ranks[b] >= ranks[a]+1-(n+1)*(1-active))
            m.update()
        except Exception:
            m.dispose()
            raise

    def price(self, prices, fleet_price=0, *, phase_one=False, deadline=None):
        import gurobipy as gp
        from gurobipy import GRB
        m, context = self.model, self.context
        offset = -min(0.0, fleet_price)
        m.setObjective((0 if phase_one else self.cost)
            -gp.quicksum(prices.get(node['key'], 0)*self.visits[i] for i, node in enumerate(context['nodes']))+offset)
        m.Params.TimeLimit = max(0.0, deadline-time.perf_counter()) if deadline is not None else 60
        m.optimize()
        infeasible = m.Status == GRB.INFEASIBLE
        norm = math.fsum(abs(prices.get(node['key'], 0)) for node in context['nodes'])
        margin = 1e-7*max(1.0, norm, abs(offset))
        analytic = -math.fsum(max(0.0, prices.get(node['key'], 0)) for node in context['nodes'])+offset
        raw_bound = m.ObjBound if m.Status not in (GRB.INFEASIBLE, GRB.INF_OR_UNBD) else -math.inf
        lower = max(analytic, raw_bound-margin) if math.isfinite(raw_bound) else analytic
        minimum, columns = None, []
        if m.SolCount:
            selected = {a: b for (a, b), v in self.arcs.items() if v.X > .5}
            indices, seen, current = [], set(), selected.get(-1)
            while current is not None and current != -2 and current not in seen:
                indices.append(current)
                seen.add(current)
                current = selected.get(current)
            if current != -2 or len(indices) != sum(v.X > .5 for v in self.visits.values()):
                raise RuntimeError('Pricing MILP did not produce one elementary trip')
            column = evaluate_delivery_column(self.problem, context, [context['nodes'][i]['key'] for i in indices])
            if column is None:
                raise RuntimeError('Pricing MILP route failed independent replay')
            minimum = (0 if phase_one else column['cost'])-math.fsum(prices[k] for k in column['tasks'])+offset
            if not math.isclose(minimum, m.ObjVal, rel_tol=1e-7, abs_tol=max(1e-5, margin)):
                raise RuntimeError(f'Pricing MILP reduced cost differs from raw replay: '
                    f'replay={minimum}, model={m.ObjVal}, norm={norm}, '
                    f'visits={[(i, v.X) for i, v in self.visits.items() if v.X > 1e-8]}, '
                    f'arcs={[(edge, v.X) for edge, v in self.arcs.items() if v.X > 1e-8]}')
            if minimum < -1e-7:
                columns.append(column)
        return dict(complete=m.Status in (GRB.OPTIMAL, GRB.INFEASIBLE),
            infeasible=infeasible, status=str(m.Status), minimum_reduced_cost=minimum,
            lower_bound=0.0 if infeasible else lower, margin=margin, columns=columns,
            generated_labels=0, expanded_labels=0, runtime=m.Runtime,
            work=m.Work, variables=m.NumVars, constraints=m.NumConstrs)

    def dispose(self):
        self.model.dispose()
