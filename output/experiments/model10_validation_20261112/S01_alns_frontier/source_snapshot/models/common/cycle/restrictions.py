"""Audited fixed-calendar experimental domains; contains no solver calls."""
from dataclasses import replace

from models.common.cycle.audit import audit_solution
from models.common.cycle.joint import JointBatchInstance
from models.common.cycle.policy import integer


def freeze_schedule_domain(problem, reference, *, fix_assignment=False, fixed_pool=None,
                           economic_config=None):
    """Freeze calendar/cutoffs, not route/service decisions, from a valid result.

    The result supplies a common feasible reference for E2/E4. Restricting the
    pool below the reference requirement may make the new problem infeasible;
    feasibility must be established by reoptimization, never by relabeling it.
    """
    audit = audit_solution(problem, reference)
    if not audit.get('valid'):
        raise ValueError('A frozen experimental domain requires an audited reference')
    if getattr(problem, 'optimize_loading', False):
        raise ValueError('Fixed-calendar ablation uses standard batch-start loading')
    if getattr(problem, 'calendar_cycle', False):
        child = problem.selected_problem(reference)
    elif getattr(problem, 'uniform_cycle', False):
        child = problem.for_count(reference['cycle_batch_count'])
    else:
        child = problem
    cutoffs = child.validated_cutoffs(reference['batch_cutoffs'])
    policy = replace(child.operating_policy, batches=tuple(
        replace(b, cutoff=cutoffs[b.id]) for b in child.operating_policy.batches))
    options = dict(pool_min=1, pool_max=child.total)
    if fix_assignment:
        options['fixed_assignment'] = dict(reference['order_batch_assignment'])
    if fixed_pool is not None:
        pool = integer(fixed_pool, 'fixed_pool', 1)
        if pool > child.total:
            raise ValueError('Fixed pool exceeds total demand')
        options.update(pool_min=pool, pool_max=pool)
    return JointBatchInstance(child.network, child.orders, policy, options,
                              economic_config or child.cost_profile.config, _physical_source=child)


def ablation_domains(problem, reference, fixed_pool=None):
    """The four domains share exactly the same calendar and physical inputs."""
    pool = problem.total if fixed_pool is None else fixed_pool
    return {group: freeze_schedule_domain(problem, reference,
                fix_assignment=group in ('A', 'C'), fixed_pool=pool if group in ('A', 'B') else None)
            for group in ('A', 'B', 'C', 'D')}


def audit_restricted_solution(problem, result, *, fixed_assignment=None, fixed_pool=None):
    audit = audit_solution(problem, result)
    if fixed_assignment is not None and result.get('order_batch_assignment') != fixed_assignment:
        audit['errors'].append(dict(code='EXPERIMENT_FIXED_ASSIGNMENT_MISMATCH'))
    if fixed_pool is not None and result.get('container_pool_size') != fixed_pool:
        audit['errors'].append(dict(code='EXPERIMENT_FIXED_POOL_MISMATCH'))
    audit['valid'] = not audit['errors']
    return audit
