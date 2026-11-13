"""The six public routing models."""
from dataclasses import dataclass

@dataclass(frozen=True)
class ModelSpec:
    number: int
    slug: str
    description: str
    supports_cutoff: bool = False
    supports_multi_batch: bool = False
    supported_solvers: tuple = ('exact', 'alns')

    def module_path(self, batch_mode, method):
        if batch_mode not in ('single_batch', 'multi_batch', 'no_batch'):
            raise ValueError(f'Unknown batch mode: {batch_mode}')
        allowed = {6: ('no_batch',), 5: ('single_batch', 'multi_batch')}.get(self.number, ('single_batch',))
        if batch_mode not in allowed:
            raise ValueError(f'M{self.number} requires {" or ".join(allowed)}')
        if method not in ('exact', 'alns'):
            raise ValueError(f'Unknown solver method: {method}')
        if method not in self.supported_solvers:
            raise ValueError(f'M{self.number} currently supports only: {", ".join(self.supported_solvers)}')
        return f'models.routing.model_{self.number}.{method}'

MODEL_SPECS = {
    1: ModelSpec(1, "fixed_pure_delivery", "固定分仓、一次性包装纯配送；支持 B=1", True),
    2: ModelSpec(2, "free_pure_delivery", "自由分仓、一次性包装纯配送；支持 B=1", True),
    3: ModelSpec(3, "fixed_container_cycle", "单批次、固定分仓、容器循环", True),
    4: ModelSpec(4, "free_container_cycle", "自由分仓、容器循环；支持 B=1", True),
    5: ModelSpec(5, "flexible_batch_cost", "不等长批次、逐批独立分界与容器路径成本优化", True, True),
    6: ModelSpec(6, "no_batch_container_cycle", "固定分仓、无批次、多趟车辆与循环箱池权衡"),
}

def require_model_number(number):
    if number not in MODEL_SPECS:
        raise ValueError(f"当前模型编号必须为 1–6，收到 {number}")
    return number
