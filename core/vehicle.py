# core/vehicle.py
from typing import Dict, Optional

class FleetConfig:
    """管理 E1 和 E2 的车队全局配置"""
    def __init__(self, config_dict: Dict[str, float]):
        self.e1_count = int(config_dict.get("E1_vehicle_count", 20))
        self.e1_weight_cap = float(config_dict.get("E1_Qk", 500.0))
        self.e1_volume_cap = float(config_dict.get("E1_Ck", 30.0))
        
        # E2 配置
        self.e2_per_sat_count = int(config_dict.get("vehicles_per_satellite", 30))
        self.e2_weight_cap = float(config_dict.get("E2_Qv", 300.0))
        self.e2_volume_cap = float(config_dict.get("E2_Cv", 5.0))

class Vehicle:
    """表示具体的单个车辆实体（为后续扩展多车型预留）"""
    # 修正点：将 assigned_sat 的类型改为 Optional[str]
    def __init__(self, v_id: str, v_type: str, weight_cap: float, vol_cap: float, assigned_sat: Optional[str] = None):
        self.id = v_id
        self.type = v_type # 'E1' or 'E2'
        self.weight_cap = weight_cap
        self.vol_cap = vol_cap
        self.assigned_sat = assigned_sat # 针对前置仓独立车队的 E2 车辆