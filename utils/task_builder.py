from core.instance import ProblemInstance
from core.node import Task


def build_tasks_for_model(problem: ProblemInstance, model_type: int):
    e1_tasks = []
    e2_tasks_by_sat = {s.id: [] for s in problem.satellites}

    cw = problem.fleet.container_weight
    cv = problem.fleet.container_volume

    if model_type == 1:
        for s in problem.satellites:
            if s.delivery_weight > 1e-9:
                e1_tasks.append(
                    Task(f"{s.id}d", s.id, "d", s.delivery_weight, s.delivery_volume, (0.0, s.e1_delivery_due_date))
                )
        for c in problem.customers:
            if c.assigned_sat and c.demand > 1e-9:
                # 配送重量 = 物品 + 容器
                e2_tasks_by_sat[c.assigned_sat].append(
                    Task(f"{c.id}d", c.id, "d", c.demand + cw, cv, c.tw_del)
                )
    elif model_type in (2, 3, 4, 5):
        for s in problem.satellites:
            if s.delivery_weight > 1e-9:
                e1_tasks.append(
                    Task(f"{s.id}d", s.id, "d", s.delivery_weight, s.delivery_volume, (0.0, s.e1_delivery_due_date))
                )
            if s.pickup_weight > 1e-9 or s.pickup_volume > 1e-9:
                e1_tasks.append(
                    Task(f"{s.id}p", s.id, "p", max(s.pickup_weight, 0.0), max(s.pickup_volume, 0.0),
                         (s.e1_pickup_ready_time, float('inf')))
                )
        for c in problem.customers:
            if not c.assigned_sat:
                continue
            if c.demand > 1e-9:
                # 配送重量 = 物品 + 容器
                e2_tasks_by_sat[c.assigned_sat].append(
                    Task(f"{c.id}d", c.id, "d", c.demand + cw, cv, c.tw_del)
                )
            if cw > 1e-9:
                # 回收重量 = 容器
                e2_tasks_by_sat[c.assigned_sat].append(
                    Task(f"{c.id}p", c.id, "p", cw, cv, c.tw_pick)
                )
    else:
        raise ValueError(f"当前仅支持 model 1/2/3/4/5，收到: {model_type}")

    return e1_tasks, e2_tasks_by_sat
