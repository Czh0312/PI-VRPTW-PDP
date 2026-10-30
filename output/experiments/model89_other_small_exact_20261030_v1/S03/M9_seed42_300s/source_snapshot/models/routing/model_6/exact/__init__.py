"""M6 direct Gurobi public entry."""
from models.routing.model_6.exact.solver import solve_exact

__all__ = ['solve_exact']
MODEL_NUMBER = 6
BATCH_MODE = 'joint_candidate_batches'
