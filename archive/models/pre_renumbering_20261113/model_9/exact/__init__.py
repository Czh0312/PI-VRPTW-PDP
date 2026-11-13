"""M9 direct Gurobi public entry."""
from models.routing.model_9.exact.solver import solve_exact

__all__ = ['solve_exact']
MODEL_NUMBER = 9
BATCH_MODE = 'flexible_cycle'
