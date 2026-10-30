"""M5 direct Gurobi public entry."""
from models.routing.model_5.exact.solver import solve_exact

__all__ = ['solve_exact']
MODEL_NUMBER = 5
BATCH_MODE = 'multi_batch'
