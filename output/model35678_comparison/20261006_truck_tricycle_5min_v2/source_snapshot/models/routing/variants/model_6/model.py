"""M6 public scope; physical projections are shared rules, not M5 solving."""
MODEL_NUMBER = 6


def validate_problem(problem):
    if problem.model_number != 6 or not getattr(problem, 'joint_batch', False):
        raise ValueError('Model 6 requires a JointBatchInstance')


def build_model(problem, **kwargs):
    from .exact.model import build_model as build
    return build(problem, **kwargs)
