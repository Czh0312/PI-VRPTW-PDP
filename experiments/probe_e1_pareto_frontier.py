#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Probe: enumerate the Pareto-minimal E1 plan frontier for Model 2 exactness.

Claim: the whole Model 2 exact problem decomposes as

    min over feasible E1 plans P of [ E1_cost(P) + sum_s E2_s(P) ]

where E2_s(P) is the per-satellite E2 route-selection optimum subject to the
coupling implied by P (E2 departure floor = T_del[s], return ceiling = T_pick[s]).
Since E2_s(P) >= per-satellite BCP bound L_s for EVERY P, any E1 plan with
E1_cost >= UB - sum_s L_s is pruned without any E2 work.  The plans below that
threshold form a small finite set; we enumerate them by iteratively solving the
tiny E1 MIP with no-good cuts on the chosen x-arcs.

This script only enumerates the E1 side (seconds per solve).  For each frontier
plan it prints the coupling vector so the E2 floor/cap certificates can be
targeted exactly as in probe_s1_floor_cert.py.
"""

import argparse
import json
import sys
import time
from pathlib import Path

import gurobipy as gp
from gurobipy import GRB

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.append(str(PROJECT_ROOT))

from core.instance import ProblemInstance
from models.model_2.route_pool_exact import _add_e1_constraints
from utils.data_loader import parse_dataset
from utils.task_builder import build_tasks_for_model

CONFIG_DIR = PROJECT_ROOT / "config"
DATA_DIR = PROJECT_ROOT / "data"
REFERENCE_DIR = PROJECT_ROOT / "reference" / "exact_proof_evidence"


def load_problem(scale, instance):
    fleet = json.loads((CONFIG_DIR / "fleet_config.json").read_text())
    nodes, _ = parse_dataset(str(DATA_DIR / scale / f"{instance}.csv"))
    problem = ProblemInstance(nodes, fleet, speed=fleet.get("speed", 1.0))
    problem.aggregate_demands_from_pre_assignment()
    return problem


def build_e1_mip(problem, e1_tasks, cost_cap=None):
    """E1-only arc-flow MIP reusing the exact master's E1 constraint block."""
    depot_id = problem.depot.id
    F = [s.id for s in problem.satellites]
    K = list(range(problem.e1_vehicle_count))
    e1_nodes = [t.id for t in e1_tasks]
    n1e = [depot_id] + e1_nodes
    e1_task_map = {t.id: t for t in e1_tasks}

    def loc(node_id):
        if node_id == depot_id or node_id in F:
            return node_id
        task = e1_task_map.get(node_id)
        return task.cust if task is not None else node_id

    dist = {}
    travel = {}
    for i in n1e:
        for j in n1e:
            dist[i, j] = 0.0 if i == j else problem.distance(loc(i), loc(j))
            travel[i, j] = 0.0 if i == j else problem.travel_time(loc(i), loc(j))

    e1_service = {depot_id: 0.0}
    for node in e1_nodes:
        phys = loc(node)
        node_obj = problem.nodes.get(phys)
        e1_service[node] = getattr(node_obj, "service_time", 0.0) if node_obj else 0.0

    m = gp.Model("E1_Pareto")
    x = m.addVars(n1e, n1e, K, vtype=GRB.BINARY, name="x")
    A = m.addVars(n1e, K, lb=0.0, name="A_e1")
    Q = m.addVars(n1e, K, lb=0.0, ub=problem.e1_weight_cap, name="Q_k")
    V = m.addVars(n1e, K, lb=0.0, ub=problem.e1_volume_cap, name="Vol_k")
    _add_e1_constraints(
        m, problem, e1_tasks, x, A, Q, V,
        n1e, e1_nodes, K, depot_id, e1_task_map, dist, travel, e1_service,
    )
    obj_e1 = gp.quicksum(
        dist[i, j] * x[i, j, k]
        for k in K for i in n1e for j in n1e if i != j
    )
    if cost_cap is not None:
        m.addConstr(obj_e1 <= float(cost_cap), name="E1_cost_cap")
    m.setObjective(obj_e1, GRB.MINIMIZE)
    m.setParam("OutputFlag", 0)
    m.setParam("MIPGap", 0.0)
    return m, {"x": x, "A_e1": A, "n1e": n1e, "K": K, "e1_task_map": e1_task_map,
               "e1_service": e1_service, "obj": obj_e1, "F": F, "depot_id": depot_id}


def extract_plan(m, ctx, e1_tasks):
    """Extract (cost, t_del, t_pick, arcs) from a solved E1 MIP."""
    x = ctx["x"]
    A = ctx["A_e1"]
    K = ctx["K"]
    n1e = ctx["n1e"]
    e1_task_map = ctx["e1_task_map"]
    e1_service = ctx["e1_service"]

    arcs = []
    for (i, j, k), var in x.items():
        if var.X > 0.5:
            arcs.append((i, j, k))
    arcs.sort()
    cost = float(ctx["obj"].getValue())

    t_del = {}
    t_pick = {}
    for task in e1_tasks:
        s = task.cust
        visiting_k = None
        for k in K:
            if any(x[i, task.id, k].X > 0.5 for i in n1e if i != task.id):
                visiting_k = k
                break
        if visiting_k is None:
            raise RuntimeError(f"task {task.id} not visited in extracted plan")
        val = float(A[task.id, visiting_k].X)
        if task.kind == "d":
            t_del[s] = val + float(e1_service.get(task.id, 0.0))
        else:
            t_pick[s] = val
    return {
        "cost": cost,
        "t_del": t_del,
        "t_pick": t_pick,
        "arcs": arcs,
        "e1_tasks": [t.id for t in e1_tasks],
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scale", default="100_customers")
    ap.add_argument("--instance", default="C101")
    ap.add_argument("--ub", type=float, default=1829.0093866572092)
    ap.add_argument("--e2-lb", type=float, default=1031.8799212028766)
    ap.add_argument("--max-plans", type=int, default=2000)
    ap.add_argument("--time-limit", type=float, default=30.0)
    args = ap.parse_args()

    problem = load_problem(args.scale, args.instance)
    e1_tasks, e2_tasks_by_sat = build_tasks_for_model(problem, 2)
    C0 = args.ub - args.e2_lb
    print(f"Instance: {args.scale}/{args.instance}")
    print(f"E1 trucks={problem.e1_vehicle_count} E1 tasks={len(e1_tasks)} "
          f"sats={len(e2_tasks_by_sat)}")
    print(f"UB={args.ub:.6f}  E2_LB={args.e2_lb:.6f}  "
          f"E1 cost threshold C0={C0:.6f}")
    print(f"(E1 plans with cost >= C0 are pruned by the tau-independent E2 bound)")

    t0 = time.time()
    plans = []
    seen_arcs = set()
    for it in range(args.max_plans):
        m, ctx = build_e1_mip(problem, e1_tasks, cost_cap=C0 - 1e-9)
        m.setParam("TimeLimit", args.time_limit)
        # no-good cuts over already-found plans (exact arc assignments)
        for plan in plans:
            arc_set = plan["arcs"]
            m.addConstr(
                gp.quicksum(ctx["x"][arc] for arc in arc_set) <= len(arc_set) - 1,
                name=f"nogood_{it}",
            )
        m.optimize()
        if m.status != GRB.OPTIMAL:
            if m.status == GRB.INFEASIBLE:
                print(f"iter {it}: E1 MIP infeasible below C0 -> enumeration complete")
            else:
                print(f"iter {it}: E1 MIP status {m.status} -> stopping")
            break
        plan = extract_plan(m, ctx, e1_tasks)
        plans.append(plan)
        tau_key = (tuple(sorted((s, round(v, 4)) for s, v in plan["t_del"].items())),
                   tuple(sorted((s, round(v, 4)) for s, v in plan["t_pick"].items())))
        if it < 8 or it % 50 == 0:
            print(f"iter {it}: cost={plan['cost']:.4f}  tau_S1_del={plan['t_del'].get('S1')}  "
                  f"t={time.time()-t0:.1f}s")
    else:
        print(f"reached --max-plans={args.max_plans} (enumeration NOT complete)")

    print(f"\nTotal plans enumerated below C0: {len(plans)}  "
          f"(elapsed {time.time()-t0:.1f}s)")

    # Deduplicate by rounded tau vector, keep the minimum cost per class.
    classes = {}
    for p in plans:
        tau_key = (tuple(sorted((s, round(v, 4)) for s, v in p["t_del"].items())),
                   tuple(sorted((s, round(v, 4)) for s, v in p["t_pick"].items())))
        if tau_key not in classes or p["cost"] < classes[tau_key]["cost"]:
            classes[tau_key] = p
    print(f"Distinct tau-classes: {len(classes)}")

    # Pareto filter: keep plans not dominated by any other (lower/equal cost,
    # componentwise t_del <=, t_pick >=).
    frontier = []
    items = list(classes.values())
    for i, p in enumerate(items):
        dominated = False
        for j, q in enumerate(items):
            if i == j:
                continue
            if (q["cost"] <= p["cost"] + 1e-9
                    and all(q["t_del"][s] <= p["t_del"][s] + 1e-9 for s in q["t_del"])
                    and all(q["t_pick"][s] >= p["t_pick"][s] - 1e-9 for s in q["t_pick"])):
                dominated = True
                break
        if not dominated:
            frontier.append(p)
    print(f"Pareto-minimal tau-classes: {len(frontier)}")
    for p in sorted(frontier, key=lambda p: p["cost"]):
        print(f"  cost={p['cost']:.4f}  t_del={ {s: round(v,2) for s,v in p['t_del'].items()} }")


if __name__ == "__main__":
    main()
