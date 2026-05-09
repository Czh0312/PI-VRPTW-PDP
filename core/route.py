from typing import List, Dict, Optional
from core.node import Task

class Route:
    """表示一条车辆路径，E1 和 E2 通用。

    数学模型对应:
      E2: 
        - departure_time → t_f^r  (从前置仓出发的绝对时间)
        - arrival_times[i] → t_i^r (相对到达时间)
        - 绝对服务时间 = departure_time + arrival_times[i]
      E1:
        - departure_time → 从总仓库出发的时间
        - arrival_times[i] → t_i^k (到达前置仓 i 的时间)
      通用:
        - load_w_at[i] → Q_i^k / Q_i^r (离开节点 i 后的剩余重量)
        - load_v_at[i] → V_i^k / V_i^r (离开节点 i 后的剩余体积)
    """
    def __init__(self, vehicle_id: int, base_id: str, capacity_w: float, capacity_v: float,
                 echelon: int = 2):
        self.vehicle_id = vehicle_id
        self.base_id = base_id       # E1: depot_id, E2: sat_id
        self.echelon = echelon       # 1 或 2
        self.tasks: List[Task] = []  # 顺序排列的任务
        self.cap_w = capacity_w      # 最大重量容量 Q_max
        self.cap_v = capacity_v      # 最大体积容量 V_max

        # --- 动态状态 (随路径变更而更新) ---
        self.dist = 0.0              # 路径总距离
        self.load_w = 0.0            # 当前总配送载重
        self.load_v = 0.0            # 当前总配送体积

        # 时间追踪
        self.departure_time: float = 0.0       # 从 base 出发的绝对时间
        self.arrival_times: List[float] = []   # 每个 task 的到达时间 (与 tasks 等长)

        # 逐节点载重追踪 (用于模型2双侧约束验证)
        self.load_w_at: List[float] = []       # 离开每个 task 后的剩余重量
        self.load_v_at: List[float] = []       # 离开每个 task 后的剩余体积

        self.is_valid = True         # 可行性标记

    @property
    def sat_id(self) -> str:
        """兼容旧代码中使用 route.sat_id 的地方"""
        return self.base_id

    @sat_id.setter
    def sat_id(self, value: str):
        self.base_id = value

    def clone(self) -> 'Route':
        new_r = Route(self.vehicle_id, self.base_id, self.cap_w, self.cap_v,
                      echelon=self.echelon)
        new_r.tasks = self.tasks[:]
        new_r.load_w = self.load_w
        new_r.load_v = self.load_v
        new_r.dist = self.dist
        new_r.departure_time = self.departure_time
        new_r.arrival_times = self.arrival_times[:]
        new_r.load_w_at = self.load_w_at[:]
        new_r.load_v_at = self.load_v_at[:]
        new_r.is_valid = self.is_valid
        return new_r


class Solution:
    """表示完整的两阶段解"""
    def __init__(self):
        # E1: 卡车路径列表 (与 E2 使用同一个 Route 类)
        self.e1_routes: List[Route] = []
        # E2: sat_id -> List[Route]
        self.e2_routes: Dict[str, List[Route]] = {}
        self.unassigned_tasks: List[Task] = []
        self.cost = float('inf')

    def clone(self) -> 'Solution':
        new_sol = Solution()
        new_sol.e1_routes = [r.clone() for r in self.e1_routes]
        new_sol.e2_routes = {
            s_id: [r.clone() for r in routes]
            for s_id, routes in self.e2_routes.items()
        }
        new_sol.unassigned_tasks = self.unassigned_tasks[:]
        new_sol.cost = self.cost
        return new_sol