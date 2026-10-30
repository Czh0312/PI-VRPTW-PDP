"""M1 direct Gurobi public entry."""
from models.routing.model_1.exact.solver import solve_exact

__all__ = ['solve_exact']
MODEL_NUMBER = 1
BATCH_MODE = 'single_batch'
