"""JSON-native solution contract; all schedule times are integer ticks."""
from models.common.cycle.policy import VERSION


def empty_result(problem, method):
    p = problem.operating_policy
    return dict(**({'time_tick_subdivision': p.time_tick_subdivision} if p.time_tick_subdivision != 1 else {}),
                schema_version=2, policy_version=VERSION, time_unit='ticks',
                model=problem.model_number, packaging='reusable' if problem.reusable else 'single_use',
                time_tick_seconds=p.time_tick_seconds, objective_mode='distance',
                operating_policy=p.as_dict(), solver=method,
                batches=[dict(id=b.id, start=b.start, cutoff=b.cutoff, end=b.end,
                              container_demand=problem.batch_demands[b.id]) for b in p.batches],
                e1_trips=[], e2_trips=[], info=dict(status='NO_SOLUTION', proven_optimal=False, proven_infeasible=False,
                                                 objective=None, best_bound=None, mip_gap=None))
