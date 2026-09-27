"""M8 adaptive destroy/repair search over relay modes on ONE frozen M3 plan.

MILP repairs optimize carriers, insertion positions and feasible schedules.
This is an ALNS matheuristic and requires Gurobi, unlike pure M3 ALNS.
The default stage-one solve is exact; an unproven base never silently becomes
the requested optimal-base experiment.
"""
import math
import random
import time

from models.routing.variants.model_8.legacy.baseline import prepare_baseline, initial_info


def solve_alns(problem, e1_tasks, e2_tasks_by_sat, vehicles_per_satellite,
               TFp=9999.0, max_iterations=5000, max_time=120, seed=42,
               verbose=True, return_info=False, stage1_time_ratio=None,
               base_routes=None, base_info=None, stage1_method="exact",
               repair_time_limit=2.0, max_remove=3, gurobi_params=None):
    from models.routing.variants.model_8.legacy.exact_solver import (
        optimize_stage2, merge_stage2,
    )

    if max_time <= 0 or repair_time_limit <= 0 or max_remove < 1 or max_iterations < 0:
        raise ValueError("M8 时间预算须为正，max_remove>=1，max_iterations>=0")
    if stage1_method not in ("exact", "alns"):
        raise ValueError("stage1_method 必须为 exact 或 alns")
    ratio = 0.6 if stage1_time_ratio is None else stage1_time_ratio
    if not 0 < ratio < 1:
        raise ValueError("stage1_time_ratio 必须在 (0, 1) 内")
    started = time.perf_counter()
    deadline = started + max_time
    rng = random.Random(seed)
    if base_routes is None:
        if stage1_method == "exact":
            from models.routing.variants.model_3.exact_solver import solve_exact
            params = dict(gurobi_params or {})
            params.update(MIPGap=0, MIPGapAbs=0, Seed=seed)
            e1, e2, base_info = solve_exact(
                problem, e1_tasks, e2_tasks_by_sat, vehicles_per_satellite,
                TFp=TFp, verbose=verbose, time_limit=max_time * ratio,
                return_info=True, return_incumbent_on_time_limit=True,
                gurobi_params=params,
            )
        else:
            from models.routing.alns.variants.model_3 import solve_alns as solve_m3
            e1, e2, base_info = solve_m3(
                problem, e1_tasks, e2_tasks_by_sat, vehicles_per_satellite,
                TFp=TFp, verbose=verbose, max_time=max_time * ratio,
                max_iterations=max_iterations, seed=seed, return_info=True,
            )
        if e1 is None or e2 is None:
            info = {"model": 8, "status": "NO_BASE_SOLUTION", "objective": None,
                    "proven_optimal": False, "conditional_proven_optimal": False,
                    "stage1": base_info, "runtime": time.perf_counter() - started}
            return (None, None, info) if return_info else (None, None)
        base_routes = (e1, e2)
    base = prepare_baseline(problem, e1_tasks, e2_tasks_by_sat,
                            vehicles_per_satellite, base_routes, TFp)
    stage1 = dict(base_info or {})
    if stage1.get("objective") is not None and not math.isclose(
            stage1["objective"], base["objective"], rel_tol=1e-8, abs_tol=1e-6):
        raise ValueError("M3 证书目标值与基础路线不一致")
    info = initial_info(base, stage1)
    info.update(algorithm="adaptive_relay_mode_search_with_milp_repair",
                requires_gurobi=True, stage1_method=stage1_method, seed=seed,
                iterations=0, accepted_moves=0, improving_moves=0)
    best_routes = (base["e1_routes"], base["e2_routes"])
    if stage1_method == "exact" and not info["stage1_proven_optimal"]:
        info.update(status="BASE_OPTIMUM_NOT_PROVEN",
                    stage2_skipped_reason="M3 最优性未证明；不启动第二阶段",
                    runtime=time.perf_counter() - started)
        return (*best_routes, info) if return_info else best_routes

    pickup_origins = {t.id: t.cust for tasks in e2_tasks_by_sat.values()
                      for t in tasks if t.kind == "p"}
    pickups = sorted(pickup_origins)
    receiver = {n: (sat, idx) for sat, paths in base["e2_routes"].items()
                for idx, path in enumerate(paths) for n in path[1:-1]}
    modes = dict(pickup_origins)
    current_cost = base["objective"]
    weights = [1.0] * 3
    repairs = [1.0, 1.0]
    uses, scores = [0] * 3, [0.0] * 3
    repair_uses, repair_scores = [0, 0], [0.0, 0.0]
    history = []
    stage2_started = time.perf_counter()
    for iteration in range(max_iterations):
        if not pickups or time.perf_counter() >= deadline:
            break
        destroy = rng.choices(range(3), weights=weights)[0]
        repair = rng.choices(range(2), weights=repairs)[0]
        count = rng.randint(1, min(max_remove, len(pickups)))
        if destroy == 0:
            selected = rng.sample(pickups, count)
        elif destroy == 1:
            anchor = rng.choice(pickups)
            related = [p for p in pickups if receiver[p] == receiver[anchor]]
            selected = rng.sample(related, min(count, len(related)))
        else:
            relayed = [p for p in pickups if modes[p] != pickup_origins[p]]
            selected = rng.sample(relayed, min(count, len(relayed)))
            remaining = [p for p in pickups if p not in selected]
            selected += rng.sample(remaining, min(count - len(selected), len(remaining)))
        released = set(selected)
        direct_fixes = {p: 0 for p in pickups if p not in released
                        and modes[p] == pickup_origins[p]}
        handoff_fixes = {p: modes[p] for p in pickups if p not in released
                         and modes[p] != pickup_origins[p]}
        local_deadline = min(deadline, time.perf_counter()
                             + repair_time_limit * (1 if repair == 0 else 3))
        params = dict(gurobi_params or {})
        params["Seed"] = seed + iteration
        result = optimize_stage2(
            problem, e1_tasks, e2_tasks_by_sat, vehicles_per_satellite,
            base, local_deadline, TFp=TFp, gurobi_params=params,
            relay_fixes=direct_fixes, handoff_fixes=handoff_fixes,
        )
        reward = 0.0
        accepted = False
        if result and result.get("feasible"):
            candidate_cost = result["objective"]
            progress = min(1.0, (time.perf_counter() - stage2_started)
                           / max(deadline - stage2_started, 1e-9))
            temperature = max(1e-8, 0.02 * base["objective"] * 0.01 ** progress)
            delta = candidate_cost - current_cost
            accepted = delta <= 0 or rng.random() < math.exp(-delta / temperature)
            if accepted:
                modes, current_cost = result["modes"], candidate_cost
                info["accepted_moves"] += 1
                reward = 3.0 if delta >= -1e-6 else 9.0
            if candidate_cost < info["objective"] - 1e-6:
                best_routes = merge_stage2(info, result) or best_routes
                info["improving_moves"] += 1
                reward = 33.0
            elif (not result["restricted"] and result["conditional_proven_optimal"]
                  and candidate_cost <= info["objective"] + 1e-6):
                best_routes = merge_stage2(info, result) or best_routes
        uses[destroy] += 1
        scores[destroy] += reward
        repair_uses[repair] += 1
        repair_scores[repair] += reward
        info["iterations"] = iteration + 1
        history.append({"iteration": iteration + 1, "destroy": destroy,
                        "repair": repair, "released_pickups": selected,
                        "accepted": accepted,
                        "candidate_objective": None if not result else result.get("objective"),
                        "best_objective": info["objective"]})
        if (iteration + 1) % 10 == 0:
            for w, sc, us in ((weights, scores, uses), (repairs, repair_scores, repair_uses)):
                for k in range(len(w)):
                    if us[k]:
                        w[k] = max(0.1, 0.8 * w[k] + 0.2 * sc[k] / us[k])
                    sc[k], us[k] = 0.0, 0
        if info["conditional_proven_optimal"]:
            break
    info.update(runtime=time.perf_counter() - started,
                destroy_operators=["random_pickups", "same_receiver", "existing_relays"],
                repair_operators=["short_mip", "deep_mip"],
                destroy_weights=weights, repair_weights=repairs,
                search_history=history,
                termination_reason="conditional_optimum" if info["conditional_proven_optimal"]
                else "time_limit" if time.perf_counter() >= deadline else "iteration_limit")
    if verbose:
        print(f"M8 ALNS: fixed M3={base['objective']:.6f}, "
              f"best={info['objective']:.6f}, repairs={info['iterations']}")
    return (*best_routes, info) if return_info else best_routes
