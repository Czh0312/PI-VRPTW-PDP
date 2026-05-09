# models/model_1/alns_solver.py
# -*- coding: utf-8 -*-
"""
Model 1 (纯配送) 的 ALNS 求解器
两阶段：E1 (depot → satellites) + E2 (satellite → customers)

用法：
    from models.model_1.alns_solver import solve_alns
    e1_routes, e2_routes = solve_alns(problem, e1_tasks, e2_tasks_by_sat,
                                       vehicles_per_satellite)

返回格式与 solve_exact 一致：
    e1_routes: [[depot, sat1, sat2, ..., depot], ...]
    e2_routes: {sat_id: [[sat, c1, c2, ..., sat], ...], ...}
"""
import math
import random
import copy
import time


# =====================================================================
#  数据结构
# =====================================================================

class Route:
    """一条路径：depot/satellite + 中间节点序列"""
    def __init__(self, base_id, nodes=None):
        self.base = base_id          # depot (E1) 或 satellite (E2)
        self.nodes = nodes or []     # 中间访问的任务节点 ID 序列

    def full_path(self):
        return [self.base] + self.nodes + [self.base]

    def copy(self):
        return Route(self.base, list(self.nodes))


class Solution:
    """完整的两阶段解"""
    def __init__(self):
        self.e1_routes = []                    # List[Route]
        self.e2_routes = {}                    # {sat_id: List[Route]}
        self.cost = float('inf')

    def copy(self):
        s = Solution()
        s.e1_routes = [r.copy() for r in self.e1_routes]
        s.e2_routes = {
            sat: [r.copy() for r in routes]
            for sat, routes in self.e2_routes.items()
        }
        s.cost = self.cost
        return s


# =====================================================================
#  可行性检查与目标值计算
# =====================================================================

class Evaluator:
    """评估解的可行性和目标值"""

    def __init__(self, problem, e1_tasks, e2_tasks_by_sat):
        self.problem = problem
        self.e1_task_map = {t.id: t for t in e1_tasks}
        self.e2_task_map = {}
        self.e2_tasks_by_sat = e2_tasks_by_sat
        for sat_id, tasks in e2_tasks_by_sat.items():
            for t in tasks:
                self.e2_task_map[t.id] = t

        # 缓存：任务ID → 物理节点ID
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

    def service_time(self, node_id):
        phys_id = self.phys(node_id)
        node_obj = self.problem.nodes.get(phys_id)
        return getattr(node_obj, 'service_time', 0.0) if node_obj else 0.0

    def route_distance(self, route: Route):
        """计算一条路径的总距离"""
        path = route.full_path()
        total = 0.0
        for i in range(len(path) - 1):
            total += self.dist(path[i], path[i + 1])
        return total

    def total_cost(self, sol: Solution):
        """计算总距离（目标值）"""
        cost = 0.0
        for r in sol.e1_routes:
            cost += self.route_distance(r)
        for sat_id, routes in sol.e2_routes.items():
            for r in routes:
                cost += self.route_distance(r)
        return cost

    def check_e1_route_feasible(self, route: Route):
        """检查 E1 路径可行性：容量 + 时间窗"""
        total_w, total_v = 0.0, 0.0
        for nid in route.nodes:
            t = self.e1_task_map.get(nid)
            if t:
                total_w += t.w
                total_v += t.v
        if total_w > self.problem.e1_weight_cap + 1e-6:
            return False
        if total_v > self.problem.e1_volume_cap + 1e-6:
            return False

        # 时间窗检查
        current_time = 0.0
        prev = route.base
        for nid in route.nodes:
            travel = self.dist(prev, nid)
            current_time += travel
            t = self.e1_task_map.get(nid)
            if t:
                if current_time < t.tw[0]:
                    current_time = t.tw[0]
                if current_time > t.tw[1] + 1e-6:
                    return False
                current_time += self.service_time(nid)
            prev = nid
        return True

    def check_e2_route_feasible(self, route: Route, sat_id):
        """检查 E2 路径可行性：容量 + 时间窗"""
        total_w, total_v = 0.0, 0.0
        for nid in route.nodes:
            t = self.e2_task_map.get(nid)
            if t:
                total_w += t.w
                total_v += t.v
        if total_w > self.problem.e2_weight_cap + 1e-6:
            return False
        if total_v > self.problem.e2_volume_cap + 1e-6:
            return False

        # 时间窗检查（相对时间，从 satellite 出发时刻为 0）
        # 注意：这里不考虑 E1 的衔接时间，简化处理
        # 实际的绝对时间 = A_start + 相对时间
        current_time = 0.0
        prev = sat_id
        for nid in route.nodes:
            travel = self.dist(prev, nid)
            current_time += travel
            t = self.e2_task_map.get(nid)
            if t:
                # 可以等待
                if current_time < t.tw[0]:
                    current_time = t.tw[0]
                # 不能迟到（这里用相对时间窗，简化处理）
                # 严格版本需要加上 A_start 做绝对时间
                if current_time > t.tw[1] + 1e-6:
                    return False
                current_time += self.service_time(nid)
            prev = nid
        return True

    def is_feasible(self, sol: Solution):
        """检查整个解的可行性"""
        # E1：每个 satellite 的 delivery task 都必须被访问
        visited_e1 = set()
        for r in sol.e1_routes:
            if not self.check_e1_route_feasible(r):
                return False
            visited_e1.update(r.nodes)
        for tid in self.e1_task_map:
            if tid not in visited_e1:
                return False

        # E2：每个客户的 delivery task 都必须被访问
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
    """
    贪心构造初始解：
    E1: 每个 satellite delivery task 分配到一辆独立的卡车（最简单）
    E2: 对每个 satellite 的客户，按时间窗排序，贪心插入
    """
    sol = Solution()
    depot_id = problem.depot.id
    sat_ids = [s.id for s in problem.satellites]

    # ---- E1: 简单分配 ----
    # 把所有 E1 delivery tasks 按 satellite 排序，尽量合并到同一辆车
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
            # 防止死循环：单个任务超过容量时强制单独开路径
            forced = remaining.pop(0)
            sol.e1_routes.append(Route(depot_id, [forced.id]))

    # ---- E2: 对每个 satellite 贪心构造 ----
    for sat_id in sat_ids:
        tasks = list(e2_tasks_by_sat.get(sat_id, []))
        if not tasks:
            sol.e2_routes[sat_id] = []
            continue

        # 按时间窗下界排序
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
                # 容量检查
                if cur_w + t.w > problem.e2_weight_cap:
                    continue
                if cur_v + t.v > problem.e2_volume_cap:
                    continue
                # 时间窗检查
                travel = evaluator.dist(prev, t.id)
                arr = cur_time + travel
                if arr < t.tw[0]:
                    arr = t.tw[0]
                if arr > t.tw[1] + 1e-6:
                    continue
                # 可以插入
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
                # 防止死循环：强制把第一个未分配的客户单独开一条路径
                if unassigned:
                    forced = unassigned.pop(0)
                    r = Route(sat_id, [forced.id])
                    routes.append(r)

        sol.e2_routes[sat_id] = routes

    sol.cost = evaluator.total_cost(sol)
    return sol


# =====================================================================
#  破坏算子 (Destroy Operators)
# =====================================================================

def random_removal(sol, evaluator, num_remove, echelon='e2'):
    """随机移除 num_remove 个客户"""
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
    """移除对目标值贡献最大（最浪费）的客户"""
    savings = []
    if echelon == 'e2':
        for sat_id, routes in sol.e2_routes.items():
            for r in routes:
                path = r.full_path()
                for idx, nid in enumerate(r.nodes):
                    pos = idx + 1  # 在 full_path 中的位置
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

    # 按 saving 降序（移除收益最大的）
    savings.sort(key=lambda x: -x[0])
    removed = []
    for saving, sat_id, r, nid in savings[:num_remove]:
        if nid in r.nodes:
            r.nodes.remove(nid)
            removed.append(nid)
    return removed


def shaw_removal(sol, evaluator, num_remove, echelon='e2'):
    """Shaw 移除：移除相似的客户（距离近 + 时间窗接近）"""
    if echelon == 'e2':
        all_nodes = []
        for sat_id, routes in sol.e2_routes.items():
            for r in routes:
                for nid in r.nodes:
                    all_nodes.append((sat_id, r, nid))
        if not all_nodes:
            return []

        # 随机选一个种子
        seed_sat, seed_r, seed_nid = random.choice(all_nodes)
        seed_task = evaluator.e2_task_map.get(seed_nid)

        # 计算与种子的"相似度"
        similarities = []
        for sat_id, r, nid in all_nodes:
            if nid == seed_nid:
                continue
            t = evaluator.e2_task_map.get(nid)
            if not t or not seed_task:
                continue
            dist_sim = evaluator.dist(seed_nid, nid)
            tw_sim = abs(seed_task.tw[0] - t.tw[0])
            score = dist_sim + 0.1 * tw_sim  # 加权
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
#  修复算子 (Repair Operators)
# =====================================================================

def greedy_insertion(sol, removed, evaluator, echelon='e2'):
    """贪心插入：每次找最小插入成本的位置"""
    if echelon == 'e2':
        for nid in removed:
            task = evaluator.e2_task_map.get(nid)
            if not task:
                continue
            # 找出该客户属于哪个 satellite
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
                    # 试着在 pos 位置插入
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
                # 开新路径
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
    """Regret-k 插入：优先插入"选择余地最小"的客户"""
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

                # 收集所有可行插入位置及其成本
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
                    # 开新路径
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

            assert best_route_info is not None
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
        # E1 节点少，直接用贪心就够了
        greedy_insertion(sol, removed, evaluator, echelon='e1')


# =====================================================================
#  清理空路径
# =====================================================================

def clean_empty_routes(sol):
    """移除空路径"""
    sol.e1_routes = [r for r in sol.e1_routes if r.nodes]
    for sat_id in sol.e2_routes:
        sol.e2_routes[sat_id] = [
            r for r in sol.e2_routes[sat_id] if r.nodes
        ]


# =====================================================================
#  ALNS 主循环
# =====================================================================

def solve_alns(problem, e1_tasks, e2_tasks_by_sat, vehicles_per_satellite,
               max_iterations=5000, max_time=120, seed=42, verbose=True):
    """
    ALNS 求解 Model 1 (纯配送)

    参数:
        max_iterations: 最大迭代次数
        max_time: 最大运行时间(秒)
        seed: 随机种子
        verbose: 是否打印过程
    """
    random.seed(seed)
    start_time = time.time()

    evaluator = Evaluator(problem, e1_tasks, e2_tasks_by_sat)

    # ---- 构造初始解 ----
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

    # ---- 算子定义 ----
    destroy_ops = [random_removal, worst_removal, shaw_removal]
    repair_ops = [greedy_insertion, regret_insertion]
    n_destroy = len(destroy_ops)
    n_repair = len(repair_ops)

    # 自适应权重
    destroy_weights = [1.0] * n_destroy
    repair_weights = [1.0] * n_repair
    destroy_scores = [0.0] * n_destroy
    repair_scores = [0.0] * n_repair
    destroy_counts = [0] * n_destroy
    repair_counts = [0] * n_repair

    # 分数更新参数
    sigma1 = 33   # 找到新的全局最优
    sigma2 = 9    # 改进了当前解
    sigma3 = 3    # 接受了更差的解
    decay = 0.8   # 权重衰减

    # 模拟退火参数
    T_start = max(sol.cost * 0.05, 10.0)
    T_end = 0.01
    T = T_start
    cooling_rate = (T_end / T_start) ** (1.0 / max(max_iterations, 1))

    # ---- 统计计数 ----
    total_e2_tasks = sum(len(r.nodes) for routes in sol.e2_routes.values()
                         for r in routes)
    total_e1_tasks = sum(len(r.nodes) for r in sol.e1_routes)

    segment_size = 100
    no_improve_count = 0
    iteration = 0

    for iteration in range(max_iterations):
        if time.time() - start_time > max_time:
            if verbose:
                print(f"\n达到时间限制 {max_time}s")
            break

        # ---- 选择破坏/修复算子（轮盘赌） ----
        d_idx = _roulette(destroy_weights)
        r_idx = _roulette(repair_weights)
        destroy_counts[d_idx] += 1
        repair_counts[r_idx] += 1

        # ---- 决定操作 E1 还是 E2 ----
        # 80% 概率操作 E2（客户多），20% 操作 E1（satellite 少）
        if random.random() < 0.8 and total_e2_tasks > 0:
            echelon = 'e2'
            n_tasks = total_e2_tasks
        else:
            echelon = 'e1'
            n_tasks = total_e1_tasks

        # 移除数量：[1, min(n/3, 15)]
        max_remove = max(2, min(n_tasks // 2, 20))
        num_remove = random.randint(1, max(1, max_remove))

        # ---- 破坏 + 修复 ----
        candidate = sol.copy()
        removed = destroy_ops[d_idx](candidate, evaluator, num_remove, echelon)

        if r_idx == 1:
            repair_ops[r_idx](candidate, removed, evaluator, echelon, k=2)
        else:
            repair_ops[r_idx](candidate, removed, evaluator, echelon)

        clean_empty_routes(candidate)
        candidate.cost = evaluator.total_cost(candidate)

        # ---- 接受准则（模拟退火） ----
        delta = candidate.cost - sol.cost
        accepted = False

        if delta < -1e-6:
            # 改进
            sol = candidate
            accepted = True
            if sol.cost < best_cost - 1e-6:
                best_sol = sol.copy()
                best_cost = sol.cost
                destroy_scores[d_idx] += sigma1
                repair_scores[r_idx] += sigma1
                no_improve_count = 0
                if verbose and iteration % 100 == 0:
                    print(f"  [{iteration:5d}] ★ 新最优: {best_cost:.2f}")
            else:
                destroy_scores[d_idx] += sigma2
                repair_scores[r_idx] += sigma2
        elif T > 1e-10:
            prob = math.exp(-delta / T)
            if random.random() < prob:
                sol = candidate
                accepted = True
                destroy_scores[d_idx] += sigma3
                repair_scores[r_idx] += sigma3

        T *= cooling_rate
        no_improve_count += 1

        # ---- 自适应权重更新 ----
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

    # ---- 输出结果 ----
    elapsed = time.time() - start_time
    if verbose:
        print(f"\n{'=' * 60}")
        print(f"ALNS 求解完成")
        print(f"最优目标值: {best_cost:.2f}")
        print(f"总耗时: {elapsed:.2f} 秒")
        print(f"迭代次数: {min(iteration + 1, max_iterations)}")
        print(f"{'=' * 60}")

    # 转换为 main.py 兼容的输出格式
    e1_routes_out = [r.full_path() for r in best_sol.e1_routes]
    e2_routes_out = {}
    for sat_id, routes in best_sol.e2_routes.items():
        e2_routes_out[sat_id] = [r.full_path() for r in routes]

    _print_solution(best_sol, evaluator)

    return e1_routes_out, e2_routes_out


# =====================================================================
#  辅助函数
# =====================================================================

def _roulette(weights):
    """轮盘赌选择"""
    total = sum(weights)
    if total < 1e-10:
        return random.randint(0, len(weights) - 1)
    r = random.random() * total
    cumsum = 0.0
    for i, w in enumerate(weights):
        cumsum += w
        if cumsum >= r:
            return i
    return len(weights) - 1


def _simulate_load_m1(route, evaluator, task_map, base_id):
    """Model 1 纯配送载重模拟: 返回 [(node, W, V), ...]"""
    init_w = sum(task_map[n].w for n in route.nodes
                 if task_map.get(n))
    init_v = sum(task_map[n].v for n in route.nodes
                 if task_map.get(n))
    W_load, V_load = init_w, init_v
    result = [(base_id, W_load, V_load)]
    for nid in route.nodes:
        t = task_map.get(nid)
        if t:
            W_load -= t.w
            V_load -= t.v
        result.append((nid, W_load, V_load))
    result.append((base_id, W_load, V_load))  # 返回基地
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