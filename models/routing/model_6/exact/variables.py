"""Decision variable definitions and grouped handles for M6."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ModelVariables:
    """Create decision variables on one model and retain their semantic groups."""

    model: Any
    routes: dict = field(default_factory=lambda: {"e1": [], "e2": []})
    assignments: dict = field(default_factory=dict)
    task_times: dict = field(default_factory=dict)
    pool: Any = None
    calendar: dict = field(default_factory=dict)
    active_sites: dict = field(default_factory=dict)
    demand_counts: dict = field(default_factory=dict)
    demand_weights: dict = field(default_factory=dict)
    supply_times: dict = field(default_factory=dict)
    task_load_weight: dict = field(default_factory=dict)
    task_load_volume: dict = field(default_factory=dict)
    task_steps: dict = field(default_factory=dict)
    shared_arcs: dict = field(default_factory=dict)
    aggregate_arcs: dict = field(default_factory=dict)
    global_arcs: dict = field(default_factory=dict)
    pickup_returns: dict = field(default_factory=dict)
    expected_returns: dict = field(default_factory=dict)
    activation: dict = field(default_factory=dict)
    physical_vehicles: dict = field(default_factory=dict)

    def create_container_pool(self, problem):
        from gurobipy import GRB

        return self.model.addVar(
            vtype=GRB.INTEGER, lb=problem.pool_min, ub=problem.pool_max, name="container_pool"
        )

    def create_task_time(self, k, o):
        from gurobipy import GRB

        return self.model.addVar(
            vtype=GRB.INTEGER,
            lb=(o.delivery_window if k == "d" else o.pickup_window)[0],
            ub=(o.delivery_window if k == "d" else o.pickup_window)[1],
            name=f"time_{o.id}_{k}",
        )

    def create_task_step(self, causal_limit, key):
        from gurobipy import GRB

        return self.model.addVar(ub=causal_limit, name=f"causal_{key[0]}_{key[1]}")

    def create_batch_start(self, b, problem):
        from gurobipy import GRB

        return self.model.addVar(
            vtype=GRB.INTEGER,
            lb=problem.start_bounds[b.id][0],
            ub=problem.start_bounds[b.id][1],
            name=f"batch_start_{b.id}",
        )

    def create_shared_cutoff_offset(self, hi, lo):
        from gurobipy import GRB

        return self.model.addVar(vtype=GRB.INTEGER, lb=lo, ub=hi, name="shared_cutoff_offset")

    def create_batch_cutoff(self, b, problem):
        from gurobipy import GRB

        return self.model.addVar(
            vtype=GRB.INTEGER,
            lb=problem.cutoff_bounds[b.id][0],
            ub=problem.cutoff_bounds[b.id][1],
            name=f"cutoff_{b.id}",
        )

    def create_order_batch_assignment(self, b, o):
        from gurobipy import GRB

        return self.model.addVar(vtype=GRB.BINARY, name=f"assign_{o.id}_{b}")

    def create_batch_activation(self, b):
        from gurobipy import GRB

        return self.model.addVar(vtype=GRB.BINARY, name=f"delivery_active_{b.id}")

    def create_supply_time(self, b, problem, s):
        from gurobipy import GRB

        return self.model.addVar(
            vtype=GRB.INTEGER, ub=problem.cutoff_bounds[b.id][1], name=f"supply_{s}_{b.id}"
        )

    def create_route_used(self, label):
        from gurobipy import GRB

        return self.model.addVar(vtype=GRB.BINARY, name=label + "_used")

    def create_route_start(self, H, label):
        from gurobipy import GRB

        return self.model.addVar(vtype=GRB.INTEGER, ub=H, name=label + "_start")

    def create_route_end(self, H, label):
        from gurobipy import GRB

        return self.model.addVar(vtype=GRB.INTEGER, ub=H, name=label + "_end")

    def create_route_visits(self, label, nodes):
        from gurobipy import GRB

        return self.model.addVars(len(nodes), vtype=GRB.BINARY, name=label + "_visit")

    def create_route_times(self, H, label, nodes):
        from gurobipy import GRB

        return self.model.addVars(len(nodes), vtype=GRB.INTEGER, ub=H, name=label + "_time")

    def create_route_weight(self, cap_w, label, nodes):
        from gurobipy import GRB

        return self.model.addVars(len(nodes), ub=cap_w, name=label + "_weight")

    def create_route_volume(self, cap_v, label, nodes):
        from gurobipy import GRB

        return self.model.addVars(len(nodes), ub=cap_v, name=label + "_volume")

    def create_route_rank(self, label, nodes):
        from gurobipy import GRB

        return self.model.addVars(len(nodes), ub=max(1, len(nodes)), name=label + "_rank")

    def create_route_arcs(self, arcs, label):
        from gurobipy import GRB

        return self.model.addVars(arcs, vtype=GRB.BINARY, name=label + "_arc")

    def create_quantity(self, Q, b, k, kind, s):
        from gurobipy import GRB

        return self.model.addVar(vtype=GRB.INTEGER, ub=Q, name=f"quantity_{b.id}_{k}_{s}_{kind}")

    def create_goods_weight(self, b, f, k, s):
        from gurobipy import GRB

        return self.model.addVar(ub=f.e1_weight_cap, name=f"goods_weight_{b.id}_{k}_{s}")

    def create_hd(self, r):
        from gurobipy import GRB

        return self.model.addVar(vtype=GRB.BINARY, name=r["label"] + "_loaded")

    def create_only_pick(self):
        from gurobipy import GRB

        return self.model.addVar(vtype=GRB.BINARY)

    def create_pickup_return_time(self, o, p, problem):
        from gurobipy import GRB

        return self.model.addVar(
            vtype=GRB.INTEGER,
            ub=problem.cutoff_bounds[p.batches[-1].id][1],
            name=f"returned_{o.id}",
        )

    def create_h(self, b, o):
        from gurobipy import GRB

        return self.model.addVar(vtype=GRB.BINARY, name=f"eligible_{o.id}_{b.id}")

    def create_ss(self, causal_limit):
        from gurobipy import GRB

        return self.model.addVar(ub=causal_limit)

    def create_released(self, Q):
        from gurobipy import GRB

        return self.model.addVar(vtype=GRB.INTEGER, ub=Q)

    def create_active(self, key, layer):
        from gurobipy import GRB

        return self.model.addVar(vtype=GRB.BINARY, name=f"vehicle_{layer}_{key}")
