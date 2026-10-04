"""Current numbering: M1-M4 single batch; M5 fixed multi-batch cycles."""
from dataclasses import dataclass

@dataclass(frozen=True)
class ModelSpec:
    number: int
    slug: str
    description: str
    supports_cutoff: bool = False
    supports_multi_batch: bool = False

    def module_path(self, batch_mode):
        if batch_mode not in ('single_batch', 'multi_batch'):
            raise ValueError(f'Unknown batch mode: {batch_mode}')
        expected = 'multi_batch' if self.supports_multi_batch else 'single_batch'
        if batch_mode != expected:
            raise ValueError(f'M{self.number} requires {expected}')
        return f'models.routing.variants.model_{self.number}'

MODEL_SPECS = {
    1: ModelSpec(1, "fixed_pure_delivery", "固定分仓、一次性包装纯配送；支持 B=1", True),
    2: ModelSpec(2, "free_pure_delivery", "自由分仓、一次性包装纯配送；支持 B=1", True),
    3: ModelSpec(3, "fixed_container_cycle", "单批次、固定分仓、容器循环", True),
    4: ModelSpec(4, "free_container_cycle", "自由分仓、容器循环；支持 B=1", True),
    5: ModelSpec(5, "fixed_multibatch_container_cycle", "多批次、固定分仓、有限容器跨批循环", True, True),
}

def require_model_number(number):
    if number not in MODEL_SPECS:
        raise ValueError(f"当前模型编号必须为 1–5；M5 是新版多批次模型，旧 M6–M8 已退出主线，收到: {number}")
    return number
