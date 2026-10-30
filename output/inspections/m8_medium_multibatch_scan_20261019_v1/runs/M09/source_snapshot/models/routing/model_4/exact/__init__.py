"""M4 direct Gurobi public entry."""
from models.routing.model_4.exact.solver import solve_exact

__all__ = ['solve_exact']
MODEL_NUMBER = 4
BATCH_MODE = 'single_batch'
