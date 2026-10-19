"""M8 direct Gurobi public entry."""
from models.routing.model_8.exact.solver import solve_exact

__all__ = ['solve_exact']
MODEL_NUMBER = 8
BATCH_MODE = 'flexible_cycle'
