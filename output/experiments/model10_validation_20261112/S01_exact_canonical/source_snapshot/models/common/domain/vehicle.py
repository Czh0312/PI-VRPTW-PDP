# models/common/domain/vehicle.py
from typing import Dict

class FleetConfig:
    """管理全局基础参数（车队配置 + pickup 单位值）"""
    def __init__(self, config_dict: Dict[str, float]):
        self.e1_count = int(config_dict.get("E1_vehicle_count", 2))
        self.e1_weight_cap = float(config_dict.get("E1_Qk", 500.0))
        self.e1_volume_cap = float(config_dict.get("E1_Ck", 30.0))

        # E2 配置
        self.e2_per_sat_count = int(config_dict.get("vehicles_per_satellite", 4))
        self.e2_weight_cap = float(config_dict.get("E2_Qv", 300.0))
        self.e2_volume_cap = float(config_dict.get("E2_Cv", 5.0))

        # 容器参数（配送时加容器重量，回收时只有容器）
        self.container_weight = float(config_dict.get("container_weight", 0.0))
        self.container_volume = float(config_dict.get("container_volume", 1.0))

        # 行驶速度（speed=1 时 travel_time == distance）
        self.speed = float(config_dict.get("speed", 1.0))
