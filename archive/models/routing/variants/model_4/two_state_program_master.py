"""Restricted master over validated M4 satellite-program columns.

This is deliberately a building block, not an exact M4 solver.  Every column
is a complete E2 program at one satellite with a validated ``(h, p)`` witness
from :mod:`models.routing.variants.model_4.certified_time_expansion`.  The master selects such
programs to cover customers exactly once.  It is safe for column-generation
experiments because it exposes only fixed, program-level coefficients; it is
never allowed to label a finite program pool as globally optimal.
"""

from __future__ import annotations

from typing import Iterable, Sequence

import gurobipy as gp
from gurobipy import GRB

from models.routing.variants.model_4.certified_time_expansion import M4SatelliteProgram


def build_m4_two_state_program_master(
    customer_ids: Sequence[str],
    satellite_ids: Sequence[str],
    programs: Iterable[M4SatelliteProgram],
    *,
    relax: bool = False,
):
    """Build the fixed-row E2 program RMP.

    The omitted E1 delivery/pickup routing rows are intentional: a future
    global master will add them and link a selected program's ``h``/``p`` to
    E1 arrival variables.  Keeping this component separate makes it
    impossible to mistake a program-pool integer optimum for a global M4
    certificate.
    """

    customers = tuple(customer_ids)
    satellites = tuple(satellite_ids)
    customer_set = set(customers)
    satellite_set = set(satellites)
    columns = tuple(programs)
    for column in columns:
        if column.sat_id not in satellite_set:
            raise ValueError(f"program uses unknown satellite {column.sat_id!r}")
        if not set(column.customers) <= customer_set:
            raise ValueError(f"program contains unknown customer(s): {column.customers!r}")
        if not column.schedule.feasible:
            raise ValueError("cannot add an infeasible M4 program column")
        if column.pickup_ready_time + 1e-9 < max(column.schedule.route_return_times, default=column.ready_time):
            raise ValueError("program pickup-ready time precedes an E2 return")

    model = gp.Model("M4_TwoState_Program_RMP")
    model.setParam("OutputFlag", 0)
    var_type = GRB.CONTINUOUS if relax else GRB.BINARY
    lam = model.addVars(range(len(columns)), vtype=var_type, lb=0.0, ub=1.0, name="program")
    model.setObjective(gp.quicksum(columns[idx].cost * lam[idx] for idx in range(len(columns))), GRB.MINIMIZE)

    for customer_id in customers:
        model.addConstr(
            gp.quicksum(lam[idx] for idx, column in enumerate(columns) if customer_id in column.customers) == 1,
            f"ProgramCover_{customer_id}",
        )
    for satellite_id in satellites:
        model.addConstr(
            gp.quicksum(lam[idx] for idx, column in enumerate(columns) if column.sat_id == satellite_id) <= 1,
            f"ProgramOnePerSatellite_{satellite_id}",
        )

    return model, {"program": lam}, {"columns": columns, "customers": customers, "satellites": satellites}


def solve_m4_two_state_program_pool(*args, **kwargs):
    """Solve a finite program pool while explicitly withholding certification."""

    model, variables, context = build_m4_two_state_program_master(*args, **kwargs)
    model.optimize()
    status = "OPTIMAL" if model.status == GRB.OPTIMAL else str(model.status)
    selected = [
        context["columns"][idx]
        for idx, value in variables["program"].items()
        if model.SolCount and value.X > 0.5
    ]
    return {
        "status": status,
        "proven_optimal": False,
        "proof_scope": "restricted_m4_two_state_program_pool_only",
        "objective": float(model.ObjVal) if model.SolCount else None,
        "selected_programs": selected,
    }
