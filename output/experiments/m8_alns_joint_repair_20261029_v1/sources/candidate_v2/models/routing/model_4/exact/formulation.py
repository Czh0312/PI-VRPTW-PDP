"""B=1 joint MILP with implied bounds and homogeneous vehicle symmetry."""
from __future__ import annotations

import time

from models.routing.model_4.exact.preprocessing.model_bounds import (
    route_arcs,
    trip_slot_limits,
    COMPRESS_ROUTE_MIN_INTERNAL_ARCS,
)


from models.routing.model_4.exact.parameters import prepare_parameters
from models.routing.model_4.exact.variables import ModelVariables
from models.routing.model_4.exact.constraints import (
    add_assignment_constraints,
    add_supply_time_constraints,
    add_route_constraints,
    add_first_echelon_constraints,
    add_supply_coverage_constraints,
    add_second_echelon_constraints,
    add_shared_route_constraints,
    add_task_and_return_coverage_constraints,
    add_inventory_constraints,
)
from models.routing.model_4.exact.objective import set_distance_objective


def build_model(
    problem,
    *,
    verbose=False,
    deadline=None,
    share_e2=True,
    travel_bounds=None,
    domain_check=None,
    compress_routes=None,
    linear_e2_links=None,
    factor_e2_loads=None,
    strengthening='none',
):
    import gurobipy as gp


    parameters = prepare_parameters(
        domain_check=domain_check,
        linear_e2_links=linear_e2_links,
        problem=problem,
        share_e2=share_e2,
        travel_bounds=travel_bounds,
    )
    p = parameters.policy
    f = parameters.fleet
    free = parameters.free_assignment
    compact = parameters.share_e2
    linear_mode = parameters.linear_link_mode
    windows = parameters.task_windows
    site_windows = parameters.site_windows
    domains = parameters.assignment_domains
    travel_bounds = parameters.travel_bounds
    domain_check = parameters.domain_check
    tightened = parameters.bounds
    H = parameters.horizon
    total = parameters.total_containers
    m = gp.Model("multi_batch_cutoff_v1")
    variables = ModelVariables(m)
    m.Params.OutputFlag = int(verbose)
    m.Params.MIPGap = 0
    m.Params.MIPGapAbs = 0
    m.Params.Threads = 1
    routes1, routes2, objective = [], [], gp.LinExpr()
    variables.routes["e1"] = routes1
    variables.routes["e2"] = routes2
    assignments, active_sites, counts, weights = add_assignment_constraints(parameters, variables)
    variables.assignments = assignments
    variables.active_sites = active_sites
    variables.demand_counts = counts
    variables.demand_weights = weights
    deliveries = add_supply_time_constraints(parameters, variables)
    variables.supply_times = deliveries
    order_times = {
        (o.id, k): variables.create_task_time(H, k, o)
        for o in problem.orders
        for k in problem.task_kinds
    }
    variables.task_times = order_times
    # Each task is visited exactly once across all vehicles and trip slots.
    # Its time and post-service load therefore have one well-defined value.
    # Keep route-indexed boundary arcs, but share internal propagation once.
    task_load_w = {key: variables.create_task_load_w(f) for key in order_times} if compact else {}
    variables.task_load_weight = task_load_w
    task_load_v = {key: variables.create_task_load_v(f) for key in order_times} if compact else {}
    variables.task_load_volume = task_load_v
    if compact:
        for key, (lw, uw, lv, uv) in tightened["loads"].items():
            task_load_w[key].LB, task_load_w[key].UB = lw, uw
            task_load_v[key].LB, task_load_v[key].UB = lv, uv
    shared_arcs = {}
    variables.shared_arcs = shared_arcs
    aggregate_arcs = {}
    variables.aggregate_arcs = aggregate_arcs
    global_arcs = {}
    variables.global_arcs = global_arcs
    causal_limit = tightened["causal_limit"]
    # Every mandatory task has a selected predecessor and successor: its
    # step lies strictly between a nonnegative route start and an end <= L.
    # Mandatory d -> p precedence also makes d <= L-2 and p >= 2.
    task_step_bounds = {
        (o.id, k): (
            ((0, causal_limit))
        )
        for o in problem.orders
        for k in problem.task_kinds
    }
    order_steps = {
        key: variables.create_task_step(hi, lo) for key, (lo, hi) in task_step_bounds.items()
    }
    variables.task_steps = order_steps
    causal_largest_m = 0

    def step_slack(left, right):
        nonlocal causal_largest_m
        slack = (causal_limit + 1)
        causal_largest_m = max(causal_largest_m, slack)
        return slack

    e2_arc_cache = {}

    def check_budget():
        if deadline is not None and time.perf_counter() >= deadline:
            m.dispose()
            raise TimeoutError("MILP construction reached the total time budget")

    e2_nodes = {}
    indexed_internal = 0
    for s in problem.satellites:
        check_budget()
        nodes = []
        for o in problem.orders:
            if not (s in domains[o.customer] if free else o.satellite == s):
                continue
            for kind in problem.task_kinds:
                _, weight, volume = problem.task(o.id, kind)
                nodes.append(
                    dict(
                        site=o.customer,
                        order=o,
                        kind=kind,
                        quantity=o.count,
                        w=weight,
                        v=volume,
                        service=o.service_time,
                        window=site_windows[s, o.id, kind] if free else windows[o.id, kind],
                    )
                )
        e2_nodes[s] = nodes
        e2_arc_cache[s] = route_arcs(problem, nodes, s, "e2", check_budget=check_budget)
        indexed_internal += sum(a >= 0 and b >= 0 for a, b in e2_arc_cache[s]) * sum(
            trip_slot_limits(problem, len(nodes))
        )
    compressed = compact and (
        indexed_internal >= COMPRESS_ROUTE_MIN_INTERNAL_ARCS
        if compress_routes is None
        else compress_routes
    )
    if linear_e2_links is None:
        # Bounded linear links help indexed pickup models. Large compressed
        # models also factor departure loads below; their dense source-link
        # sums otherwise dominate the matrix and can hurt incumbent search.
        linear_mode = "complete" if compressed or (not free and problem.reusable) else False
    linear_identity = compressed and linear_mode in ("identity", "all", "complete")
    linear_internal = compact and linear_mode in ("internal", "all", "complete")
    linear_boundary = compact and linear_mode in ("all", "complete")
    linear_schedule = compact and linear_mode == "complete"
    factor_loads = compact and (
        compressed and linear_schedule if factor_e2_loads is None else factor_e2_loads
    )
    site_arcs = {}
    identities = {}

    def route(nodes, base, cap_w, cap_v, unload, label, layer, batch=None):
        nonlocal objective
        result, objective = add_route_constraints(
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
            compressed=compressed,
            e2_arc_cache=e2_arc_cache,
            factor_loads=factor_loads,
            linear_boundary=linear_boundary,
            linear_schedule=linear_schedule,
            objective=objective,
            route_arcs=route_arcs,
            batch=batch,
        )
        return result

    add_first_echelon_constraints(
        parameters,
        variables,
        route=route,
    )
    add_supply_coverage_constraints(parameters, variables)
    add_second_echelon_constraints(
        parameters,
        variables,
        causal_limit=causal_limit,
        e2_nodes=e2_nodes,
        linear_boundary=linear_boundary,
        linear_schedule=linear_schedule,
        route=route,
        step_slack=step_slack,
        task_step_bounds=task_step_bounds,
    )
    objective, identities, site_arcs = add_shared_route_constraints(
        parameters,
        variables,
        check_budget=check_budget,
        compressed=compressed,
        e2_arc_cache=e2_arc_cache,
        e2_nodes=e2_nodes,
        linear_identity=linear_identity,
        linear_internal=linear_internal,
        objective=objective,
        step_slack=step_slack,
        task_step_bounds=task_step_bounds,
    )
    add_task_and_return_coverage_constraints(
        parameters,
        variables,
        check_budget=check_budget,
    )
    add_inventory_constraints(parameters, variables)
    set_distance_objective(m, objective)
    m.update()
    context = dict(
        parameters=parameters,
        variables=variables,
        e1=routes1,
        e2=routes2,
        shared_e2=compact,
        shared_e2_arcs=len(shared_arcs),
        global_e2_arcs=global_arcs,
        compressed_e2_routes=compressed,
        indexed_e2_internal_arcs=indexed_internal,
        single_batch_linear_links=linear_mode if compact else False,
        factored_e2_loads=factor_loads,
        e2_site_flow=False,
        e2_site_arcs=site_arcs,
        e2_route_identities=identities,
        tight_e2_route_bounds=False,
        e2_causal_step_bounds=False,
        e2_task_steps=order_steps,
        e2_causal_largest_m=causal_largest_m,
        e2_causal_limit=causal_limit,
        assignments=assignments,
        trip_count_symmetry=True,
        single_batch_bounds=True,
    )
    from models.routing.model_4.exact.strengthening import strengthen
    context['strengthening'] = strengthen(problem, context, strengthening, check_budget)
    context['e2_causal_step_bounds'] = strengthening in ('bounds', 'combined')
    context['tight_e2_route_bounds'] = strengthening in ('bounds', 'combined')
    m.update()
    return m, context
