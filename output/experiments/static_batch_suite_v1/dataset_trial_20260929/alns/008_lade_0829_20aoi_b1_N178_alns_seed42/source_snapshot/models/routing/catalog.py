"""Four-model research design and explicit implementation status."""
from dataclasses import dataclass

@dataclass(frozen=True)
class ModelSpec:
    number: int
    slug: str
    description: str
    supports_cutoff: bool = False

MODEL_SPECS = {
    1: ModelSpec(1, "fixed_pure_delivery", "固定分仓、一次性包装纯配送；新版待适配"),
    2: ModelSpec(2, "free_pure_delivery", "自由分仓、一次性包装纯配送；新版待适配"),
    3: ModelSpec(3, "fixed_container_cycle", "固定分仓、容器循环；支持 B=1 和多批次", True),
    4: ModelSpec(4, "free_container_cycle", "自由分仓、容器循环；新版待适配"),
}

def require_model_number(number):
    if number not in MODEL_SPECS:
        raise ValueError(f"模型编号必须为 1–4；旧 M5–M8 已退出主线，收到: {number}")
    return number
