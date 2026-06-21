# models/model_4/transshipment_mip.py
# -*- coding: utf-8 -*-
"""
Exact second-stage transshipment selector for Model 4.

Stage 1 routes are fixed. This module enumerates feasible single-relocation
plans on those routes, then solves a binary set-packing model that selects the
maximum total distance saving among non-conflicting plans.
"""
from dataclasses import dataclass

import gurobipy as gp
from gurobipy import GRB

from models.alns_base import FEAS_TOL


@dataclass(frozen=True)
class TransshipmentPlan:
    sat_id: str
    beneficiary_idx: int
    carrier_idx: int
    pick_node: str
    old_phys: str
    new_phys: str
    pick_pos: int
    drop_pos: int
    net_saving: float
    beneficiary_saving: float
    carrier_detour: float
    weight: float
    volume: float


def solve_stage2_transshipment_mip(sol, evaluator, verbose=True):
    """
    Optimize transshipment choices for fixed Model 2 routes.

    The model is exact over the enumerated candidate plans. To keep precomputed
    route feasibility and distance deltas valid, each E2 route may participate
    in at most one selected transshipment, either as beneficiary or carrier.
    """
    sol.task_to_phys = dict(evaluator.task_to_phys)
    plans = _enumerate_plans(sol, evaluator)

    if verbose:
        print(f"  候选中转方案数: {len(plans)}")

    if not plans:
        sol.cost = evaluator.total_cost(sol)
        return []

    m = gp.Model("Model4_Stage2_Transshipment")
    if not verbose:
        m.setParam("OutputFlag", 0)

    y = m.addVars(range(len(plans)), vtype=GRB.BINARY, name="y")
    m.setObjective(
        gp.quicksum(plans[i].net_saving * y[i] for i in range(len(plans))),
        GRB.MAXIMIZE,
    )

    # Each pickup can be relocated at most once.
    for pick_node in sorted({p.pick_node for p in plans}):
        m.addConstr(
            gp.quicksum(y[i] for i, p in enumerate(plans)
                        if p.pick_node == pick_node) <= 1,
            f"PickupOnce_{pick_node}",
        )

    # Keep precomputed plan deltas independent: one role per physical route.
    route_keys = sorted({
        (p.sat_id, p.beneficiary_idx) for p in plans
    } | {
        (p.sat_id, p.carrier_idx) for p in plans
    })
    for sat_id, route_idx in route_keys:
        m.addConstr(
            gp.quicksum(
                y[i] for i, p in enumerate(plans)
                if (p.sat_id == sat_id
                    and (p.beneficiary_idx == route_idx
                         or p.carrier_idx == route_idx))
            ) <= 1,
            f"RouteOnce_{sat_id}_{route_idx}",
        )

    m.optimize()

    if m.status not in (GRB.OPTIMAL, GRB.TIME_LIMIT) or m.SolCount == 0:
        if verbose:
            print("  阶段二 MIP 未选择中转方案")
        sol.cost = evaluator.total_cost(sol)
        return []

    selected = [plans[i] for i in range(len(plans)) if y[i].X > 0.5]
    _apply_plans(sol, evaluator, selected)
    sol.cost = evaluator.total_cost(sol)

    if verbose:
        total_saving = sum(p.net_saving for p in selected)
        print(f"  阶段二 MIP 选择中转数: {len(selected)}")
        print(f"  阶段二 MIP 净改进: {total_saving:.2f}")
        for p in selected:
            print(
                f"    sat={p.sat_id}: 搬运车{p.carrier_idx} "
                f"取@{p.old_phys}->放@{p.new_phys}, "
                f"取货车{p.beneficiary_idx} {p.pick_node}, "
                f"net={p.net_saving:.2f}"
            )

    return selected


def _enumerate_plans(sol, evaluator):
    plans = []
    base_task_to_phys = dict(sol.task_to_phys or evaluator.task_to_phys)
    evaluator.task_to_phys = dict(base_task_to_phys)
    e2_start_by_sat, e1_pickups = evaluator.e1_schedule(sol)
    if e2_start_by_sat is None:
        return plans

    for sat_id, routes in sol.e2_routes.items():
        tasks = evaluator.e2_tasks_by_sat.get(sat_id, [])
        candidate_phys = sorted({t.cust for t in tasks})
        start_time = e2_start_by_sat.get(sat_id, 0.0)

        for ben_idx, ben_route in enumerate(routes):
            for pick_node in list(ben_route.nodes):
                if pick_node.startswith("TRANS_"):
                    continue
                task = evaluator.e2_task_map.get(pick_node)
                if not task or task.kind != "p":
                    continue

                old_phys = base_task_to_phys.get(pick_node, task.cust)
                old_ben_dist = evaluator.route_distance(ben_route)

                for new_phys in candidate_phys:
                    if new_phys == old_phys:
                        continue

                    evaluator.task_to_phys[pick_node] = new_phys
                    try:
                        ben_ok, ben_arrivals, ben_return = evaluator.simulate_e2_route(
                                ben_route, sat_id, start_time)
                        if not ben_ok:
                            continue
                        if f"{sat_id}p" in evaluator.e1_task_map:
                            if ben_return > e1_pickups.get(sat_id, -float("inf")) + FEAS_TOL:
                                continue
                        new_ben_dist = evaluator.route_distance(ben_route)
                        base_arrivals = {}
                        for route in routes:
                            ok, arrivals, _ret = evaluator.simulate_e2_route(
                                route, sat_id, start_time)
                            if ok:
                                base_arrivals.update(arrivals)
                    finally:
                        evaluator.task_to_phys[pick_node] = old_phys

                    ben_saving = old_ben_dist - new_ben_dist
                    if ben_saving <= FEAS_TOL:
                        continue

                    for carrier_idx, carrier_route in enumerate(routes):
                        if carrier_idx == ben_idx:
                            continue
                        plans.extend(
                            _carrier_insertions(
                                sat_id, ben_idx, carrier_idx,
                                pick_node, old_phys, new_phys,
                                task, ben_saving, carrier_route, evaluator,
                                start_time, ben_arrivals,
                                e1_pickups.get(sat_id), base_arrivals,
                            )
                        )

    evaluator.task_to_phys = dict(base_task_to_phys)
    plans.sort(key=lambda p: p.net_saving, reverse=True)
    return plans


def _carrier_insertions(sat_id, ben_idx, carrier_idx, pick_node,
                        old_phys, new_phys, task, ben_saving,
                        carrier_route, evaluator, start_time, ben_arrivals,
                        e1_pickup_time, base_arrivals):
    plans = []
    old_carrier_dist = evaluator.route_distance(carrier_route)
    n = len(carrier_route.nodes)
    pick_vnode = _transport_pick_node(pick_node, old_phys)
    drop_vnode = _transport_drop_node(pick_node, new_phys)

    for pick_pos in range(n + 1):
        with_pick = carrier_route.copy()
        with_pick.nodes.insert(pick_pos, pick_vnode)
        with_pick.transport_loads[pick_vnode] = (task.w, task.v)

        for drop_pos in range(pick_pos + 1, n + 2):
            test_route = with_pick.copy()
            test_route.nodes.insert(drop_pos, drop_vnode)
            test_route.transport_loads[drop_vnode] = (task.w, task.v)

            carrier_ok, carrier_arrivals, carrier_return = evaluator.simulate_e2_route(
                test_route, sat_id, start_time)
            if not carrier_ok:
                continue
            if (ben_arrivals.get(pick_node, float("inf"))
                    < carrier_arrivals.get(drop_vnode, float("inf")) - FEAS_TOL):
                continue
            d_id = f"{task.cust}d"
            d_arrival = carrier_arrivals.get(
                d_id, ben_arrivals.get(d_id, base_arrivals.get(d_id))
            )
            if d_id in evaluator.e2_task_map:
                if d_arrival is None:
                    continue
                if (carrier_arrivals.get(pick_vnode, -float("inf"))
                        < d_arrival + evaluator.service_time(d_id) - FEAS_TOL):
                    continue
            if (e1_pickup_time is not None
                    and carrier_return > e1_pickup_time + FEAS_TOL):
                continue

            new_carrier_dist = evaluator.route_distance(test_route)
            detour = new_carrier_dist - old_carrier_dist
            net = ben_saving - detour
            if net <= FEAS_TOL:
                continue

            plans.append(
                TransshipmentPlan(
                    sat_id=sat_id,
                    beneficiary_idx=ben_idx,
                    carrier_idx=carrier_idx,
                    pick_node=pick_node,
                    old_phys=old_phys,
                    new_phys=new_phys,
                    pick_pos=pick_pos,
                    drop_pos=drop_pos,
                    net_saving=net,
                    beneficiary_saving=ben_saving,
                    carrier_detour=detour,
                    weight=task.w,
                    volume=task.v,
                )
            )

    return plans


def _apply_plans(sol, evaluator, selected):
    evaluator.task_to_phys = dict(sol.task_to_phys or evaluator.task_to_phys)

    for p in selected:
        evaluator.task_to_phys[p.pick_node] = p.new_phys
        sol.task_to_phys[p.pick_node] = p.new_phys

        carrier_route = sol.e2_routes[p.sat_id][p.carrier_idx]
        pick_vnode = _transport_pick_node(p.pick_node, p.old_phys)
        drop_vnode = _transport_drop_node(p.pick_node, p.new_phys)

        carrier_route.nodes.insert(p.pick_pos, pick_vnode)
        carrier_route.transport_loads[pick_vnode] = (p.weight, p.volume)
        carrier_route.nodes.insert(p.drop_pos, drop_vnode)
        carrier_route.transport_loads[drop_vnode] = (p.weight, p.volume)

        sol.transshipments[
            (p.sat_id, p.beneficiary_idx, p.pick_node, p.new_phys)
        ] = (p.old_phys, p.carrier_idx, p.weight, p.volume)

    evaluator.task_to_phys = dict(sol.task_to_phys)


def _transport_pick_node(pick_node, old_phys):
    return f"TRANS_PICK_{pick_node}@{old_phys}"


def _transport_drop_node(pick_node, new_phys):
    return f"TRANS_DROP_{pick_node}@{new_phys}"
