"""Compact INTEGER E1 master for delivery-pool incumbent search only.

Each active site belongs to exactly one selected E1 tour. At integer solutions
all its customer cargo therefore belongs to that tour; per-customer allocation
is redundant. Aggregate warehouse loads are allocated to unordered site sets;
all time-distinct tours with the same sites share capacity rows. Homogeneous
weight/volume constraints retain disaggregation without conditional big-M rows.
This formulation is not a replacement for the column-generation LP, and none
of its restricted-pool bounds may be exported to the complete solver.
"""
from __future__ import annotations

from collections import defaultdict
import math
import time

from .single_batch_columns import DeliveryMaster


class DeliveryPoolMaster(DeliveryMaster):
    """Reuse route-column rows, without the LP's disaggregated cargo variables."""

    def __init__(self, problem, domains, contexts, tours, *, deadline=None):
        import gurobipy as gp
        from gurobipy import GRB
        self.problem, self.contexts = problem, contexts
        self.model = m = gp.Model('B1_compact_delivery_primal_pool')
        m.Params.OutputFlag = 0
        m.Params.Threads = 1
        self.variables, self.columns, self.known = [], [], set()
        self.time_rows = {s: {} for s in contexts}
        self.phase_one = False
        self.artificial = {}
        try:
            def check():
                if deadline is not None and time.perf_counter() >= deadline:
                    raise TimeoutError('Compact primal master construction reached its budget')
            groups = defaultdict(list)
            for order in problem.orders:
                groups[order.customer].append(order)
            f = problem.fleet
            weights = {c: math.fsum(o.goods_weight+o.count*f.container_weight for o in orders)
                       for c, orders in groups.items()}
            volumes = {c: sum(o.count for o in orders)*f.container_volume for c, orders in groups.items()}
            self.assignment = z = {(c, s): m.addVar(vtype=GRB.BINARY, name=f'z_{c}_{s}')
                                  for c, choices in domains.items() for s in choices}
            self.active = active = {s: m.addVar(vtype=GRB.BINARY, name=f'active_{s}') for s in contexts}
            self.alpha = alpha = [m.addVar(vtype=GRB.BINARY, obj=t['cost'], name=f'e1_tour_{i}')
                                  for i, t in enumerate(tours)]
            m.addConstr(gp.quicksum(alpha) <= f.e1_count, name='e1_fleet')
            permitted, visits, sets = defaultdict(list), defaultdict(list), defaultdict(list)
            for i, tour in enumerate(tours):
                check()
                sets[tuple(sorted(tour['sites']))].append(alpha[i])
                for site in tour['sites']:
                    visits[site].append(alpha[i])
                for key in tour['permitted']:
                    permitted[key].append(alpha[i])
            for customer, choices in domains.items():
                check()
                m.addConstr(gp.quicksum(z[customer, s] for s in choices) == 1)
                for site in choices:
                    m.addConstr(z[customer, site] <= active[site])
                    # With unique site supply, this is exactly cargo eligibility.
                    m.addConstr(z[customer, site] <= gp.quicksum(permitted[customer, site]))
            self.loads = loads = {}
            for kind, values, capacity in (('weight', weights, f.e1_weight_cap+1e-6),
                                            ('volume', volumes, f.e1_volume_cap+1e-6)):
                allocation, limits = defaultdict(list), {}
                for site in contexts:
                    check()
                    eligible = [c for c, s in z if s == site]
                    upper = min(capacity, math.fsum(values[c] for c in eligible))
                    limits[site] = upper
                    m.addConstr(gp.quicksum(values[c]*z[c, site] for c in eligible) <= upper*active[site])
                for sites, selection in sets.items():
                    check()
                    chosen = gp.quicksum(selection)
                    group = []
                    for site in sites:
                        loads[kind, sites, site] = load = m.addVar(ub=limits[site])
                        allocation[site].append(load)
                        group.append(load)
                        m.addConstr(load <= limits[site]*chosen)
                    m.addConstr(gp.quicksum(group) <= capacity*chosen)
                for site in contexts:
                    check()
                    m.addConstr(gp.quicksum(allocation[site])
                                == gp.quicksum(values[c]*variable for (c, s), variable in z.items() if s == site))
            for site in contexts:
                check()
                m.addConstr(gp.quicksum(visits[site]) == active[site])
                m.addConstr(active[site] <= gp.quicksum(v for (c, s), v in z.items() if s == site))
            self.coverage, self.fleet = {}, {}
            for site, context in contexts.items():
                check()
                self.fleet[site] = m.addConstr(-context['maximum_trips']*active[site] <= 0)
                for node in context['nodes']:
                    self.coverage[site, node['key']] = m.addConstr(-z[node['order'].customer, site] == 0)
            self.e1_costs = list(zip(alpha, (t['cost'] for t in tours)))
            self.capacity_site_sets = len(sets)
            m.update()
        except Exception:
            m.dispose()
            raise
