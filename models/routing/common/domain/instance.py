from typing import Dict, List, Optional
import math
from models.routing.common.domain.node import Node, Depot, Satellite, Customer
from models.routing.common.domain.vehicle import FleetConfig
from models.routing.common.operating_policy import OperatingPolicy

class ProblemInstance:
    """
    2E-VRP (Two-Echelon Vehicle Routing Problem) 实例数据管理类。
    负责维护节点数据、车辆配置、需求聚合逻辑及距离/时间矩阵。
    """

    def __init__(self, nodes: Dict[str, Node], fleet_config: Dict[str, float],
                 speed: float = 1.0,
                 distance_matrix: Optional[Dict[str, Dict[str, float]]] = None,
                 travel_time_matrix: Optional[Dict[str, Dict[str, float]]] = None,
                 operating_policy=None):
        # ==========================================
        # 1. 基础节点数据初始化
        # ==========================================
        self.nodes: Dict[str, Node] = nodes
        self.depot: Optional[Depot] = None
        self.satellites: List[Satellite] = []
        self.customers: List[Customer] = []

        # 自动识别并将节点分类（Depot, Satellite, Customer）
        self._identify_node_types()
        self.operating_policy = OperatingPolicy.from_config(
            operating_policy if operating_policy is not None else fleet_config.get("operating_policy"))
        
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
        self.distance_matrix_source = "provided" if distance_matrix is not None else "euclidean"
        self.travel_time_matrix_source = "provided" if travel_time_matrix is not None else "distance/speed"
        self._provided_distance_matrix = distance_matrix
        self._provided_travel_time_matrix = travel_time_matrix
        self._build_matrices()
        self.operating_policy.validate_problem(self)

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
        根据客户分配情况，聚合计算 E1 层卫星仓的各项需求指标。

        - delivery_weight: 物品重量 + 配送容器数 × 单箱重量
        - delivery_volume: 配送容器数 × 单箱体积
        - pickup_weight: 回收容器数 × 单箱重量
        - pickup_volume: 回收容器数 × 单箱体积

        旧数据没有容器数字段时，Customer 默认值为 1，保持历史口径。
        """
        sat_map = {s.id: s for s in self.satellites}
        cw = self.fleet.container_weight
        cv = self.fleet.container_volume

        # 重置卫星仓状态
        for sat in self.satellites:
            sat.delivery_weight = 0.0
            sat.delivery_volume = 0.0
            sat.pickup_weight = 0.0
            sat.pickup_volume = 0.0
            sat.e1_delivery_due_date = float('inf')

        for cust in self.customers:
            assignment = cust.assigned_sat
            if assignment and assignment in sat_map:
                sat = sat_map[assignment]
                delivery_count = max(0.0, cust.container_count_del)
                pickup_count = max(0.0, cust.container_count_pick)

                # 配送重量 = 物品重量 + 容器重量
                sat.delivery_weight += cust.weight_del + delivery_count * cw
                sat.delivery_volume += delivery_count * cv
                sat.pickup_weight += pickup_count * cw
                sat.pickup_volume += pickup_count * cv

                # 时间窗逻辑
                if delivery_count > 0 and cust.tw_del[1] < sat.e1_delivery_due_date:
                    sat.e1_delivery_due_date = cust.tw_del[1]

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
        
        if self._provided_distance_matrix is not None:
            missing = [
                (left, right)
                for left in ids
                for right in ids
                if left not in self._provided_distance_matrix
                or right not in self._provided_distance_matrix[left]
            ]
            if missing:
                raise ValueError(f"距离矩阵缺少节点对: {missing[:5]}")
        if self._provided_travel_time_matrix is not None:
            missing = [
                (left, right)
                for left in ids
                for right in ids
                if left not in self._provided_travel_time_matrix
                or right not in self._provided_travel_time_matrix[left]
            ]
            if missing:
                raise ValueError(f"时间矩阵缺少节点对: {missing[:5]}")

        for i in range(n):
            if self._provided_distance_matrix is not None:
                for j in range(n):
                    self.dist[i][j] = float(
                        self._provided_distance_matrix[ids[i]][ids[j]]
                    )
                    if self._provided_travel_time_matrix is not None:
                        self.time[i][j] = float(
                            self._provided_travel_time_matrix[ids[i]][ids[j]]
                        )
                    else:
                        self.time[i][j] = self.dist[i][j] * inv_speed
                continue
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
