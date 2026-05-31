# models/model_4/alns_solver.py
# -*- coding: utf-8 -*-
"""
Model 4 (两阶段：先规划再中转) 的 ALNS 求解器
"""
import math
import random
import time

from models.alns_base import (
    Route, Solution, _roulette, clean_empty_routes, FEAS_TOL,
    is_tfp_active,
)


# =====================================================================
#  评估器
# =====================================================================

class Evaluator:
    def __init__(self, problem, e1_tasks, e2_tasks_by_sat, TFp=9999.0,
                 vehicles_per_satellite=2):
        self.problem = problem
        self.TFp = TFp
        self.vehicles_per_satellite = vehicles_per_satellite
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

        self.e1_del_nodes = [t.id for t in e1_tasks if t.kind == 'd']
        self.e1_pick_nodes = [t.id for t in e1_tasks if t.kind == 'p']

        self.forbidden_e1 = set()
        sat_ids = [s.id for s in problem.satellites]
        for s_id in sat_ids:
            pid, did = f"{s_id}p", f"{s_id}d"
            if pid in self.e1_task_map and did in self.e1_task_map:
                self.forbidden_e1.add((pid, did))

    def signed_demand(self, task):
        return task.w if task.kind == 'd' else -task.w

    def signed_volume(self, task):
        return task.v if task.kind == 'd' else -task.v

    def phys(self, node_id):
        if node_id.startswith("TRANS_PICK_"):
            return node_id[len("TRANS_PICK_"):]
        if node_id.startswith("TRANS_DROP_"):
            return node_id[len("TRANS_DROP_"):]
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
        path = route.full_path()
        for i in range(len(path) - 1):
            if (path[i], path[i + 1]) in self.forbidden_e1:
                return False

        init_w = 0.0
        init_v = 0.0
        for nid in route.nodes:
            t = self.e1_task_map.get(nid)
            if t and t.kind == 'd':
                init_w += t.w
                init_v += t.v

        if init_w > self.problem.e1_weight_cap + FEAS_TOL:
            return False
        if init_v > self.problem.e1_volume_cap + FEAS_TOL:
            return False

        Q_load, V_load = init_w, init_v
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
                if t.kind == 'p' and is_tfp_active(self.TFp) and current_time < self.TFp - FEAS_TOL:
                    return False

                Q_load -= self.signed_demand(t)
                V_load -= self.signed_volume(t)

                if Q_load < -FEAS_TOL or Q_load > self.problem.e1_weight_cap + FEAS_TOL:
                    return False
                if V_load < -FEAS_TOL or V_load > self.problem.e1_volume_cap + FEAS_TOL:
                    return False

                current_time += self.service_time(nid)
            prev = nid
        return True

    def check_e2_route_feasible(self, route, sat_id):
        init_w = 0.0
        init_v = 0.0
        for nid in route.nodes:
            if nid.startswith("TRANS_"):
                continue
            t = self.e2_task_map.get(nid)
            if t and t.kind == 'd':
                init_w += t.w
                init_v += t.v

        if init_w > self.problem.e2_weight_cap + FEAS_TOL:
            return False
        if init_v > self.problem.e2_volume_cap + FEAS_TOL:
            return False

        Q_load, V_load = init_w, init_v
        current_time = 0.0
        prev = sat_id

        for nid in route.nodes:
            travel = self.travel(prev, nid)
            current_time += travel

            if nid.startswith("TRANS_PICK_"):
                tw, tv = route.transport_loads.get(nid, (0.0, 0.0))
                Q_load += tw
                V_load += tv
                if Q_load > self.problem.e2_weight_cap + FEAS_TOL:
                    return False
                if V_load > self.problem.e2_volume_cap + FEAS_TOL:
                    return False
                current_time += self.service_time(nid)
            elif nid.startswith("TRANS_DROP_"):
                tw, tv = route.transport_loads.get(nid, (0.0, 0.0))
                Q_load -= tw
                V_load -= tv
                if Q_load < -FEAS_TOL:
                    return False
                if V_load < -FEAS_TOL:
                    return False
                current_time += self.service_time(nid)
            else:
                t = self.e2_task_map.get(nid)
                if t:
                    if current_time < t.tw[0]:
                        current_time = t.tw[0]
                    if current_time > t.tw[1] + FEAS_TOL:
                        return False
                    if t.kind == 'p' and is_tfp_active(self.TFp) and current_time < self.TFp - FEAS_TOL:
                        return False

                    Q_load -= self.signed_demand(t)
                    V_load -= self.signed_volume(t)

                    if Q_load < -FEAS_TOL or Q_load > self.problem.e2_weight_cap + FEAS_TOL:
                        return False
                    if V_load < -FEAS_TOL or V_load > self.problem.e2_volume_cap + FEAS_TOL:
                        return False

                    current_time += self.service_time(nid)
            prev = nid
        return True

    def is_feasible(self, sol):
        if len(sol.e1_routes) > self.problem.e1_vehicle_count:
            return False

        visited_e1 = []
        for r in sol.e1_routes:
            if not self.check_e1_route_feasible(r):
                return False
            visited_e1.extend(r.nodes)
        required_e1 = set(self.e1_task_map)
        if set(visited_e1) != required_e1:
            return False
        if len(visited_e1) != len(required_e1):
            return False

        for sat_id, routes in sol.e2_routes.items():
            if len(routes) > self.vehicles_per_satellite:
                return False
            visited_e2 = []
            for r in routes:
                if not self.check_e2_route_feasible(r, sat_id):
                    return False
                visited_e2.extend(n for n in r.nodes
                                  if not n.startswith("TRANS_"))
            required = {t.id for t in self.e2_tasks_by_sat.get(sat_id, [])}
            if set(visited_e2) != required:
                return False
            if len(visited_e2) != len(required):
                return False
        return True


# =====================================================================
#  初始解构造
# =====================================================================

def build_initial_solution(problem, e1_tasks, e2_tasks_by_sat, evaluator):
    sol = Solution()
    depot_id = problem.depot.id
    sat_ids = [s.id for s in problem.satellites]

    del_tasks = [t for t in e1_tasks if t.kind == 'd']
    pick_tasks = [t for t in e1_tasks if t.kind == 'p']

    all_nodes = [t.id for t in del_tasks] + [t.id for t in pick_tasks]
    single_route = Route(depot_id, all_nodes)
    if evaluator.check_e1_route_feasible(single_route):
        sol.e1_routes = [single_route]
    else:
        remaining_del = list(del_tasks)
        remaining_del.sort(key=lambda t: evaluator.dist(depot_id, t.cust))
        routes = []
        while remaining_del:
            route = Route(depot_id)
            cur_w, cur_v = 0.0, 0.0
            to_remove = []
            for t in remaining_del:
                if (cur_w + t.w <= problem.e1_weight_cap and
                        cur_v + t.v <= problem.e1_volume_cap):
                    route.nodes.append(t.id)
                    cur_w += t.w
                    cur_v += t.v
                    to_remove.append(t)
            for t in to_remove:
                remaining_del.remove(t)
            if route.nodes:
                routes.append(route)
            elif remaining_del:
                forced = remaining_del.pop(0)
                routes.append(Route(depot_id, [forced.id]))

        for t in pick_tasks:
            inserted = False
            for r in routes:
                r.nodes.append(t.id)
                if evaluator.check_e1_route_feasible(r):
                    inserted = True
                    break
                r.nodes.pop()
            if not inserted:
                routes.append(Route(depot_id, [t.id]))
        sol.e1_routes = routes

    for sat_id in sat_ids:
        tasks = list(evaluator.e2_tasks_by_sat.get(sat_id, []))
        if not tasks:
            sol.e2_routes[sat_id] = []
            continue

        del_t = [t for t in tasks if t.kind == 'd']
        pick_t = [t for t in tasks if t.kind == 'p']
        del_t.sort(key=lambda t: t.tw[0])
        pick_t.sort(key=lambda t: t.tw[0])

        all_sorted = del_t + pick_t
        routes = []
        unassigned = list(all_sorted)

        while unassigned:
            route = Route(sat_id)
            assigned = []
            for t in unassigned:
                test_route = Route(sat_id, route.nodes + [t.id])
                if evaluator.check_e2_route_feasible(test_route, sat_id):
                    route.nodes.append(t.id)
                    assigned.append(t)
            for t in assigned:
                unassigned.remove(t)
            if route.nodes:
                routes.append(route)
            elif unassigned:
                forced = unassigned.pop(0)
                routes.append(Route(sat_id, [forced.id]))

        sol.e2_routes[sat_id] = routes

    sol.cost = evaluator.total_cost(sol)
    sol.task_to_phys = dict(evaluator.task_to_phys)
    return sol


# =====================================================================
#  破坏/修复算子（同 Model 2，无中转）
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
            type_sim = 0.0 if seed_task.kind == t.kind else 50.0
            score = dist_sim + 0.1 * tw_sim + type_sim
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
                routes = sol.e2_routes.get(sat_id, [])
                max_v = evaluator.vehicles_per_satellite
                if len(routes) >= max_v:
                    best_r2, best_p2, best_c2 = None, 0, float('inf')
                    for r in routes:
                        for p in range(len(r.nodes) + 1):
                            old = r.nodes[:]
                            r.nodes.insert(p, nid)
                            c = evaluator.route_distance(r)
                            r.nodes = old
                            if c < best_c2:
                                best_c2, best_r2, best_p2 = c, r, p
                    if best_r2:
                        best_r2.nodes.insert(best_p2, nid)
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
                sol.e1_routes.append(Route(depot_id, [nid]))


def regret_insertion(sol, removed, evaluator, echelon='e2', k=2):
    if echelon == 'e2':
        pending = list(removed)
        while pending:
            best_regret = -float('inf')
            best_nid = None
            best_route_info = None

            for nid in list(pending):
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
                        evaluator.dist(sat_id, nid) * 2, None, 0))

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
                    routes = sol.e2_routes.get(sat_id, [])
                    max_v = evaluator.vehicles_per_satellite
                    if len(routes) >= max_v:
                        best_r2, best_p2, best_c2 = None, 0, float('inf')
                        for r in routes:
                            for p in range(len(r.nodes) + 1):
                                old = r.nodes[:]
                                r.nodes.insert(p, best_nid)
                                c = evaluator.route_distance(r)
                                r.nodes = old
                                if c < best_c2:
                                    best_c2, best_r2, best_p2 = c, r, p
                        if best_r2:
                            best_r2.nodes.insert(best_p2, best_nid)
                    else:
                        new_route = Route(sat_id, [best_nid])
                        if sat_id not in sol.e2_routes:
                            sol.e2_routes[sat_id] = []
                        sol.e2_routes[sat_id].append(new_route)

    elif echelon == 'e1':
        greedy_insertion(sol, removed, evaluator, echelon='e1')


# =====================================================================
#  阶段二：后处理中转优化
# =====================================================================

def postprocess_transshipment(sol, evaluator, verbose=True):
    iteration = 0
    while True:
        iteration += 1
        evaluator.task_to_phys = dict(sol.task_to_phys)
        improved = _search_and_apply_relocation(sol, evaluator)
        if not improved:
            break
        if verbose:
            sol.cost = evaluator.total_cost(sol)
            print(f"  中转迭代 {iteration}: cost={sol.cost:.2f}, "
                  f"中转数={len(sol.transshipments)}")

    sol.cost = evaluator.total_cost(sol)
    sol.task_to_phys = dict(evaluator.task_to_phys)


def _search_and_apply_relocation(sol, evaluator):
    if not evaluator.is_feasible(sol):
        return False

    t_start = time.time()
    time_budget = 2.0

    best_plan = None

    relocated = {k[2] for k in sol.transshipments}

    for sat_id, routes in sol.e2_routes.items():
        if time.time() - t_start > time_budget:
            break

        all_tasks = evaluator.e2_tasks_by_sat.get(sat_id, [])
        all_phys = list(set(t.cust for t in all_tasks))

        pickups = []
        for ben_idx, route in enumerate(routes):
            for pick_node in route.nodes:
                if pick_node.startswith("TRANS_"):
                    continue
                t = evaluator.e2_task_map.get(pick_node)
                if t and t.kind == 'p' and pick_node not in relocated:
                    pickups.append((ben_idx, pick_node, t))

        if not pickups:
            continue
        if len(pickups) > 5:
            pickups = random.sample(pickups, 5)

        for ben_idx, pick_node, t in pickups:
            if time.time() - t_start > time_budget:
                break

            old_phys = evaluator.phys(pick_node)
            ben_route = sol.e2_routes[sat_id][ben_idx]
            old_ben_dist = evaluator.route_distance(ben_route)

            candidates = [p for p in all_phys if p != old_phys]
            if len(candidates) > 3:
                candidates = random.sample(candidates, 3)

            for new_phys in candidates:
                evaluator.task_to_phys[pick_node] = new_phys
                try:
                    if not evaluator.check_e2_route_feasible(ben_route, sat_id):
                        continue
                    new_ben_dist = evaluator.route_distance(ben_route)
                    v2_saving = old_ben_dist - new_ben_dist
                finally:
                    evaluator.task_to_phys[pick_node] = old_phys

                if v2_saving <= 0.01:
                    continue

                carrier_list = [(ci, cr) for ci, cr in enumerate(routes)
                                if ci != ben_idx]
                if len(carrier_list) > 3:
                    carrier_list = random.sample(carrier_list, 3)

                for carrier_idx, carrier_route in carrier_list:
                    if time.time() - t_start > time_budget:
                        break

                    old_cd = evaluator.route_distance(carrier_route)
                    pick_vnode = f"TRANS_PICK_{old_phys}"
                    drop_vnode = f"TRANS_DROP_{new_phys}"

                    pick_positions = _quick_best_positions(
                        carrier_route.nodes, carrier_route.base,
                        pick_vnode, evaluator, top_k=3)

                    for pp in pick_positions:
                        if time.time() - t_start > time_budget:
                            break

                        saved_nodes = carrier_route.nodes[:]
                        saved_loads = dict(carrier_route.transport_loads)
                        carrier_route.nodes.insert(pp, pick_vnode)
                        carrier_route.transport_loads[pick_vnode] = (t.w, t.v)

                        drop_positions = _quick_best_positions(
                            carrier_route.nodes, carrier_route.base,
                            drop_vnode, evaluator, top_k=3,
                            start_after=pp)

                        for dp in drop_positions:
                            old_nodes2 = carrier_route.nodes[:]
                            old_loads2 = dict(carrier_route.transport_loads)

                            carrier_route.nodes.insert(dp, drop_vnode)
                            carrier_route.transport_loads[drop_vnode] = (t.w, t.v)

                            if evaluator.check_e2_route_feasible(
                                    carrier_route, sat_id):
                                new_cd = evaluator.route_distance(carrier_route)
                                net = v2_saving - (new_cd - old_cd)

                                if (best_plan is None
                                        or net > best_plan[0]):
                                    best_plan = (
                                        net, sat_id, ben_idx,
                                        pick_node, new_phys,
                                        carrier_idx, pp, dp)

                            carrier_route.nodes[:] = old_nodes2
                            carrier_route.transport_loads = old_loads2

                        carrier_route.nodes[:] = saved_nodes
                        carrier_route.transport_loads = saved_loads

    if best_plan and best_plan[0] > 0.01:
        (net_saving, sat_id, ben_idx, pick_node, new_phys,
         carrier_idx, pick_pos, drop_pos) = best_plan

        old_phys = evaluator.phys(pick_node)
        t = evaluator.e2_task_map[pick_node]

        evaluator.task_to_phys[pick_node] = new_phys

        carrier_route = sol.e2_routes[sat_id][carrier_idx]
        pick_vnode = f"TRANS_PICK_{old_phys}"
        drop_vnode = f"TRANS_DROP_{new_phys}"

        carrier_route.nodes.insert(pick_pos, pick_vnode)
        carrier_route.transport_loads[pick_vnode] = (t.w, t.v)

        carrier_route.nodes.insert(drop_pos, drop_vnode)
        carrier_route.transport_loads[drop_vnode] = (t.w, t.v)

        sol.transshipments[(sat_id, ben_idx, pick_node, new_phys)] = (
            old_phys, carrier_idx, t.w, t.v)
        sol.task_to_phys = dict(evaluator.task_to_phys)
        return True

    return False


def _quick_best_positions(nodes, base, vnode, evaluator, top_k=3,
                          start_after=-1):
    v_phys = evaluator.phys(vnode)
    path = [base] + nodes + [base]
    positions = []

    for pos in range(start_after + 1, len(nodes) + 1):
        prev_phys = evaluator.phys(path[pos])
        next_phys = evaluator.phys(path[pos + 1])
        detour = (evaluator.problem.distance(prev_phys, v_phys)
                  + evaluator.problem.distance(v_phys, next_phys)
                  - evaluator.problem.distance(prev_phys, next_phys))
        positions.append((detour, pos))

    positions.sort(key=lambda x: x[0])
    return [p for _, p in positions[:top_k]]


def _path_with_relocations(route, sol, evaluator):
    path = route.full_path()
    out = []
    for nid in path:
        if nid.startswith("TRANS_"):
            out.append(nid)
            continue
        phys = sol.task_to_phys.get(nid)
        default_phys = nid[:-1] if nid.endswith(('d', 'p')) else nid
        if phys and phys != default_phys and phys in evaluator.problem.nodes:
            out.append(phys)
        else:
            out.append(nid)
    return out


def _removal_saving(route, node_id, evaluator):
    path = route.full_path()
    node_pos_in_full = None
    for idx in range(1, len(path) - 1):
        if path[idx] == node_id:
            node_pos_in_full = idx
            break
    if node_pos_in_full is None:
        return 0.0

    prev_n = path[node_pos_in_full - 1]
    next_n = path[node_pos_in_full + 1]
    return (evaluator.dist(prev_n, node_id) + evaluator.dist(node_id, next_n)
            - evaluator.dist(prev_n, next_n))


# =====================================================================
#  主求解函数
# =====================================================================

def solve_alns(problem, e1_tasks, e2_tasks_by_sat, vehicles_per_satellite,
               TFp=9999.0, max_iterations=5000, max_time=120,
               seed=42, verbose=True):
    random.seed(seed)
    start_time = time.time()

    evaluator = Evaluator(problem, e1_tasks, e2_tasks_by_sat, TFp,
                          vehicles_per_satellite=vehicles_per_satellite)

    sol = build_initial_solution(problem, e1_tasks, e2_tasks_by_sat, evaluator)
    sol.cost = evaluator.total_cost(sol)
    best_sol = sol.copy()
    best_cost = sol.cost

    if verbose:
        print(f"\n{'=' * 60}")
        print(f"ALNS 求解器 (Model 4 两阶段：先规划再中转)")
        print(f"{'=' * 60}")
        print(f"初始解目标值: {sol.cost:.2f}")
        print(f"初始解可行性: {evaluator.is_feasible(sol)}")
        print(f"E1 路径数: {len(sol.e1_routes)}")
        e2_count = sum(len(v) for v in sol.e2_routes.values())
        print(f"E2 路径数: {e2_count}")

    if verbose:
        print(f"\n--- 阶段一：Model 2 ALNS (无中转) ---")

    destroy_ops = [random_removal, worst_removal, shaw_removal]
    repair_ops = [greedy_insertion, regret_insertion]
    n_d, n_r = len(destroy_ops), len(repair_ops)

    d_weights = [1.0] * n_d
    r_weights = [1.0] * n_r
    d_scores = [0.0] * n_d
    r_scores = [0.0] * n_r
    d_counts = [0] * n_d
    r_counts = [0] * n_r

    sigma1, sigma2, sigma3 = 33, 9, 3
    decay = 0.8

    T_start = max(sol.cost * 0.05, 10.0)
    T_end = 0.01
    T = T_start
    cooling_rate = (T_end / T_start) ** (1.0 / max(max_iterations, 1))

    total_e2 = sum(len(r.nodes) for routes in sol.e2_routes.values() for r in routes)
    total_e1 = sum(len(r.nodes) for r in sol.e1_routes)

    segment_size = 100

    for iteration in range(max_iterations):
        if time.time() - start_time > max_time * 0.8:
            if verbose and iteration == 0:
                print(f"  阶段一时间接近限制，停止")
            break

        d_idx = _roulette(d_weights)
        r_idx = _roulette(r_weights)
        d_counts[d_idx] += 1
        r_counts[r_idx] += 1

        if random.random() < 0.8 and total_e2 > 0:
            echelon = 'e2'
            n_tasks = total_e2
        else:
            echelon = 'e1'
            n_tasks = total_e1

        max_remove = max(2, min(n_tasks // 2, 20))
        num_remove = random.randint(1, max(1, max_remove))

        candidate = sol.copy()
        removed = destroy_ops[d_idx](candidate, evaluator, num_remove, echelon)

        if r_idx == 1:
            repair_ops[r_idx](candidate, removed, evaluator, echelon, k=2)
        else:
            repair_ops[r_idx](candidate, removed, evaluator, echelon)

        clean_empty_routes(candidate)
        if not evaluator.is_feasible(candidate):
            T *= cooling_rate
            continue
        candidate.cost = evaluator.total_cost(candidate)

        delta = candidate.cost - sol.cost
        if delta < -FEAS_TOL:
            sol = candidate
            if sol.cost < best_cost - FEAS_TOL:
                best_sol = sol.copy()
                best_cost = sol.cost
                d_scores[d_idx] += sigma1
                r_scores[r_idx] += sigma1
                if verbose and iteration % 100 == 0:
                    print(f"  [{iteration:5d}] ★ 新最优: {best_cost:.2f}")
            else:
                d_scores[d_idx] += sigma2
                r_scores[r_idx] += sigma2
        elif T > 1e-10:
            prob = math.exp(-delta / T)
            if random.random() < prob:
                sol = candidate
                d_scores[d_idx] += sigma3
                r_scores[r_idx] += sigma3

        T *= cooling_rate

        if (iteration + 1) % segment_size == 0:
            for i in range(n_d):
                if d_counts[i] > 0:
                    d_weights[i] = (decay * d_weights[i] +
                                    (1 - decay) * d_scores[i] / d_counts[i])
                d_scores[i] = 0.0
                d_counts[i] = 0
            for i in range(n_r):
                if r_counts[i] > 0:
                    r_weights[i] = (decay * r_weights[i] +
                                    (1 - decay) * r_scores[i] / r_counts[i])
                r_scores[i] = 0.0
                r_counts[i] = 0
            if verbose:
                elapsed = time.time() - start_time
                print(f"  [{iteration + 1:5d}] cost={sol.cost:.2f}  "
                      f"best={best_cost:.2f}  T={T:.4f}  "
                      f"time={elapsed:.1f}s")

    stage1_cost = best_cost
    if verbose:
        elapsed = time.time() - start_time
        print(f"\n  阶段一完成: cost={best_cost:.2f}, 耗时={elapsed:.2f}s")

    if verbose:
        print(f"\n--- 阶段二：后处理中转搜索 ---")

    evaluator.task_to_phys = dict(best_sol.task_to_phys)
    postprocess_transshipment(best_sol, evaluator, verbose=verbose)

    if verbose:
        print(f"\n  阶段二完成: cost={best_sol.cost:.2f} "
              f"(改进: {stage1_cost - best_sol.cost:.2f})")
        print(f"  中转操作数: {len(best_sol.transshipments)}")

    elapsed = time.time() - start_time
    if verbose:
        print(f"\n{'=' * 60}")
        print(f"Model 4 求解完成")
        print(f"阶段一(无中转): {stage1_cost:.2f}")
        print(f"阶段二(中转后): {best_sol.cost:.2f}")
        print(f"中转操作数: {len(best_sol.transshipments)}")
        print(f"总耗时: {elapsed:.2f} 秒")
        print(f"{'=' * 60}")

    evaluator.task_to_phys = dict(best_sol.task_to_phys)
    e1_routes_out = [
        _path_with_relocations(r, best_sol, evaluator)
        for r in best_sol.e1_routes
    ]
    e2_routes_out = {}
    for sat_id, routes in best_sol.e2_routes.items():
        e2_routes_out[sat_id] = [
            _path_with_relocations(r, best_sol, evaluator)
            for r in routes
        ]

    if verbose:
        _print_solution(best_sol, evaluator)
    return e1_routes_out, e2_routes_out


# =====================================================================
#  载量模拟和打印
# =====================================================================

def _simulate_e1_load(route, evaluator):
    init_w = sum(evaluator.e1_task_map[n].w
                 for n in route.nodes if evaluator.e1_task_map.get(n) and
                 evaluator.e1_task_map[n].kind == 'd')
    init_v = sum(evaluator.e1_task_map[n].v
                 for n in route.nodes if evaluator.e1_task_map.get(n) and
                 evaluator.e1_task_map[n].kind == 'd')
    Q_load, V_load = init_w, init_v
    result = [(route.base, Q_load, V_load)]
    for nid in route.nodes:
        t = evaluator.e1_task_map.get(nid)
        if t:
            Q_load -= evaluator.signed_demand(t)
            V_load -= evaluator.signed_volume(t)
        result.append((nid, Q_load, V_load))
    result.append((route.base, Q_load, V_load))
    return result


def _simulate_e2_load(route, sat_id, evaluator):
    init_w = sum(evaluator.e2_task_map[n].w
                 for n in route.nodes
                 if not n.startswith("TRANS_")
                 and evaluator.e2_task_map.get(n)
                 and evaluator.e2_task_map[n].kind == 'd')
    init_v = sum(evaluator.e2_task_map[n].v
                 for n in route.nodes
                 if not n.startswith("TRANS_")
                 and evaluator.e2_task_map.get(n)
                 and evaluator.e2_task_map[n].kind == 'd')
    Q_load, V_load = init_w, init_v
    result = [(sat_id, Q_load, V_load)]
    for nid in route.nodes:
        if nid.startswith("TRANS_PICK_"):
            tw, tv = route.transport_loads.get(nid, (0.0, 0.0))
            Q_load += tw
            V_load += tv
        elif nid.startswith("TRANS_DROP_"):
            tw, tv = route.transport_loads.get(nid, (0.0, 0.0))
            Q_load -= tw
            V_load -= tv
        else:
            t = evaluator.e2_task_map.get(nid)
            if t:
                Q_load -= evaluator.signed_demand(t)
                V_load -= evaluator.signed_volume(t)
        result.append((nid, Q_load, V_load))
    result.append((sat_id, Q_load, V_load))
    return result


def _print_solution(sol, evaluator):
    print("\n--- E1 路线 ---")
    for i, r in enumerate(sol.e1_routes):
        path = r.full_path()
        dist = evaluator.route_distance(r)
        print(f"  卡车 {i}: {' -> '.join(path)}  (距离={dist:.2f})")
        loads = _simulate_e1_load(r, evaluator)
        load_strs = []
        for idx, (n, Qv, Vv) in enumerate(loads):
            is_first = (idx == 0)
            is_last = (idx == len(loads) - 1)
            tag = n if is_first else f"{n}_arr" if is_last else str(n)
            load_strs.append(f"{tag}(Q={Qv:.1f},V={Vv:.1f})")
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
            loads = _simulate_e2_load(r, sat_id, evaluator)
            load_strs = []
            for idx, (n, Qv, Vv) in enumerate(loads):
                is_first = (idx == 0)
                is_last = (idx == len(loads) - 1)
                tag = n if is_first else f"{n}_arr" if is_last else str(n)
                load_strs.append(f"{tag}(Q={Qv:.1f},V={Vv:.1f})")
            print(f"      载量: {' -> '.join(load_strs)}")

    if sol.transshipments:
        print("\n--- 中转操作 (搬运 + 取货位置变更) ---")
        for (sat_id, ben_idx, pick_node, new_phys), (
                old_phys, carrier_idx, cw, cv) in sol.transshipments.items():
            print(f"  sat={sat_id}: 搬运车{carrier_idx} "
                  f"取容器@{old_phys} → 放容器@{new_phys}, "
                  f"取货车{ben_idx} {pick_node}: "
                  f"取货位置 {old_phys} → {new_phys} "
                  f"(w={cw:.1f}, v={cv:.1f})")
