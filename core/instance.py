from typing import Dict, List, Optional
import math
from core.node import Node, Depot, Satellite, Customer
from core.vehicle import FleetConfig

class ProblemInstance:
    """
    2E-VRP (Two-Echelon Vehicle Routing Problem) 实例数据管理类。
    负责维护节点数据、车辆配置、需求聚合逻辑及距离/时间矩阵。
    """

    def __init__(self, nodes: Dict[str, Node], fleet_config: Dict[str, float],
                 speed: float = 1.0):
        # ==========================================
        # 1. 基础节点数据初始化
        # ==========================================
        self.nodes: Dict[str, Node] = nodes
        self.depot: Optional[Depot] = None
        self.satellites: List[Satellite] = []
        self.customers: List[Customer] = []

        # 自动识别并将节点分类（Depot, Satellite, Customer）
        self._identify_node_types()
        
        # ==========================================
        # 2. 车辆与容量配置 (E1 & E2)
        # ==========================================
        self.fleet = FleetConfig(fleet_config)

        # 属性平铺：为了兼容算法中对 instance.e1_xxx 的直接调用
        self.e1_vehicle_count = self.fleet.e1_count
        self.e1_weight_cap = self.fleet.e1_weight_cap
        self.e1_volume_cap = self.fleet.e1_volume_cap
        self.e2_weight_cap = self.fleet.e2_weight_cap
        self.e2_volume_cap = self.fleet.e2_volume_cap

        # ==========================================
        # 3. 路径成本预计算 (距离与时间)
        # ==========================================
        self.speed = speed
        self._build_matrices()

    def _identify_node_types(self):
        """遍历 nodes 字典，根据实例类型分发到对应列表"""
        sats, custs = [], []
        for node in self.nodes.values():
            if isinstance(node, Depot):
                self.depot = node
            elif isinstance(node, Satellite):
                sats.append(node)
            elif isinstance(node, Customer):
                custs.append(node)

        if not self.depot:
            raise ValueError("错误：输入数据中未包含仓库 (Depot) 节点。")

        # 排序以保证矩阵索引和算法迭代的确定性
        self.satellites = sorted(sats, key=lambda s: s.id)
        self.customers = sorted(custs, key=lambda c: c.id)

    def aggregate_demands_from_pre_assignment(self):
        """
        核心逻辑：根据 E2 层的客户分配情况，聚合计算 E1 层卫星仓的各项需求指标。
        
        修正点提示：
        - delivery_weight/volume: 对应模型约束中 E1 需运送至卫星仓的载荷总量。
        - e1_delivery_due_date: 决定了 E1 必须在此时间前到达，否则影响后续 E2 配送。
        """
        sat_map = {s.id: s for s in self.satellites}

        # 重置卫星仓状态
        for sat in self.satellites:
            sat.delivery_weight = 0.0
            sat.delivery_volume = 0.0
            sat.pickup_weight = 0.0
            sat.pickup_volume = 0.0
            sat.e1_delivery_due_date = float('inf')
            sat.e1_pickup_ready_time = 0.0 

        for cust in self.customers:
            assignment = cust.assigned_sat
            if assignment and assignment in sat_map:
                sat = sat_map[assignment]

                # --- 需求量聚合 ---
                sat.delivery_weight += cust.weight_del
                sat.delivery_volume += cust.vol_del
                sat.pickup_weight += cust.weight_pick
                sat.pickup_volume += cust.vol_pick

                # --- 时间窗逻辑 (反推 E1 约束) ---
                
                # 配送：E1 必须在客户最晚时间窗前到达卫星仓（此处可根据需要减去 sat->cust 的行驶时间）
                if cust.tw_del[1] < sat.e1_delivery_due_date:
                    sat.e1_delivery_due_date = cust.tw_del[1]

                # 回收：E1 只有在所有回收任务（E2）回到卫星仓后才能开始回收作业
                if cust.weight_pick > 0 and cust.tw_pick[1] > sat.e1_pickup_ready_time:
                    sat.e1_pickup_ready_time = cust.tw_pick[1]

    def _build_matrices(self):
        """
        预计算全节点的距离矩阵 (dist) 和时间矩阵 (time)。
        使用 List[List] 结构以优化高频调用时的性能。
        """
        ids = list(self.nodes.keys())
        n = len(ids)

        # 建立 ID 与 矩阵索引的映射关系
        self.node_index: Dict[str, int] = {nid: i for i, nid in enumerate(ids)}
        self.index_node: List[str] = ids 

        # 初始化 $n \times n$ 矩阵
        self.dist = [[0.0] * n for _ in range(n)]
        self.time = [[0.0] * n for _ in range(n)]

        nodes_list = [self.nodes[nid] for nid in ids]
        inv_speed = 1.0 / self.speed
        
        for i in range(n):
            ni = nodes_list[i]
            for j in range(i + 1, n):
                nj = nodes_list[j]
                # 计算欧式距离
                d = math.sqrt((ni.x - nj.x) ** 2 + (ni.y - nj.y) ** 2)
                t = d * inv_speed
                
                # 对称填充
                self.dist[i][j] = self.dist[j][i] = d
                self.time[i][j] = self.time[j][i] = t

    # ==========================================
    # 4. 数据查询接口
    # ==========================================

    def distance(self, node1_id: str, node2_id: str) -> float:
        """根据节点字符串 ID 查询距离 $d_{ij}$"""
        try:
            return self.dist[self.node_index[node1_id]][self.node_index[node2_id]]
        except KeyError:
            raise KeyError(f"节点 ID 未找到: {node1_id} 或 {node2_id}")

    def travel_time(self, node1_id: str, node2_id: str) -> float:
        """根据节点字符串 ID 查询行驶时间 $t_{ij}$"""
        try:
            return self.time[self.node_index[node1_id]][self.node_index[node2_id]]
        except KeyError:
            raise KeyError(f"节点 ID 未找到: {node1_id} 或 {node2_id}")