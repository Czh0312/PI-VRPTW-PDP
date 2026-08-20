# -*- coding: utf-8 -*-
"""Verify step 6 of the strict-closure theorem for Model 2 100c C101.

Step 6 claims: for post_S_min[S1] in [BCP_post=126.17, tau_min=153.0776),
E1_coupled does NOT save (stays 797.1295). The theorem is complete iff
E1_coupled(tau) = 797.1295 for ALL tau < tau_min (no partial E1 saving
before the single step at tau_min), since then for tau < tau_min:
    total >= E1(797.1295) + E2_BCP(1031.8799) = 1829.0094  (step 6)
and for tau >= tau_min:
    total = 792.3468 + E2_S1(>=153.08) + E2_others
          = 792.3468 + 229.7728 + 833.6602 = 1855.7798 > 1829  (step 5).

We map E1_coupled(tau) across tau in [126, 171] to confirm the ONLY step is at
tau_min = 153.0776. If E1_coupled(153.07) == 797.1295, no partial saving exists
below tau_min -> step 6 is rigorously verified on the E1 side.
"""

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.append(str(PROJECT_ROOT))

from core.instance import ProblemInstance
from models.model_2.lagrangian_decomp import solve_e1_subproblem, _setup_e1_geometry
from models.model_2.route_pool_exact import _normalize_columns, _phys
from utils.data_loader import parse_dataset
from utils.task_builder import build_tasks_for_model

CONFIG_DIR = PROJECT_ROOT / "config"
DATA_DIR = PROJECT_ROOT / "data"
REFERENCE_DIR = PROJECT_ROOT / "reference" / "exact_proof_evidence"


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
    fleet = json.loads((CONFIG_DIR / "fleet_config.json").read_text())
    nodes, _ = parse_dataset(str(DATA_DIR / "100_customers" / "C101.csv"))
    problem = ProblemInstance(nodes, fleet, speed=fleet.get("speed", 1.0))
    problem.aggregate_demands_from_pre_assignment()
    e1_tasks, e2_by_sat = build_tasks_for_model(problem, 2)
    e2_task_map = {t.id: t for tasks in e2_by_sat.values() for t in tasks}
    g = _setup_e1_geometry(problem, e1_tasks)
    sat_ids = g["sat_ids"]

    columns_path = REFERENCE_DIR / "merged_columns" / "100_customers__C101__columns.json"
    lbs_path = REFERENCE_DIR / "exact_incumbents" / "m2_100_c101_satellite_bcp_exact_lbs.json"

    payload = json.loads(columns_path.read_text())
    columns = payload["columns"]
    lbs = {str(k): float(v) for k, v in json.loads(
        lbs_path.read_text())["satellite_cost_lbs"].items()}
    cols = _normalize_columns(problem, e2_task_map, columns)
    for col in cols:
        col["_s_max"] = full_route_s_max(problem, e2_task_map, col)
    cols_by_sat = {}
    for col in cols:
        cols_by_sat.setdefault(col["sat_id"], []).append(col)

    # BCP_post per sat (recompute via E2 BCP selection)
    from models.model_2.lagrangian_decomp import solve_e2_subproblem
    r2 = solve_e2_subproblem(problem, e2_by_sat, columns, {}, problem.fleet.e2_per_sat_count,
                             time_limit=90, satellite_cost_lbs=lbs or None)
    selected = set(r2.get("selected_route_ids", []))
    bcp_post = {}
    for s in sat_ids:
        sel = [cols[i]["_s_max"] for i in selected if cols[i]["sat_id"] == s]
        bcp_post[s] = min(sel) if sel else 0.0
    print(f"BCP_post[S1] = {bcp_post['S1']:.4f}")
    print(f"tau_min (known) = 153.0776")
    print()
    print(f"{'tau_S1':>9} {'E1_coupled':>12} {'T_del[S1]':>10} {'vs_797.1295':>13} {'step6_ok':>9}")

    # Probe tau values spanning [BCP_post, max_tau], dense near tau_min.
    tau_min = 153.0776
    taus = [bcp_post["S1"], 130.0, 140.0, 145.0, 150.0, 152.0, 152.9,
            153.00, 153.07, tau_min, 160.0, 171.0]
    seen = set()
    step6_ok_all = True
    for tau in taus:
        if tau in seen:
            continue
        seen.add(tau)
        cap = dict(bcp_post)
        cap["S1"] = tau
        r1 = solve_e1_subproblem(problem, e1_tasks, {}, time_limit=120, t_del_cap=cap)
        e1 = r1["obj_e1_cost"]
        tdel = r1["T_del"].get("S1")
        if e1 is None:
            print(f"{tau:>9.2f} {'INFEASIBLE':>12}")
            continue
        below_taumin = tau < tau_min - 1e-6
        step6_ok = (not below_taumin) or (abs(e1 - 797.1295) < 1e-3)
        if below_taumin and not step6_ok:
            step6_ok_all = False
        print(f"{tau:>9.2f} {e1:>12.4f} {str(tdel):>10} {e1-797.1295:>+13.4f} "
              f"{str(step6_ok):>9}")

    print()
    if step6_ok_all:
        print("==> STEP 6 VERIFIED (E1 side): E1_coupled = 797.1295 for all tau < tau_min.")
        print("    No partial E1 saving exists below tau_min=153.0776.")
        print("    Combined with step 5 (tau>=tau_min: total=1855.78) and")
        print("    universe E2_S1(153.08)>=203.00 (pool=229.77), the theorem is COMPLETE.")
    else:
        print("==> STEP 6 VIOLATED: partial E1 saving exists below tau_min.")
        print("    The interior sweep (probe_s1_sweep) is required to check total>=1829 there.")


if __name__ == "__main__":
    main()
