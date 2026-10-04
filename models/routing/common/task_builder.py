from models.routing.common.domain.instance import ProblemInstance
from models.routing.common.domain.node import Task


def build_tasks_from_assignment(problem: ProblemInstance, assignment: dict):
    """Build delivery/pickup tasks for an explicit free-assignment partition.

    This follows the Model 4 one-container-per-customer formulation and does
    not mutate the pre-assignment stored on ``ProblemInstance``.
    """
    cw = problem.fleet.container_weight
    cv = problem.fleet.container_volume
    sat_ids = [satellite.id for satellite in problem.satellites]
    e2_tasks_by_sat = {sat_id: [] for sat_id in sat_ids}
    aggregate = {
        sat_id: {
            "count": 0,
            "del_w": 0.0,
            "del_v": 0.0,
            "pick_w": 0.0,
            "pick_v": 0.0,
            "due": float("inf"),
        }
        for sat_id in sat_ids
    }
    for customer in problem.customers:
        sat_id = assignment.get(customer.id)
        if sat_id not in aggregate:
            continue
        values = aggregate[sat_id]
        values["count"] += 1
        values["del_w"] += customer.weight_del + cw
        values["del_v"] += cv
        values["pick_w"] += cw
        values["pick_v"] += cv
        values["due"] = min(values["due"], customer.tw_del[1])
        e2_tasks_by_sat[sat_id].append(Task(
            f"{customer.id}d", customer.id, "d",
            customer.weight_del + cw, cv, customer.tw_del,
        ))
        e2_tasks_by_sat[sat_id].append(Task(
            f"{customer.id}p", customer.id, "p",
            cw, cv, customer.tw_pick,
        ))

    e1_tasks = []
    for sat_id in sat_ids:
        values = aggregate[sat_id]
        if values["count"] <= 0:
            continue
        ready = getattr(problem.nodes[sat_id], "e1_pickup_ready_time", 0.0)
        e1_tasks.append(Task(
            f"{sat_id}d", sat_id, "d",
            values["del_w"], values["del_v"],
            (0.0, values["due"]),
        ))
        e1_tasks.append(Task(
            f"{sat_id}p", sat_id, "p",
            values["pick_w"], values["pick_v"],
            (ready, float("inf")),
        ))
    return e1_tasks, e2_tasks_by_sat


def build_tasks_for_model(problem: ProblemInstance, model_type: int):
    from .operating_policy import policy_for
    from models.routing.catalog import require_model_number
    require_model_number(model_type)
    policy = policy_for(problem)
    policy.require_model(model_type)
    if getattr(policy, 'multi_batch', False):
        raise ValueError('Multibatch tasks require the event-based solver; static E1 pickup totals are not supported')
    if policy.single_day:
        policy.validate_problem(problem)
        problem.aggregate_demands_from_pre_assignment()
    e1_tasks = []
    e2_tasks_by_sat = {s.id: [] for s in problem.satellites}

    cw = problem.fleet.container_weight
    cv = problem.fleet.container_volume

    if model_type in (2, 4):
        # M2/M4 是顾客自由分仓模型，求解器直接从 ProblemInstance
        # 构造任务；这里返回空容器，仅保持统一主入口的调用契约。
        return e1_tasks, e2_tasks_by_sat
    if model_type == 1:
        for s in problem.satellites:
            if s.delivery_weight > 1e-9:
                e1_tasks.append(
                    Task(f"{s.id}d", s.id, "d", s.delivery_weight, s.delivery_volume, (0.0, s.e1_delivery_due_date))
                )
        for c in problem.customers:
            if c.assigned_sat and c.demand > 1e-9:
                delivery_count = max(0.0, c.container_count_del)
                # 配送重量 = 物品 + 容器
                e2_tasks_by_sat[c.assigned_sat].append(
                    Task(
                        f"{c.id}d", c.id, "d",
                        c.demand + delivery_count * cw,
                        delivery_count * cv,
                        c.tw_del,
                    )
                )
    elif model_type == 3:
        for s in problem.satellites:
            if s.delivery_weight > 1e-9 or (policy.single_day and s.delivery_volume > 1e-9):
                e1_tasks.append(
                    Task(f"{s.id}d", s.id, "d", s.delivery_weight, s.delivery_volume, (0.0, s.e1_delivery_due_date))
                )
            if s.pickup_weight > 1e-9 or s.pickup_volume > 1e-9:
                e1_tasks.append(
                    Task(f"{s.id}p", s.id, "p", max(s.pickup_weight, 0.0), max(s.pickup_volume, 0.0),
                         (max(0.0, s.e1_pickup_ready_time), float('inf')))
                )
        for c in problem.customers:
            if not c.assigned_sat:
                continue
            delivery_count = max(0.0, c.container_count_del)
            pickup_count = max(0.0, c.container_count_pick)
            if c.demand > 1e-9 or delivery_count > 1e-9:
                # 配送重量 = 物品 + 容器
                e2_tasks_by_sat[c.assigned_sat].append(
                    Task(
                        f"{c.id}d", c.id, "d",
                        c.demand + delivery_count * cw,
                        delivery_count * cv,
                        c.tw_del,
                    )
                )
            if pickup_count > 1e-9 and (cw > 1e-9 or cv > 1e-9):
                # 回收重量 = 容器
                e2_tasks_by_sat[c.assigned_sat].append(
                    Task(
                        f"{c.id}p", c.id, "p",
                        pickup_count * cw,
                        pickup_count * cv,
                        c.tw_pick,
                    )
                )
    else:
        raise ValueError(f"当前仅支持 model 1–8，收到: {model_type}")

    return e1_tasks, e2_tasks_by_sat
