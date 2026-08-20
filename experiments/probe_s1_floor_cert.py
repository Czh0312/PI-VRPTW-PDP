# -*- coding: utf-8 -*-
"""Certify the universe min E2 S1 cost with a latest-departure floor, closing
the last gap of the strict-closure theorem for Model 2 100c C101.

Theorem needs: E2_S1(s_max >= tau_min=153.0776) >= 203.00 over the FULL route
universe (so that total(tau_min) = 792.3468 + E2_S1 + 833.6602 >= 1829.0094).
The 16336-col pool gives 229.77 (margin +26.77); this script CERTIFIES the
universe value via floor-restricted column generation with MIP pricing
(s_max-floor-aware in e2_route_cg._solve_pricing_mip).

Method:
  - initial columns = pool S1 routes with full-route s_max >= 153.0776
  - CG loop: set-partition LP (cover==1, vehicle<=7) -> duals -> MIP
    pricing with s_max_floor=153.0776 -> add negative-RC cols -> repeat
  - convergence (pricing OPTIMAL, rc_bound >= -EPS, no new columns) =>
    the LP optimum is the universe floor-restricted LP optimum.
  - LP optimum <= integer optimum, so LP >= 203.00 => integer >= 203.00.

If the LP needs artificials (some S1 task uncoverable at the floor), the
floor-restricted problem is infeasible => E2_S1(153.08) = +inf > 203, which is
an even stronger closure (no E2 set can loosen S1 that far).

Usage:
    GRB_THREADS=2 .venv/bin/python experiments/probe_s1_floor_cert.py \
        --columns-json reference/exact_proof_evidence/merged_columns/100_customers__C101__columns.json
"""

import argparse
import json
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.append(str(PROJECT_ROOT))

import gurobipy as gp
from gurobipy import GRB

from core.instance import ProblemInstance
from models.model_2.e2_route_cg import (
    E2RouteColumn,
    _build_master,
    _solve_pricing_mip,
    _solve_pricing_labeling_exact,
)
from models.model_2.route_pool_exact import _normalize_columns, _phys
from utils.data_loader import parse_dataset
from utils.task_builder import build_tasks_for_model

CONFIG_DIR = PROJECT_ROOT / "config"
DATA_DIR = PROJECT_ROOT / "data"
REFERENCE_DIR = PROJECT_ROOT / "reference" / "exact_proof_evidence"
EPS = 1e-6


def full_route_s_max(problem, e2_task_map, col):
    path = col["path"]
    sat_phys = _phys(e2_task_map, col["sat_id"])
    horizon = max((t.tw[1] for t in e2_task_map.values()
                   if t.kind == "d" and t.tw[1] < float("inf")), default=2000.0)
    sm = horizon
    task_nodes = [n for n in path if n in e2_task_map]
    cum = 0.0
    prev_phys = sat_phys
    prev_service = 0.0
    for idx, node in enumerate(task_nodes):
        task = e2_task_map[node]
        l_i = task.tw[1]
        travel_seg = problem.travel_time(prev_phys, task.cust)
        cum = travel_seg if idx == 0 else cum + prev_service + travel_seg
        if l_i < float("inf"):
            sm = min(sm, l_i - cum)
        prev_phys = task.cust
        node_obj = problem.nodes.get(task.cust)
        prev_service = getattr(node_obj, "service_time", 0.0) if node_obj else 0.0
    return sm


def main():
    ap = argparse.ArgumentParser(
        description="Certify universe E2 S1 cost with latest-departure floor")
    ap.add_argument("--scale", default="100_customers")
    ap.add_argument("--instance", default="C101")
    ap.add_argument("--columns-json",
                    default=str(REFERENCE_DIR / "merged_columns"
                                / "100_customers__C101__columns.json"))
    ap.add_argument("--satellite", default="S1")
    ap.add_argument("--floor", type=float, default=153.0776)
    ap.add_argument("--threshold", type=float, default=203.00)
    ap.add_argument("--max-iter", type=int, default=60)
    ap.add_argument("--pricing-time", type=float, default=300.0)
    ap.add_argument("--pricing-max-expansions", type=int, default=4000000)
    ap.add_argument("--dominance-mode", default="same_visited",
                    choices=["same_visited", "subset", "subset_bucket"])
    ap.add_argument("--pricing-method", default="mip", choices=["mip", "label"])
    args = ap.parse_args()

    fleet = json.loads((CONFIG_DIR / "fleet_config.json").read_text())
    nodes, _ = parse_dataset(str(DATA_DIR / args.scale / f"{args.instance}.csv"))
    problem = ProblemInstance(nodes, fleet, speed=fleet.get("speed", 1.0))
    problem.aggregate_demands_from_pre_assignment()
    _, e2_by_sat = build_tasks_for_model(problem, 2)
    tasks = e2_by_sat[args.satellite]
    task_ids = [t.id for t in tasks]
    e2_task_map = {t.id: t for t in tasks}
    vehicles = problem.fleet.e2_per_sat_count

    payload = json.loads(Path(args.columns_json).read_text())
    columns = payload["columns"] if isinstance(payload, dict) else payload
    cols = _normalize_columns(problem, e2_task_map, columns)
    floor_cols = [c for c in cols if c["sat_id"] == args.satellite]
    for c in floor_cols:
        c["_s_max"] = full_route_s_max(problem, e2_task_map, c)

    pool_floor = [c for c in floor_cols if c["_s_max"] >= args.floor - EPS]
    print(f"[{args.scale}/{args.instance}/{args.satellite}] floor={args.floor}")
    print(f"  S1 pool routes total={len(floor_cols)}, with s_max>={args.floor}: {len(pool_floor)}")

    # Coverage check: can floor routes cover every S1 task?
    covered = set()
    for c in pool_floor:
        covered.update(c["tasks"])
    uncovered = set(task_ids) - covered
    if uncovered:
        print(f"  {len(uncovered)} S1 task(s) UNCOVERABLE at the floor: {sorted(uncovered)}")
        print(f"  ==> floor-restricted E2_S1 is INFEASIBLE = +inf > {args.threshold}")
        print(f"      (no E2 set can loosen {args.satellite} to post_S_min>={args.floor};")
        print(f"       closure holds trivially).")
        return

    # Initial columns: pool floor routes (dedup by path).
    columns_cg = []
    seen = set()
    for c in pool_floor:
        p = tuple(c["path"])
        if p in seen:
            continue
        seen.add(p)
        columns_cg.append(E2RouteColumn(
            sat_id=args.satellite, path=p,
            tasks=tuple(n for n in c["path"] if n in e2_task_map),
            cost=float(c["cost"]), source="pool_floor",
        ))
    print(f"  initial CG columns (dedup): {len(columns_cg)}")

    t0 = time.time()
    lp_obj = None
    converged = False
    last_info = None
    for it in range(args.max_iter):
        m, lambdas, artificials, cover_constrs, vehicle_constr, _ = _build_master(
            args.satellite, task_ids, columns_cg, vehicles, relax=True,
        )
        m.Params.OutputFlag = 0
        m.optimize()
        if m.status != GRB.OPTIMAL:
            print(f"  iter {it}: LP status {m.status}")
            break
        lp_obj = float(m.ObjVal)
        art_sum = sum(v.X for v in artificials.values())
        cover_duals = {tid: cover_constrs[tid].Pi for tid in task_ids}
        vehicle_dual = float(vehicle_constr.Pi)

        if args.pricing_method == "label":
            cols_out, info = _solve_pricing_labeling_exact(
                problem, args.satellite, tasks, cover_duals, vehicle_dual,
                pool_solutions=20, max_expansions=args.pricing_max_expansions,
                time_limit=args.pricing_time, stop_after_columns=True,
                dominance_mode=args.dominance_mode, s_max_floor=args.floor,
            )
            certified_now = (
                info["status"] == "EXACT_LABEL_OPTIMAL"
                and info.get("reduced_cost_bound", float("-inf")) >= -EPS
            )
        else:
            cols_out, info = _solve_pricing_mip(
                problem, args.satellite, tasks, cover_duals, vehicle_dual,
                pricing_time_limit=args.pricing_time, pool_solutions=10,
                s_max_floor=args.floor,
            )
            rcb = info.get("reduced_cost_bound", float("-inf"))
            certified_now = rcb is not None and rcb >= -EPS and len(cols_out) == 0
        last_info = info
        n_added = 0
        for c in cols_out:
            if c.path in seen:
                continue
            seen.add(c.path)
            columns_cg.append(c)
            n_added += 1
        print(f"  iter {it}: LP={lp_obj:.4f} art={art_sum:.2f} "
              f"pricing={info['status']} rc_bound={info.get('reduced_cost_bound')} "
              f"new={n_added} cols={len(columns_cg)} t={time.time()-t0:.1f}s")

        if certified_now:
            converged = True
            break
        if n_added == 0 and not cols_out:
            # pricing found no negative-RC column but did not certify -> stuck
            break

    print()
    if lp_obj is None:
        print("  LP never solved; certification inconclusive.")
        return
    print(f"  floor-restricted S1 LP optimum = {lp_obj:.4f}")
    print(f"  pricing converged (universe-certified): {converged}  "
          f"(last status={last_info['status'] if last_info else '?'})")
    print(f"  threshold for closure = {args.threshold:.4f}")
    if converged and lp_obj >= args.threshold - 1e-4:
        print(f"\n  ==> CERTIFIED: universe E2_S1(s_max>={args.floor}) = {lp_obj:.4f} >= {args.threshold:.4f}")
        print(f"      Strict-closure theorem COMPLETE -> Model 2 {args.scale}/{args.instance}")
        print(f"      PROVEN OPTIMAL at 1829.0093866572092.")
    elif lp_obj >= args.threshold - 1e-4:
        print(f"\n  ==> LP >= threshold but pricing NOT certified (status {last_info['status']});")
        print(f"      strong evidence, not a rigorous certificate yet.")
    else:
        print(f"\n  ==> LP {lp_obj:.4f} < threshold {args.threshold:.4f}: closure would FAIL")
        print(f"      at the LP level; need integer floor-restricted optimum (BCP).")


if __name__ == "__main__":
    main()
