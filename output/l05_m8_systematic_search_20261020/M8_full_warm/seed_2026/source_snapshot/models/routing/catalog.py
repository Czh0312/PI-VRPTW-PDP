"""M1-M4 single batch; M5 fixed multi-batch; M6-M8 joint calendar/pool cost."""
from dataclasses import dataclass

@dataclass(frozen=True)
class ModelSpec:
    number: int
    slug: str
    description: str
    supports_cutoff: bool = False
    supports_multi_batch: bool = False

    def module_path(self, batch_mode, method):
        if batch_mode not in ('single_batch', 'multi_batch'):
            raise ValueError(f'Unknown batch mode: {batch_mode}')
        expected = 'multi_batch' if self.supports_multi_batch else 'single_batch'
        if self.number not in (6, 7, 8) and batch_mode != expected:
            raise ValueError(f'M{self.number} requires {expected}')
        if method not in ('exact', 'alns'):
            raise ValueError(f'Unknown solver method: {method}')
        return f'models.routing.model_{self.number}.{method}'

MODEL_SPECS = {
    1: ModelSpec(1, "fixed_pure_delivery", "固定分仓、一次性包装纯配送；支持 B=1", True),
    2: ModelSpec(2, "free_pure_delivery", "自由分仓、一次性包装纯配送；支持 B=1", True),
    3: ModelSpec(3, "fixed_container_cycle", "单批次、固定分仓、容器循环", True),
    4: ModelSpec(4, "free_container_cycle", "自由分仓、容器循环；支持 B=1", True),
    5: ModelSpec(5, "fixed_multibatch_container_cycle", "多批次、固定分仓、有限容器跨批循环", True, True),
    6: ModelSpec(6, "joint_batch_pool_cost", "均匀周期分批、统一分界与容器路径成本优化；支持历史候选时段", True, True),
    7: ModelSpec(7, "independent_cutoff_cost", "等长批次、逐批独立分界与容器路径成本优化", True, True),
    8: ModelSpec(8, "flexible_batch_cost", "不等长批次、逐批独立分界与容器路径成本优化", True, True),
}

def require_model_number(number):
    if number not in MODEL_SPECS:
        raise ValueError(f"当前模型编号必须为 1–8；M6–M8 是联合分批成本模型，收到: {number}")
    return number
