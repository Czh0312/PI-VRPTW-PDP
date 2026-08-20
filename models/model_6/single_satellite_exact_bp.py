"""Certified branch-price entry point for the single-satellite M6 subclass.

For the project's 5/10-customer benchmark instances, each instance contains
one satellite.  In that subclass the two M6 state dimensions collapse
exactly:

* E1 has no other delivery event before ``s_d``, so its delivery completion
  can be left-shifted to the direct-depot earliest time ``h`` without harming
  any E2 route or time window.
* E1 pickup has no upper time bound and may wait at ``s_p``.  A finite,
  data-derived horizon ``p_max`` therefore admits every E2 program; E1 waits
  until ``p_max`` if its actual returns are earlier.

At this one ``(h, p_max)`` state, the complete-program pricer and fixed-grid
branch-price tree are a global exact method for this subclass.  The entry
point refuses certification for multi-satellite, zero-delivery-task, or
capacity-ineligible instances rather than silently applying the argument
outside its proof domain.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from typing import Any, Iterable, Mapping

import gurobipy as gp
from gurobipy import GRB

from models.model_6.two_state_fixed_grid_branch_price import (
    solve_m6_two_state_fixed_grid_branch_price,
)
from models.model_6.two_state_global_program_master import (
    build_m6_two_state_global_program_master,
)
from models.model_6.certified_time_expansion import M6SatelliteProgram
from models.model_6.two_state_program_pricing import price_m6_satellite_program
from models.solver_utils import extract_routes_e1


@dataclass(frozen=True)
class M6SingleSatelliteExactResult:
    """Certificate-bearing result for the eligible single-satellite subclass."""

    e1_routes: tuple[tuple[str, ...], ...] | None
    e2_routes_by_sat: dict[str, list[list[str]]] | None
    assignment: dict[str, str] | None
    info: dict[str, Any]
    # A non-certified run can still safely preserve validated columns.  They
    # may be reused as an initial restricted master in a later invocation;
    # they are not themselves a proof until the full B&P tree closes.
    programs: tuple[M6SatelliteProgram, ...] = ()


def solve_single_satellite_exact_branch_price(
    problem,
    vehicles_per_satellite: int,
    *,
    TFp: float = 9999.0,
    seed_time_limit: float = 60.0,
    pricing_time_limit: float = 30.0,
    pricing_gurobi_params: Mapping[str, object] | None = None,
    pricing_solution_pool_size: int = 1,
    pricing_mode: str = "minimize",
    pricing_subtour_strengthening_size: int = 0,
    exclude_existing_pricing_programs: bool = False,
    max_duplicate_reprices: int = 8,
    max_nodes: int = 512,
    max_cg_iterations_per_node: int = 60,
    initial_programs: Iterable[M6SatelliteProgram] | None = None,
) -> M6SingleSatelliteExactResult:
    """Strictly solve an eligible M6 single-satellite instance.

    ``proven_optimal`` is set only after the full branch-price tree over the
    universal state has closed.  Any seed-pricing timeout, node cap, or
    incomplete child pricing returns a non-certified result.
    """

    sat_id, h, p_max = _validate_and_universal_state(problem)
    customers = tuple(customer.id for customer in problem.customers)
    reused = tuple(initial_programs or ())
    if reused:
        _validate_reused_programs(reused, sat_id, h, p_max, customers)
        initial_columns = reused
        seed_status = "REUSED_VALIDATED_PROGRAM_POOL"
        seed_runtime = 0.0
    else:
        seed = price_m6_satellite_program(
            problem,
            sat_id,
            delivery_ready_time=h,
            pickup_ready_time=p_max,
            customer_rc_terms={customer_id: 0.0 for customer_id in customers},
            required_customer_ids=customers,
            vehicles_per_satellite=vehicles_per_satellite,
            time_limit=seed_time_limit,
            gurobi_params=pricing_gurobi_params,
            solution_pool_size=pricing_solution_pool_size,
        )
        if seed.program is None:
            return _uncertified(
                "NO_COMPLETE_E2_SEED",
                sat_id,
                h,
                p_max,
                seed_status=seed.status,
                seed_runtime=seed.runtime,
            )
        initial_columns = (seed.program,)
        seed_status = seed.status
        seed_runtime = seed.runtime

    bcp = solve_m6_two_state_fixed_grid_branch_price(
        problem,
        initial_columns,
        vehicles_per_satellite,
        max_nodes=max_nodes,
        max_cg_iterations_per_node=max_cg_iterations_per_node,
        pricing_time_limit=pricing_time_limit,
        pricing_gurobi_params=pricing_gurobi_params,
        pricing_solution_pool_size=pricing_solution_pool_size,
        pricing_mode=pricing_mode,
        pricing_subtour_strengthening_size=pricing_subtour_strengthening_size,
        exclude_existing_pricing_programs=exclude_existing_pricing_programs,
        max_duplicate_reprices=max_duplicate_reprices,
    )
    if bcp.status != "FIXED_GRID_OPTIMAL" or not bcp.fixed_grid_proven_optimal:
        return _uncertified(
            bcp.status,
            sat_id,
            h,
            p_max,
            seed_status=seed_status,
            seed_runtime=seed_runtime,
            bcp_nodes=bcp.nodes_processed,
            bcp_columns=len(bcp.columns),
            programs=bcp.columns,
        )

    # The B&P result has established that no unrepresented program at the
    # universal state can improve any node.  Re-solve the finite final master
    # only to extract an auditable integer route witness.
    model, variables, context = build_m6_two_state_global_program_master(
        problem,
        bcp.columns,
        vehicles_per_satellite,
        TFp=TFp,
        relax=False,
    )
    model.setParam("OutputFlag", 0)
    model.setParam("MIPGap", 0.0)
    model.setParam("IntFeasTol", 1e-9)
    model.setParam("FeasibilityTol", 1e-9)
    model.optimize()
    if model.status != GRB.OPTIMAL:
        # This should be unreachable after a closed B&P tree, but refusing
        # certification is safer than trusting an inconsistent extraction MIP.
        return _uncertified(
            f"FINAL_MASTER_{_status_name(model.status)}",
            sat_id,
            h,
            p_max,
            seed_status=seed_status,
            seed_runtime=seed_runtime,
            bcp_nodes=bcp.nodes_processed,
            bcp_columns=len(bcp.columns),
            programs=bcp.columns,
        )

    selected_programs = [
        context["columns"][rid]
        for rid, variable in variables["program"].items()
        if variable.X > 0.5
    ]
    if len(selected_programs) != 1:
        return _uncertified(
            "FINAL_MASTER_NONUNIQUE_PROGRAM_SELECTION",
            sat_id,
            h,
            p_max,
            seed_status=seed_status,
            seed_runtime=seed_runtime,
            bcp_nodes=bcp.nodes_processed,
            bcp_columns=len(bcp.columns),
            programs=bcp.columns,
        )
    selected = selected_programs[0]
    depot_id = problem.depot.id
    e1_nodes = [f"{sat_id}d", f"{sat_id}p"]
    sol_x = {key: variable.X for key, variable in variables["x"].items()}
    e1_routes = tuple(
        tuple(route)
        for _, route in extract_routes_e1(sol_x, depot_id, e1_nodes, tuple(range(problem.e1_vehicle_count)))
    )
    e2_routes = {sat_id: [list(route) for route in selected.routes]}
    assignment = {customer_id: sat_id for customer_id in customers}
    info = {
        "status": "OPTIMAL",
        "solution_type": "proven_optimal",
        "proven_optimal": True,
        "proof_scope": "full_single_satellite_branch_price",
        "objective": float(model.ObjVal),
        "best_bound": float(model.ObjVal),
        "mip_gap": 0.0,
        "universal_delivery_ready_time": h,
        "universal_pickup_ready_time": p_max,
        "seed_status": seed_status,
        "seed_runtime": seed_runtime,
        "bcp_nodes": bcp.nodes_processed,
        "bcp_columns": len(bcp.columns),
        "assignment": assignment,
    }
    return M6SingleSatelliteExactResult(
        e1_routes, e2_routes, assignment, info, tuple(bcp.columns)
    )


def _validate_reused_programs(
    programs: tuple[M6SatelliteProgram, ...],
    sat_id: str,
    delivery_ready_time: float,
    pickup_ready_time: float,
    customer_ids: tuple[str, ...],
) -> None:
    """Validate a resumable pool before allowing it into the exact entry point.

    The public proof uses one universal state.  Reusing a column generated at
    another state would silently change that proof domain, so it is rejected
    rather than treated as a harmless warm start.  At least one complete
    program is also required because the master selects one satellite program
    at its one active state.
    """

    if not programs:
        raise ValueError("reused M6 program pool must not be empty")
    expected_customers = set(customer_ids)
    for program in programs:
        if program.sat_id != sat_id:
            raise ValueError("reused M6 program has a different satellite")
        if abs(float(program.ready_time) - delivery_ready_time) > 1e-7:
            raise ValueError("reused M6 program has a different delivery-ready state")
        if abs(float(program.pickup_ready_time) - pickup_ready_time) > 1e-7:
            raise ValueError("reused M6 program has a different pickup-ready state")
        if not set(program.customers) <= expected_customers:
            raise ValueError("reused M6 program contains an unknown customer")
    if not any(set(program.customers) == expected_customers for program in programs):
        raise ValueError("reused M6 program pool needs a complete feasible seed")


def _validate_and_universal_state(problem) -> tuple[str, float, float]:
    satellites = tuple(problem.satellites)
    if len(satellites) != 1:
        raise ValueError("single-satellite exact B&P requires exactly one satellite")
    satellite = satellites[0]
    sat_id = satellite.id
    customers = tuple(problem.customers)
    if not customers:
        raise ValueError("single-satellite exact B&P requires at least one customer")
    if any(float(customer.weight_del) <= 1e-9 for customer in customers):
        raise ValueError("single-satellite exact B&P currently requires a delivery task for every customer")
    cw = float(problem.fleet.container_weight)
    cv = float(problem.fleet.container_volume)
    total_delivery_weight = sum(float(customer.weight_del) + cw for customer in customers)
    total_delivery_volume = len(customers) * cv
    total_pickup_weight = len(customers) * cw
    total_pickup_volume = len(customers) * cv
    if total_delivery_weight > float(problem.e1_weight_cap) + 1e-9:
        raise ValueError("single E1 delivery visit exceeds E1 weight capacity")
    if total_delivery_volume > float(problem.e1_volume_cap) + 1e-9:
        raise ValueError("single E1 delivery visit exceeds E1 volume capacity")
    if total_pickup_weight > float(problem.e1_weight_cap) + 1e-9:
        raise ValueError("single E1 pickup visit exceeds E1 weight capacity")
    if total_pickup_volume > float(problem.e1_volume_cap) + 1e-9:
        raise ValueError("single E1 pickup visit exceeds E1 volume capacity")

    e1_arrival = float(problem.travel_time(problem.depot.id, sat_id))
    h = e1_arrival + float(satellite.service_time)
    for customer in customers:
        delivery_upper = float(customer.tw_del[1])
        if isfinite(delivery_upper) and e1_arrival > delivery_upper + 1e-9:
            raise ValueError("direct E1 delivery already misses a customer delivery deadline")

    locations = (sat_id,) + tuple(customer.id for customer in customers)
    max_arc = max(
        (float(problem.travel_time(i, j)) for i in locations for j in locations if i != j),
        default=0.0,
    )
    max_service = max(float(customer.service_time) for customer in customers)
    # A pickup latest time never forces waiting.  To bound an earliest
    # feasible schedule, only release times and delivery deadlines matter;
    # this is much tighter than treating a conventional 9999 pickup horizon
    # as a required event time while retaining the same proof.
    schedule_base = [h]
    for customer in customers:
        schedule_base.append(float(customer.tw_del[0]))
        schedule_base.append(float(customer.tw_pick[0]))
        if isfinite(float(customer.tw_del[1])):
            schedule_base.append(float(customer.tw_del[1]))
    delivery_upper = [float(customer.tw_del[1]) for customer in customers]
    if all(isfinite(value) for value in delivery_upper):
        # Every delivery service starts no later than U_D and therefore ends
        # by U_D + service_max.  Once B is reached, all delivery and pickup
        # releases are passed, so only pickup tasks can remain.  A route can
        # then contain at most |C| task services/arcs plus its final return.
        # This dominates the old 2|C|-task envelope without excluding any
        # distance-optimal left-shifted schedule.
        post_delivery_release = max(max(delivery_upper) + max_service, max(
            float(customer.tw_pick[0]) for customer in customers
        ), h)
        p_max = (
            post_delivery_release
            + len(customers) * (max_arc + max_service)
            + max_arc
            + 1.0
        )
    else:
        # Retain the conservative all-task envelope when an input admits an
        # unbounded delivery latest time; the stronger delivery-completion
        # argument above then has no finite U_D premise.
        p_max = (
            max(schedule_base)
            + (2 * len(customers) + 2) * max_arc
            + (2 * len(customers) + 1) * max_service
            + 1.0
        )
    return sat_id, h, p_max


def _uncertified(
    status: str,
    sat_id: str,
    h: float,
    p_max: float,
    *,
    programs: Iterable[M6SatelliteProgram] = (),
    **extra,
) -> M6SingleSatelliteExactResult:
    info = {
        "status": status,
        "solution_type": "none",
        "proven_optimal": False,
        "proof_scope": "single_satellite_branch_price_incomplete",
        "objective": None,
        "best_bound": None,
        "mip_gap": None,
        "universal_delivery_ready_time": h,
        "universal_pickup_ready_time": p_max,
    }
    info.update(extra)
    return M6SingleSatelliteExactResult(None, None, None, info, tuple(programs))


def _status_name(status_code: int) -> str:
    return {
        GRB.OPTIMAL: "OPTIMAL",
        GRB.INFEASIBLE: "INFEASIBLE",
        GRB.INF_OR_UNBD: "INF_OR_UNBD",
        GRB.TIME_LIMIT: "TIME_LIMIT",
        GRB.INTERRUPTED: "INTERRUPTED",
        GRB.SUBOPTIMAL: "SUBOPTIMAL",
        GRB.NUMERIC: "NUMERIC",
    }.get(status_code, str(status_code))
