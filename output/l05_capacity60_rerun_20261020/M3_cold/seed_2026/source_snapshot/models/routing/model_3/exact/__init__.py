"""M3 direct Gurobi public entry."""
from models.routing.model_3.exact.solver import solve_exact

__all__ = ['solve_exact']
MODEL_NUMBER = 3
BATCH_MODE = 'single_batch'
