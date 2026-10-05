"""M7: equal-length batches with independent cutoffs."""
MODEL_NUMBER = 7


def validate_problem(problem):
    if problem.model_number != MODEL_NUMBER or not getattr(problem, 'calendar_cycle', False):
        raise ValueError('Model 7 requires its CalendarCycleInstance')


def build_model(problem, **kwargs):
    from .exact.model import build_model as build
    return build(problem, **kwargs)

