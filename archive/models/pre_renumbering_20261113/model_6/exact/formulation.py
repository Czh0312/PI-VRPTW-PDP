"""Complete joint order-batch, pool and two-echelon cost MILP.

No fixed-batch distance cuts are used. Each batch has positive delivery and
recovery quantities; individual vehicles may still make delivery-only or
pickup-only trips within that productive batch.
"""
from __future__ import annotations

import time
from models.common.cycle.scope import validate_problem


from models.routing.model_6.exact.parameters import prepare_parameters
from models.routing.model_6.exact.variables import ModelVariables
from models.routing.model_6.exact.constraints import (
    add_calendar_constraints,
    add_order_batch_constraints,
    add_supply_time_constraints,
    add_route_constraints,
    add_first_echelon_constraints,
    add_supply_coverage_constraints,
    add_cutoff_quantity_constraints,
    add_second_echelon_constraints,
    add_task_and_return_coverage_constraints,
    add_inventory_constraints,
    add_vehicle_activation_constraints,
)
from models.routing.model_6.exact.objective import set_cost_objective


def build_model(problem, *, verbose=False, deadline=None, compact_e2=None):
    validate_problem(problem, 6)
    if getattr(problem, "uniform_cycle", False):
        raise ValueError(
            "Uniform cycle has one MILP per batch count; use solve_exact or problem.for_count(K)"
        )
    import gurobipy as gp

    parameters = prepare_parameters(
        problem=problem, compact_e2=compact_e2,
    )
    p = parameters.policy
    f = parameters.fleet
    H = parameters.horizon
    Q = parameters.total_containers
    m = gp.Model(f'model{getattr(problem, "calendar_model_number", 6)}_joint_batch_cost_v1')
    variables = ModelVariables(m)
    m.Params.OutputFlag = int(verbose)
    m.Params.Threads = 1
    m.Params.MIPGap = 0
    m.Params.MIPGapAbs = 0

    def budget():
        if deadline is not None and time.perf_counter() >= deadline:
            raise TimeoutError("M6 model construction reached its total budget")

    try:
        variable_calendar, starts, ends, cutoffs = add_calendar_constraints(parameters, variables)
        variables.calendar["starts"] = starts
        variables.calendar["ends"] = ends
        variables.calendar["cutoffs"] = cutoffs
        y, z = add_order_batch_constraints(
            parameters,
            variables,
            variable_calendar=variable_calendar,
        )
        variables.assignments = y
        variables.activation = z
        counts = {
            (s, b.id): gp.quicksum(
                o.count * y[o.id, b.id]
                for o in problem.orders
                if o.satellite == s and (o.id, b.id) in y
            )
            for s in problem.satellites
            for b in p.batches
        }
        variables.demand_counts = counts
        weights = {
            (s, b.id): gp.quicksum(
                (o.goods_weight + o.count * f.container_weight) * y[o.id, b.id]
                for o in problem.orders
                if o.satellite == s and (o.id, b.id) in y
            )
            for s in problem.satellites
            for b in p.batches
        }
        variables.demand_weights = weights
        N = variables.create_container_pool(problem)
        variables.pool = N
        supplied = add_supply_time_constraints(parameters, variables)
        variables.supply_times = supplied
        task_times = {
            (o.id, k): variables.create_task_time(k, o) for o in problem.orders for k in ("d", "p")
        }
        variables.task_times = task_times
        slot_count = sum(
            min(p.e2_max_trips_per_vehicle, 2 * sum(o.satellite == s for o in problem.orders))
            * f.e2_per_sat_count
            for s in problem.satellites
        )
        causal_limit = 2 * len(problem.orders) + 2 * slot_count + 1
        steps = {key: variables.create_task_step(causal_limit, key) for key in task_times}
        variables.task_steps = steps
        routes = {"e1": [], "e2": []}
        variables.routes = routes

        def make_route(nodes, base, layer, label, batch=None):
            return add_route_constraints(
                parameters,
                variables,
                nodes=nodes,
                base=base,
                layer=layer,
                label=label,
                budget=budget,
                batch=batch,
            )

        add_first_echelon_constraints(
            parameters,
            variables,
            make_route=make_route,
            variable_calendar=variable_calendar,
        )
        add_supply_coverage_constraints(parameters, variables)
        returned, q_expected = add_cutoff_quantity_constraints(parameters, variables)
        variables.pickup_returns = returned
        variables.expected_returns = q_expected
        add_second_echelon_constraints(
            parameters,
            variables,
            causal_limit=causal_limit,
            make_route=make_route,
        )
        add_task_and_return_coverage_constraints(parameters, variables)
        add_inventory_constraints(
            parameters,
            variables,
            budget=budget,
        )
        physical_vehicles = add_vehicle_activation_constraints(parameters, variables)
        variables.physical_vehicles = physical_vehicles
        parts = set_cost_objective(parameters, variables)
        budget()
        m.update()
        return m, dict(
            parameters=parameters,
            variables=variables,
            **routes,
            assignments=y,
            activation=z,
            pool=N,
            cutoffs=cutoffs,
            cost_parts=parts,
            batch_starts=starts,
            batch_ends=ends,
            physical_vehicles=physical_vehicles,
        )
    except BaseException:
        m.dispose()
        raise
