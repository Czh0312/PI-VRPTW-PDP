"""M7 direct Gurobi public entry."""
from models.routing.model_7.exact.solver import solve_exact

__all__ = ['solve_exact']
MODEL_NUMBER = 7
BATCH_MODE = 'uniform_independent_cutoffs'
