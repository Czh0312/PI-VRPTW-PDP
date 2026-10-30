"""Decision variable definitions and grouped handles for M3."""
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

    def create_task_time(self, H, k, o):
        from gurobipy import GRB

        return self.model.addVar(vtype=GRB.INTEGER, ub=H, name=f"{o.id}_{k}")

    def create_task_load_w(self, f):
        from gurobipy import GRB

        return self.model.addVar(ub=f.e2_weight_cap)

    def create_task_load_v(self, f):
        from gurobipy import GRB

        return self.model.addVar(ub=f.e2_volume_cap)

    def create_task_step(self, hi, lo):
        from gurobipy import GRB

        return self.model.addVar(lb=lo, ub=hi)

    def create_customer_assignment(self, c, s):
        from gurobipy import GRB

        return self.model.addVar(vtype=GRB.BINARY, name=f"assign_{c}_{s}")

    def create_satellite_activation(self, s):
        from gurobipy import GRB

        return self.model.addVar(vtype=GRB.BINARY, name=f"active_{s}")

    def create_supply_time(self, H, b, s):
        from gurobipy import GRB

        return self.model.addVar(vtype=GRB.INTEGER, ub=H, name=f"delivery_{s}_{b.id}")

    def create_route_used(self, label):
        from gurobipy import GRB

        return self.model.addVar(vtype=GRB.BINARY, name=label + "_used")

    def create_route_start(self, H, label):
        from gurobipy import GRB

        return self.model.addVar(vtype=GRB.INTEGER, ub=H, name=label + "_start")

    def create_route_end(self, H, label):
        from gurobipy import GRB

        return self.model.addVar(vtype=GRB.INTEGER, ub=H, name=label + "_end")

    def create_route_visits(self, count, label):
        from gurobipy import GRB

        return self.model.addVars(count, vtype=GRB.BINARY, name=label + "_visit")

    def create_route_times(self, H, count, label):
        from gurobipy import GRB

        return self.model.addVars(count, vtype=GRB.INTEGER, ub=H, name=label + "_time")

    def create_route_rank(self, count, label):
        from gurobipy import GRB

        return self.model.addVars(count, lb=0, ub=max(1, count), name=label + "_rank")

    def create_load_w(self, cap_w, count, label):
        from gurobipy import GRB

        return self.model.addVars(count, ub=cap_w, name=label + "_weight")

    def create_load_v(self, cap_v, count, label):
        from gurobipy import GRB

        return self.model.addVars(count, ub=cap_v, name=label + "_volume")

    def create_global_arcs(self, global_arcs):
        from gurobipy import GRB

        return self.model.addVar(vtype=GRB.BINARY, name=f"e2_link_{len(global_arcs)}")

    def create_route_arcs(self, a, b, label):
        from gurobipy import GRB

        return self.model.addVar(vtype=GRB.BINARY, name=f"{label}_arc[{a},{b}]")

    def create_route_arcs_2(self, arcs, label):
        from gurobipy import GRB

        return self.model.addVars(arcs, vtype=GRB.BINARY, name=label + "_arc")

    def create_departure_w(self, cap_w, label):
        from gurobipy import GRB

        return self.model.addVar(ub=cap_w, name=label + "_initial_w")

    def create_departure_v(self, cap_v, label):
        from gurobipy import GRB

        return self.model.addVar(ub=cap_v, name=label + "_initial_v")

    def create_quantity(self, s, tightened):
        from gurobipy import GRB

        return self.model.addVar(vtype=GRB.INTEGER, ub=tightened["quantities"][s])

    def create_goods_weight(self, f):
        from gurobipy import GRB

        return self.model.addVar(ub=f.e1_weight_cap)

    def create_quantity_2(self, b, k, s, tightened):
        from gurobipy import GRB

        return self.model.addVar(
            vtype=GRB.INTEGER, ub=tightened["quantities"][s], name=f"q_{b.id}_{k}_{s}"
        )

    def create_r(self):
        from gurobipy import GRB

        return self.model.addVar(vtype=GRB.BINARY)

    def create_r_2(self, r):
        from gurobipy import GRB

        return self.model.addVar(lb=r["step_start_bounds"][0], ub=r["step_start_bounds"][1])

    def create_r_3(self, r):
        from gurobipy import GRB

        return self.model.addVar(lb=r["step_end_bounds"][0], ub=r["step_end_bounds"][1])

    def create_identities(self, key, routes2):
        from gurobipy import GRB

        return self.model.addVar(lb=1, ub=len(routes2), name=f"e2_route_{key}")
