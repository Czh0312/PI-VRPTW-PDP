"""Joint vehicle-indexed MILP for the declared integer-time, finite-slot model."""
from __future__ import annotations

import time

# The route-identity LP can be weaker on small models. Use the same structural
# crossover as the single-batch implementation, rather than a customer limit.
M5_COMPRESS_ROUTE_MIN_INTERNAL_ARCS = 10000


from models.routing.model_5.exact.parameters import multibatch_prepare_parameters
from models.routing.model_5.exact.variables import ModelVariables
from models.routing.model_5.exact.constraints import (
    multibatch_add_availability_constraints,
    multibatch_add_assignment_constraints,
    multibatch_add_supply_time_constraints,
    multibatch_add_route_constraints,
    multibatch_add_first_echelon_constraints,
    multibatch_add_supply_coverage_constraints,
    multibatch_add_cutoff_quantity_constraints,
    multibatch_add_second_echelon_constraints,
    multibatch_add_shared_route_constraints,
    multibatch_add_task_and_return_coverage_constraints,
    multibatch_add_inventory_constraints,
)
from models.routing.model_5.exact.objective import set_distance_objective


def build_model(
    problem,
    *,
    verbose=False,
    deadline=None,
    share_e2=True,
    travel_bounds=None,
    domain_check=None,
    share_cutoff=True,
    linear_e2_links=True,
    trip_symmetry=True,
    compress_multibatch_routes=None,
    factor_e2_loads=False,
):
    from models.common.cycle.scope import validate_problem
    validate_problem(problem, 5)
    import gurobipy as gp

    parameters = multibatch_prepare_parameters(
        domain_check=domain_check,
        linear_e2_links=linear_e2_links,
        problem=problem,
        share_cutoff=share_cutoff,
        share_e2=share_e2,
        travel_bounds=travel_bounds,
    )
    p = parameters.policy
    f = parameters.fleet
    single = parameters.single_batch
    free = parameters.free_assignment
    compact = parameters.share_e2
    compact_cutoff = parameters.share_cutoff
    linear_links = parameters.linear_e2_links
    windows = parameters.task_windows
    site_windows = parameters.site_windows
    domains = parameters.assignment_domains
    travel_bounds = parameters.travel_bounds
    domain_check = parameters.domain_check
    outward = parameters.depot_outward
    H = parameters.horizon
    total = parameters.total_containers
    m = gp.Model("multi_batch_cutoff_v1")
    variables = ModelVariables(m)
    if getattr(problem, 'optimize_pool', False):
        from gurobipy import GRB
        variables.pool = m.addVar(vtype=GRB.INTEGER, lb=problem.pool_min,
                                  ub=problem.pool_max, name='container_pool')
    m.Params.OutputFlag = int(verbose)
    m.Params.MIPGap = 0
    m.Params.MIPGapAbs = 0
    m.Params.Threads = 1
    # Also enforce availability for direct build_model callers that bypass the
    # public solve entry's necessary-condition rejection.
    multibatch_add_availability_constraints(parameters, variables)
    routes1, routes2, objective = [], [], gp.LinExpr()
    variables.routes["e1"] = routes1
    variables.routes["e2"] = routes2
    component_costs = {}
    assignments, active_sites, counts, weights = multibatch_add_assignment_constraints(
        parameters, variables
    )
    variables.assignments = assignments
    variables.active_sites = active_sites
    variables.demand_counts = counts
    variables.demand_weights = weights
    deliveries = multibatch_add_supply_time_constraints(parameters, variables)
    variables.supply_times = deliveries
    order_times = {
        (o.id, k): variables.multibatch_create_task_time(H, k, o)
        for o in problem.orders
        for k in problem.task_kinds
    }
    variables.task_times = order_times
    # Each task is visited exactly once across all vehicles and trip slots.
    # Its time and post-service load therefore have one well-defined value.
    # Keep route-indexed boundary arcs, but share internal propagation once.
    task_load_w = (
        {key: variables.multibatch_create_task_load_w(f) for key in order_times} if compact else {}
    )
    variables.task_load_weight = task_load_w
    task_load_v = (
        {key: variables.multibatch_create_task_load_v(f) for key in order_times} if compact else {}
    )
    variables.task_load_volume = task_load_v
    shared_arcs, aggregate_arcs, global_arcs, e2_arc_cache = {}, {}, {}, {}
    variables.shared_arcs = shared_arcs
    variables.aggregate_arcs = aggregate_arcs
    variables.global_arcs = global_arcs
    indexed_internal = 0
    causal_limit = max(
        10,
        4 * len(problem.orders)
        + 4 * len(problem.satellites) * f.e2_per_sat_count * p.e2_max_trips_per_vehicle,
    )
    if linear_links:
        # At most one trip per task. A satellite's causal DAG has its task
        # nodes plus two endpoints per active trip, hence at most 3*T nodes.
        causal_limit = max(1, 3 * len(order_times) - 1)
    order_steps = {
        (o.id, k): variables.multibatch_create_task_step(causal_limit)
        for o in problem.orders
        for k in problem.task_kinds
    }
    variables.task_steps = order_steps

    def check_budget():
        if deadline is not None and time.perf_counter() >= deadline:
            m.dispose()
            raise TimeoutError("MILP construction reached the total time budget")

    def route_arcs(nodes, base, cap_w, cap_v, unload, layer, batch):
        if layer == "e2" and base in e2_arc_cache:
            return e2_arc_cache[base]
        count = len(nodes)
        # -1 = source, -2 = sink. There is no empty source-to-sink route.
        end_limit = (
            min(H - p.e1_turnaround_time, batch.end) if layer == "e1" else H - p.e2_turnaround_time
        )
        arcs = [
            (-1, j)
            for j in range(count)
            if p.cycle_start + problem.travel(base, nodes[j]["site"]) <= nodes[j]["window"][1]
        ]
        arcs += [
            (i, -2)
            for i in range(count)
            if nodes[i]["window"][0]
            + nodes[i]["service"]
            + problem.travel(nodes[i]["site"], base)
            + unload
            <= end_limit
        ]
        for i, left in enumerate(nodes):
            check_budget()
            for j, right in enumerate(nodes):
                if i == j or (layer == "e1" and left["kind"] == "p" and right["kind"] == "d"):
                    continue
                if (
                    layer == "e2"
                    and left["kind"] == "p"
                    and right["kind"] == "d"
                    and left["order"].id == right["order"].id
                ):
                    continue
                if (
                    left["window"][0]
                    + left["service"]
                    + problem.travel(left["site"], right["site"])
                    > right["window"][1]
                ):
                    continue
                arcs.append((i, j))
        if layer == "e2":
            e2_arc_cache[base] = arcs
        return arcs

    e2_nodes, internal_estimate = {}, 0
    for site in problem.satellites:
        check_budget()
        nodes = []
        for order in problem.orders:
            if order.satellite != site:
                continue
            for kind in problem.task_kinds:
                _, weight, volume = problem.task(order.id, kind)
                nodes.append(
                    dict(
                        site=order.customer,
                        order=order,
                        kind=kind,
                        quantity=order.count,
                        w=weight,
                        v=volume,
                        service=order.service_time,
                        window=windows[order.id, kind],
                    )
                )
        e2_nodes[site] = nodes
        if not nodes:
            continue
        arcs = route_arcs(
            nodes, site, f.e2_weight_cap, f.e2_volume_cap, p.e2_return_unload_time, "e2", None
        )
        slots = sum(
            min(p.e2_max_trips_per_vehicle, len(nodes) // (v + 1) if trip_symmetry else len(nodes))
            for v in range(min(f.e2_per_sat_count, len(nodes)))
        )
        internal_estimate += sum(a >= 0 and b >= 0 for a, b in arcs) * slots
    compressed_routes = compact and (
        internal_estimate >= M5_COMPRESS_ROUTE_MIN_INTERNAL_ARCS
        if compress_multibatch_routes is None
        else compress_multibatch_routes
    )

    def route(nodes, base, cap_w, cap_v, unload, label, layer, batch=None):
        nonlocal objective, indexed_internal
        result, objective, indexed_internal = multibatch_add_route_constraints(
            parameters,
            variables,
            nodes=nodes,
            base=base,
            cap_w=cap_w,
            cap_v=cap_v,
            unload=unload,
            label=label,
            layer=layer,
            check_budget=check_budget,
            component_costs=component_costs,
            compressed_routes=compressed_routes,
            factor_e2_loads=factor_e2_loads,
            indexed_internal=indexed_internal,
            objective=objective,
            route_arcs=route_arcs,
            batch=batch,
        )
        return result

    multibatch_add_first_echelon_constraints(
        parameters,
        variables,
        route=route,
    )
    multibatch_add_supply_coverage_constraints(parameters, variables)
    q_expected, pickup_returns = multibatch_add_cutoff_quantity_constraints(
        parameters,
        variables,
        check_budget=check_budget,
    )
    variables.expected_returns = q_expected
    variables.pickup_returns = pickup_returns
    multibatch_add_second_echelon_constraints(
        parameters,
        variables,
        causal_limit=causal_limit,
        e2_nodes=e2_nodes,
        route=route,
        trip_symmetry=trip_symmetry,
    )
    objective = multibatch_add_shared_route_constraints(
        parameters,
        variables,
        causal_limit=causal_limit,
        check_budget=check_budget,
        component_costs=component_costs,
        compressed_routes=compressed_routes,
        objective=objective,
    )
    multibatch_add_task_and_return_coverage_constraints(
        parameters,
        variables,
        check_budget=check_budget,
    )
    multibatch_add_inventory_constraints(parameters, variables)
    cost_parts = None
    if getattr(problem, 'optimize_pool', False):
        from models.routing.model_5.exact.objective import set_pool_cost_objective
        cost_parts = set_pool_cost_objective(parameters, variables, component_costs)
    else:
        set_distance_objective(m, objective)
    m.update()
    return m, dict(
        parameters=parameters,
        variables=variables,
        e1=routes1,
        e2=routes2,
        shared_e2=compact,
        shared_e2_arcs=len(shared_arcs),
        global_e2_arcs=global_arcs,
        compressed_e2_routes=compressed_routes,
        indexed_e2_internal_arcs=indexed_internal,
        assignments=assignments,
        capacity_activation_cuts=False,
        pickup_returns=pickup_returns,
        shared_cutoff=compact_cutoff,
        trip_count_symmetry=trip_symmetry,
        linear_e2_links=linear_links,
        factored_e2_loads=bool(factor_e2_loads),
        distance_component_costs=component_costs,
        pool=variables.pool,
        cost_parts=cost_parts,
    )
