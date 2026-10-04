"""Current M3: 固定分仓、单批次容器循环."""
from .model import MODEL_NUMBER, BATCH_MODE, PACKAGING, ASSIGNMENT
from .exact_solver import solve_exact
from .alns import solve_alns

SUPPORTS_EXACT = True
__all__ = ['MODEL_NUMBER', 'BATCH_MODE', 'PACKAGING', 'ASSIGNMENT',
           'SUPPORTS_EXACT', 'solve_exact', 'solve_alns']
