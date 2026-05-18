# models/model_1/alns_solver.py
# -*- coding: utf-8 -*-
"""
Model 1 (纯配送) 的 ALNS 求解器
"""
import math
import random
import time

from models.alns_base import (
    Route, Solution, _roulette, clean_empty_routes, FEAS_TOL,
)


# =====================================================================
#  可行性检查与目标值计算
# =====================================================================

class Evaluator:
    def __init__(self, problem, e1_tasks, e2_tasks_by_sat):
        self.problem = problem
        self.e1_task_map = {t.id: t for t in e1_tasks}
        self.e2_task_map = {}
        self.e2_tasks_by_sat = e2_tasks_by_sat
        for sat_id, tasks in e2_tasks_by_sat.items():
            for t in tasks:
                self.e2_task_map[t.id] = t

        self.task_to_phys = {}
        for t in e1_tasks:
            self.task_to_phys[t.id] = t.cust
        for tasks in e2_tasks_by_sat.values():
            for t in tasks:
                self.task_to_phys[t.id] = t.cust

    def phys(self, node_id):
        return self.task_to_phys.get(node_id, node_id)

    def dist(self, a, b):
        return self.problem.distance(self.phys(a), self.phys(b))

    def travel(self, a, b):
        return self.problem.travel_time(self.phys(a), self.phys(b))

    def service_time(self, node_id):
        phys_id = self.phys(node_id)
        node_obj = self.problem.nodes.get(phys_id)
        return getattr(node_obj, 'service_time', 0.0) if node_obj else 0.0

    def route_distance(self, route):
        path = route.full_path()
        total = 0.0
        for i in range(len(path) - 1):
            total += self.dist(path[i], path[i + 1])
        return total

    def total_cost(self, sol):
        cost = 0.0
        for r in sol.e1_routes:
            cost += self.route_distance(r)
        for sat_id, routes in sol.e2_routes.items():
            for r in routes:
                cost += self.route_distance(r)
        return cost

    def check_e1_route_feasible(self, route):
        total_w, total_v = 0.0, 0.0
        for nid in route.nodes:
            t = self.e1_task_map.get(nid)
            if t:
                total_w += t.w
                total_v += t.v
        if total_w > self.problem.e1_weight_cap + FEAS_TOL:
            return False
        if total_v > self.problem.e1_volume_cap + FEAS_TOL:
            return False

        current_time = 0.0
        prev = route.base
        for nid in route.nodes:
            travel = self.travel(prev, nid)
            current_time += travel
            t = self.e1_task_map.get(nid)
            if t:
                if current_time < t.tw[0]:
                    current_time = t.tw[0]
                if current_time > t.tw[1] + FEAS_TOL:
                    return False
                current_time += self.service_time(nid)
            prev = nid
        return True

    def check_e2_route_feasible(self, route, sat_id):
        total_w, total_v = 0.0, 0.0
        for nid in route.nodes:
            t = self.e2_task_map.get(nid)
            if t:
                total_w += t.w
                total_v += t.v
        if total_w > self.problem.e2_weight_cap + FEAS_TOL:
            return False
        if total_v > self.problem.e2_volume_cap + FEAS_TOL:
            return False

        current_time = 0.0
        prev = sat_id
        for nid in route.nodes:
            travel = self.travel(prev, nid)
            current_time += travel
            t = self.e2_task_map.get(nid)
            if t:
                if current_time < t.tw[0]:
                    current_time = t.tw[0]
                if current_time > t.tw[1] + FEAS_TOL:
                    return False
                current_time += self.service_time(nid)
            prev = nid
        return True

    def is_feasible(self, sol):
        visited_e1 = set()
        for r in sol.e1_routes:
            if not self.check_e1_route_feasible(r):
                return False
            visited_e1.update(r.nodes)
        for tid in self.e1_task_map:
            if tid not in visited_e1:
                return False

        for sat_id, routes in sol.e2_routes.items():
            visited_e2 = set()
            for r in routes:
                if not self.check_e2_route_feasible(r, sat_id):
                    return False
                visited_e2.update(r.nodes)
            required = {t.id for t in self.e2_tasks_by_sat.get(sat_id, [])}
            if visited_e2 != required:
                return False
        return True


# =====================================================================
#  初始解构造
# =====================================================================

def build_initial_solution(problem, e1_tasks, e2_tasks_by_sat, evaluator):
    sol = Solution()
    depot_id = problem.depot.id
    sat_ids = [s.id for s in problem.satellites]

    remaining = list(e1_tasks)
    remaining.sort(key=lambda t: evaluator.dist(depot_id, t.cust))

    while remaining:
        route = Route(depot_id)
        cur_w, cur_v = 0.0, 0.0
        to_remove = []
        for t in remaining:
            if (cur_w + t.w <= problem.e1_weight_cap and
                    cur_v + t.v <= problem.e1_volume_cap):
                route.nodes.append(t.id)
                cur_w += t.w
                cur_v += t.v
                to_remove.append(t)
        for t in to_remove:
            remaining.remove(t)
        if route.nodes:
            sol.e1_routes.append(route)
        elif remaining:
            forced = remaining.pop(0)
            sol.e1_routes.append(Route(depot_id, [forced.id]))

    for sat_id in sat_ids:
        tasks = list(e2_tasks_by_sat.get(sat_id, []))
        if not tasks:
            sol.e2_routes[sat_id] = []
            continue

        tasks.sort(key=lambda t: t.tw[0])
        routes = []
        unassigned = list(tasks)

        while unassigned:
            route = Route(sat_id)
            cur_w, cur_v = 0.0, 0.0
            cur_time = 0.0
            prev = sat_id
            assigned_this_route = []

            for t in unassigned:
                if cur_w + t.w > problem.e2_weight_cap:
                    continue
                if cur_v + t.v > problem.e2_volume_cap:
                    continue
                travel = evaluator.travel(prev, t.id)
                arr = cur_time + travel
                if arr < t.tw[0]:
                    arr = t.tw[0]
                if arr > t.tw[1] + FEAS_TOL:
                    continue
                route.nodes.append(t.id)
                cur_w += t.w
                cur_v += t.v
                cur_time = arr + evaluator.service_time(t.id)
                prev = t.id
                assigned_this_route.append(t)

            for t in assigned_this_route:
                unassigned.remove(t)

            if route.nodes:
                routes.append(route)
            else:
                if unassigned:
                    forced = unassigned.pop(0)
                    routes.append(Route(sat_id, [forced.id]))

        sol.e2_routes[sat_id] = routes

    sol.cost = evaluator.total_cost(sol)
    return sol


# =====================================================================
#  破坏算子
# =====================================================================

def random_removal(sol, evaluator, num_remove, echelon='e2'):
    removed = []
    if echelon == 'e2':
        all_nodes = []
        for sat_id, routes in sol.e2_routes.items():
            for r in routes:
                for nid in r.nodes:
                    all_nodes.append((sat_id, r, nid))
        random.shuffle(all_nodes)
        for sat_id, r, nid in all_nodes[:num_remove]:
            if nid in r.nodes:
                r.nodes.remove(nid)
                removed.append(nid)
    elif echelon == 'e1':
        all_nodes = []
        for r in sol.e1_routes:
            for nid in r.nodes:
                all_nodes.append((r, nid))
        random.shuffle(all_nodes)
        for r, nid in all_nodes[:num_remove]:
            if nid in r.nodes:
                r.nodes.remove(nid)
                removed.append(nid)
    return removed


def worst_removal(sol, evaluator, num_remove, echelon='e2'):
    savings = []
    if echelon == 'e2':
        for sat_id, routes in sol.e2_routes.items():
            for r in routes:
                path = r.full_path()
                for idx, nid in enumerate(r.nodes):
                    pos = idx + 1
                    if pos >= len(path) - 1:
                        continue
                    prev_n = path[pos - 1]
                    next_n = path[pos + 1]
                    saving = (evaluator.dist(prev_n, nid) +
                              evaluator.dist(nid, next_n) -
                              evaluator.dist(prev_n, next_n))
                    savings.append((saving, sat_id, r, nid))
    elif echelon == 'e1':
        for r in sol.e1_routes:
            path = r.full_path()
            for idx, nid in enumerate(r.nodes):
                pos = idx + 1
                if pos >= len(path) - 1:
                    continue
                prev_n = path[pos - 1]
                next_n = path[pos + 1]
                saving = (evaluator.dist(prev_n, nid) +
                          evaluator.dist(nid, next_n) -
                          evaluator.dist(prev_n, next_n))
                savings.append((saving, None, r, nid))

    savings.sort(key=lambda x: -x[0])
    removed = []
    for saving, sat_id, r, nid in savings[:num_remove]:
        if nid in r.nodes:
            r.nodes.remove(nid)
            removed.append(nid)
    return removed


def shaw_removal(sol, evaluator, num_remove, echelon='e2'):
    if echelon == 'e2':
        all_nodes = []
        for sat_id, routes in sol.e2_routes.items():
            for r in routes:
                for nid in r.nodes:
                    all_nodes.append((sat_id, r, nid))
        if not all_nodes:
            return []

        seed_sat, seed_r, seed_nid = random.choice(all_nodes)
        seed_task = evaluator.e2_task_map.get(seed_nid)

        similarities = []
        for sat_id, r, nid in all_nodes:
            if nid == seed_nid:
                continue
            t = evaluator.e2_task_map.get(nid)
            if not t or not seed_task:
                continue
            dist_sim = evaluator.dist(seed_nid, nid)
            tw_sim = abs(seed_task.tw[0] - t.tw[0])
            score = dist_sim + 0.1 * tw_sim
            similarities.append((score, sat_id, r, nid))

        similarities.sort(key=lambda x: x[0])
        removed = [seed_nid]
        seed_r.nodes.remove(seed_nid)

        for score, sat_id, r, nid in similarities[:num_remove - 1]:
            if nid in r.nodes:
                r.nodes.remove(nid)
                removed.append(nid)
        return removed
    else:
        return random_removal(sol, evaluator, num_remove, echelon)


# =====================================================================
#  修复算子
# =====================================================================

def greedy_insertion(sol, removed, evaluator, echelon='e2'):
    if echelon == 'e2':
        for nid in removed:
            task = evaluator.e2_task_map.get(nid)
            if not task:
                continue
            sat_id = None
            for s, tasks in evaluator.e2_tasks_by_sat.items():
                if any(t.id == nid for t in tasks):
                    sat_id = s
                    break
            if not sat_id:
                continue

            best_cost = float('inf')
            best_route = None
            best_pos = None

            routes = sol.e2_routes.get(sat_id, [])
            for r in routes:
                for pos in range(len(r.nodes) + 1):
                    old_nodes = r.nodes[:]
                    r.nodes.insert(pos, nid)
                    if evaluator.check_e2_route_feasible(r, sat_id):
                        insert_cost = evaluator.route_distance(r)
                        if insert_cost < best_cost:
                            best_cost = insert_cost
                            best_route = r
                            best_pos = pos
                    r.nodes = old_nodes

            if best_route is not None:
                best_route.nodes.insert(best_pos, nid)
            else:
                new_route = Route(sat_id, [nid])
                if sat_id not in sol.e2_routes:
                    sol.e2_routes[sat_id] = []
                sol.e2_routes[sat_id].append(new_route)

    elif echelon == 'e1':
        depot_id = evaluator.problem.depot.id
        for nid in removed:
            task = evaluator.e1_task_map.get(nid)
            if not task:
                continue

            best_cost = float('inf')
            best_route = None
            best_pos = None

            for r in sol.e1_routes:
                for pos in range(len(r.nodes) + 1):
                    old_nodes = r.nodes[:]
                    r.nodes.insert(pos, nid)
                    if evaluator.check_e1_route_feasible(r):
                        insert_cost = evaluator.route_distance(r)
                        if insert_cost < best_cost:
                            best_cost = insert_cost
                            best_route = r
                            best_pos = pos
                    r.nodes = old_nodes

            if best_route is not None:
                best_route.nodes.insert(best_pos, nid)
            else:
                new_route = Route(depot_id, [nid])
                sol.e1_routes.append(new_route)


def regret_insertion(sol, removed, evaluator, echelon='e2', k=2):
    if echelon == 'e2':
        pending = list(removed)
        while pending:
            best_regret = -float('inf')
            best_nid = None
            best_route_info = None

            for nid in pending:
                task = evaluator.e2_task_map.get(nid)
                if not task:
                    pending.remove(nid)
                    continue

                sat_id = None
                for s, tasks in evaluator.e2_tasks_by_sat.items():
                    if any(t.id == nid for t in tasks):
                        sat_id = s
                        break
                if not sat_id:
                    pending.remove(nid)
                    continue

                insertion_costs = []
                routes = sol.e2_routes.get(sat_id, [])
                for r in routes:
                    for pos in range(len(r.nodes) + 1):
                        old_nodes = r.nodes[:]
                        r.nodes.insert(pos, nid)
                        if evaluator.check_e2_route_feasible(r, sat_id):
                            c = evaluator.route_distance(r)
                            insertion_costs.append((c, r, pos))
                        r.nodes = old_nodes

                if not insertion_costs:
                    insertion_costs.append((
                        evaluator.dist(sat_id, nid) * 2,
                        None, 0
                    ))

                insertion_costs.sort(key=lambda x: x[0])
                regret = 0.0
                for i in range(1, min(k, len(insertion_costs))):
                    regret += insertion_costs[i][0] - insertion_costs[0][0]

                if regret > best_regret:
                    best_regret = regret
                    best_nid = nid
                    best_route_info = insertion_costs[0]

            if best_nid is None:
                break

            if best_route_info is None:
                break
            pending.remove(best_nid)
            _, route, pos = best_route_info
            if route is not None:
                route.nodes.insert(pos, best_nid)
            else:
                sat_id = None
                for s, tasks in evaluator.e2_tasks_by_sat.items():
                    if any(t.id == best_nid for t in tasks):
                        sat_id = s
                        break
                if sat_id:
                    new_route = Route(sat_id, [best_nid])
                    if sat_id not in sol.e2_routes:
                        sol.e2_routes[sat_id] = []
                    sol.e2_routes[sat_id].append(new_route)

    elif echelon == 'e1':
        greedy_insertion(sol, removed, evaluator, echelon='e1')


# =====================================================================
#  ALNS 主循环
# =====================================================================

def solve_alns(problem, e1_tasks, e2_tasks_by_sat, vehicles_per_satellite,
               max_iterations=5000, max_time=120, seed=42, verbose=True):
    random.seed(seed)
    start_time = time.time()

    evaluator = Evaluator(problem, e1_tasks, e2_tasks_by_sat)

    sol = build_initial_solution(problem, e1_tasks, e2_tasks_by_sat, evaluator)
    sol.cost = evaluator.total_cost(sol)
    best_sol = sol.copy()
    best_cost = sol.cost

    if verbose:
        print(f"\n{'=' * 60}")
        print(f"ALNS 求解器 (Model 1 纯配送)")
        print(f"{'=' * 60}")
        print(f"初始解目标值: {sol.cost:.2f}")
        print(f"E1 路径数: {len(sol.e1_routes)}")
        e2_count = sum(len(v) for v in sol.e2_routes.values())
        print(f"E2 路径数: {e2_count}")

    destroy_ops = [random_removal, worst_removal, shaw_removal]
    repair_ops = [greedy_insertion, regret_insertion]
    n_destroy = len(destroy_ops)
    n_repair = len(repair_ops)

    destroy_weights = [1.0] * n_destroy
    repair_weights = [1.0] * n_repair
    destroy_scores = [0.0] * n_destroy
    repair_scores = [0.0] * n_repair
    destroy_counts = [0] * n_destroy
    repair_counts = [0] * n_repair

    sigma1, sigma2, sigma3 = 33, 9, 3
    decay = 0.8

    T_start = max(sol.cost * 0.05, 10.0)
    T_end = 0.01
    T = T_start
    cooling_rate = (T_end / T_start) ** (1.0 / max(max_iterations, 1))

    total_e2_tasks = sum(len(r.nodes) for routes in sol.e2_routes.values()
                         for r in routes)
    total_e1_tasks = sum(len(r.nodes) for r in sol.e1_routes)

    segment_size = 100
    iteration = 0

    for iteration in range(max_iterations):
        if time.time() - start_time > max_time:
            if verbose:
                print(f"\n达到时间限制 {max_time}s")
            break

        d_idx = _roulette(destroy_weights)
        r_idx = _roulette(repair_weights)
        destroy_counts[d_idx] += 1
        repair_counts[r_idx] += 1

        if random.random() < 0.8 and total_e2_tasks > 0:
            echelon = 'e2'
            n_tasks = total_e2_tasks
        else:
            echelon = 'e1'
            n_tasks = total_e1_tasks

        max_remove = max(2, min(n_tasks // 2, 20))
        num_remove = random.randint(1, max(1, max_remove))

        candidate = sol.copy()
        removed = destroy_ops[d_idx](candidate, evaluator, num_remove, echelon)

        if r_idx == 1:
            repair_ops[r_idx](candidate, removed, evaluator, echelon, k=2)
        else:
            repair_ops[r_idx](candidate, removed, evaluator, echelon)

        clean_empty_routes(candidate)
        candidate.cost = evaluator.total_cost(candidate)

        delta = candidate.cost - sol.cost
        if delta < -FEAS_TOL:
            sol = candidate
            if sol.cost < best_cost - FEAS_TOL:
                best_sol = sol.copy()
                best_cost = sol.cost
                destroy_scores[d_idx] += sigma1
                repair_scores[r_idx] += sigma1
                if verbose and iteration % 100 == 0:
                    print(f"  [{iteration:5d}] ★ 新最优: {best_cost:.2f}")
            else:
                destroy_scores[d_idx] += sigma2
                repair_scores[r_idx] += sigma2
        elif T > 1e-10:
            prob = math.exp(-delta / T)
            if random.random() < prob:
                sol = candidate
                destroy_scores[d_idx] += sigma3
                repair_scores[r_idx] += sigma3

        T *= cooling_rate

        if (iteration + 1) % segment_size == 0:
            for i in range(n_destroy):
                if destroy_counts[i] > 0:
                    destroy_weights[i] = (
                        decay * destroy_weights[i] +
                        (1 - decay) * destroy_scores[i] / destroy_counts[i]
                    )
                destroy_scores[i] = 0.0
                destroy_counts[i] = 0
            for i in range(n_repair):
                if repair_counts[i] > 0:
                    repair_weights[i] = (
                        decay * repair_weights[i] +
                        (1 - decay) * repair_scores[i] / repair_counts[i]
                    )
                repair_scores[i] = 0.0
                repair_counts[i] = 0

            if verbose:
                elapsed = time.time() - start_time
                print(f"  [{iteration + 1:5d}] cost={sol.cost:.2f}  "
                      f"best={best_cost:.2f}  T={T:.4f}  "
                      f"time={elapsed:.1f}s")

    elapsed = time.time() - start_time
    if verbose:
        print(f"\n{'=' * 60}")
        print(f"ALNS 求解完成")
        print(f"最优目标值: {best_cost:.2f}")
        print(f"总耗时: {elapsed:.2f} 秒")
        print(f"迭代次数: {min(iteration + 1, max_iterations)}")
        print(f"{'=' * 60}")

    e1_routes_out = [r.full_path() for r in best_sol.e1_routes]
    e2_routes_out = {}
    for sat_id, routes in best_sol.e2_routes.items():
        e2_routes_out[sat_id] = [r.full_path() for r in routes]

    if verbose:
        _print_solution(best_sol, evaluator)

    return e1_routes_out, e2_routes_out


# =====================================================================
#  辅助函数
# =====================================================================

def _simulate_load_m1(route, evaluator, task_map, base_id):
    init_w = sum(task_map[n].w for n in route.nodes if task_map.get(n))
    init_v = sum(task_map[n].v for n in route.nodes if task_map.get(n))
    W_load, V_load = init_w, init_v
    result = [(base_id, W_load, V_load)]
    for nid in route.nodes:
        t = task_map.get(nid)
        if t:
            W_load -= t.w
            V_load -= t.v
        result.append((nid, W_load, V_load))
    result.append((base_id, W_load, V_load))
    return result


def _print_solution(sol, evaluator):
    print("\n--- E1 路线 ---")
    for i, r in enumerate(sol.e1_routes):
        path = r.full_path()
        dist = evaluator.route_distance(r)
        print(f"  卡车 {i}: {' -> '.join(path)}  (距离={dist:.2f})")
        loads = _simulate_load_m1(r, evaluator, evaluator.e1_task_map, r.base)
        load_strs = []
        for idx, (n, Wv, Vv) in enumerate(loads):
            is_first = (idx == 0)
            is_last = (idx == len(loads) - 1)
            tag = n if is_first else f"{n}_arr" if is_last else str(n)
            load_strs.append(f"{tag}(W={Wv:.1f},V={Vv:.1f})")
        print(f"    载量: {' -> '.join(load_strs)}")

    print("\n--- E2 路线 ---")
    for sat_id, routes in sol.e2_routes.items():
        if not routes:
            continue
        print(f"  前置仓 {sat_id}:")
        for j, r in enumerate(routes):
            path = r.full_path()
            dist = evaluator.route_distance(r)
            print(f"    车辆 {j}: {' -> '.join(path)}  (距离={dist:.2f})")
            loads = _simulate_load_m1(r, evaluator, evaluator.e2_task_map, sat_id)
            load_strs = []
            for idx, (n, Wv, Vv) in enumerate(loads):
                is_first = (idx == 0)
                is_last = (idx == len(loads) - 1)
                tag = n if is_first else f"{n}_arr" if is_last else str(n)
                load_strs.append(f"{tag}(W={Wv:.1f},V={Vv:.1f})")
            print(f"      载量: {' -> '.join(load_strs)}")
