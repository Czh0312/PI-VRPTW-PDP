from typing import Optional, Tuple


class Node:
    """代表问题中任意位置的基类。"""
    def __init__(self, node_id: str, node_type: str, x: float, y: float):
        self.id = node_id
        self.type = node_type
        self.x = x
        self.y = y

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(id='{self.id}')"


class Depot(Node):
    """代表中心仓库 (D)。"""
    def __init__(self, node_id: str, x: float, y: float):
        super().__init__(node_id, "d", x, y)


class Satellite(Node):
    """代表前置仓 (S)。"""
    def __init__(self, node_id: str, x: float, y: float, service_time: float = 0.0):
        super().__init__(node_id, "s", x, y)
        self.service_time: float = service_time
        self.delivery_weight: float = 0.0
        self.delivery_volume: float = 0.0
        self.pickup_weight: float = 0.0
        self.pickup_volume: float = 0.0
        self.e1_delivery_due_date: float = float("inf")
        self.e1_pickup_ready_time: float = 9999.0


class Customer(Node):
    """客户：配送 / 回收重量与体积；demand 为 weight_del 的别名（兼容 Model 1）。"""
    def __init__(
        self,
        node_id: str,
        x: float,
        y: float,
        demand: float,
        service_time: float,
        tw_del: Tuple[float, float],
        tw_pick: Tuple[float, float],
        assigned_sat: Optional[str] = None,
        weight_pick: float = 0.0,
        vol_del: float = 1.0,
        vol_pick: float = 1.0,
    ):
        super().__init__(node_id, "c", x, y)
        self.weight_del: float = demand
        self.weight_pick: float = weight_pick
        self.service_time = service_time
        self.vol_del: float = vol_del
        self.vol_pick: float = vol_pick
        self.tw_del = tw_del
        self.tw_pick = tw_pick
        self.assigned_sat = assigned_sat

    @property
    def demand(self) -> float:
        return self.weight_del

    @demand.setter
    def demand(self, value: float) -> None:
        self.weight_del = value


class Task:
    """E1 / E2 任务：配送 'd' 或 取回 'p'。"""
    __slots__ = ("id", "cust", "kind", "w", "v", "tw")

    def __init__(self, id: str, cust: str, kind: str, w: float, v: float, tw: Tuple[float, float]):
        self.id = id
        self.cust = cust
        self.kind = kind
        self.w = float(w)
        self.v = float(v)
        self.tw = (float(tw[0]), float(tw[1]))


class TransshipmentTask(Task):
    """Model 3 横向中转虚拟任务。"""
    def __init__(self, cust_id: str, is_dropoff: bool, amount_w: float, amount_v: float):
        super().__init__(
            id=f"Trans_{cust_id}_{'drop' if is_dropoff else 'pick'}",
            cust=cust_id,
            kind="t",
            w=amount_w,
            v=amount_v,
            tw=(0.0, 99999.0),
        )
        self.is_dropoff = is_dropoff
