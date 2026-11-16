"""Connect this model's route components to the shared single-batch search."""
import random as random

from models.routing.alns.single_batch import solve_single_batch

from .construction import appointment_state, chronological_state
from .evaluator import initial_state
from .local_descent import SingleBatchDescent
from .neighborhoods import destroy_repair, move_route as _move, state_from_result, state_key
from .performance.single_batch_cache import SingleBatchCache


def solve_alns(problem, max_time=10, max_iterations=5000, seed=42, *args, **kwargs):
    return solve_single_batch(
        problem, max_time, max_iterations, seed, *args,
        initial_state=initial_state,
        chronological_state=chronological_state,
        appointment_state=appointment_state,
        move_route=_move,
        destroy_repair=destroy_repair,
        state_key=state_key,
        state_from_result=state_from_result,
        cache_factory=SingleBatchCache,
        descent_factory=SingleBatchDescent,
        **kwargs,
    )
