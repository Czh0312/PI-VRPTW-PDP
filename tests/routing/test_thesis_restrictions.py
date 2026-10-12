"""Independent restricted-domain checks and the exact 2x2 inclusion lattice."""
from copy import deepcopy

import pytest

from models.common.cycle.audit import audit_solution
from models.common.cycle.restrictions import ablation_domains, audit_restricted_solution, freeze_schedule_domain
from tests.routing.test_model6_joint import joint_fixture, solve_exact, solve_alns, exact_available


def test_four_groups_preserve_physics_and_exact_inclusions(exact_available):
    source = joint_fixture(pool_rate=30)
    reference = solve_exact(source, time_limit=10)
    assert reference['audit']['valid']
    domains = ablation_domains(source, reference)
    costs = {}
    for group, problem in domains.items():
        assert problem.network is source.network
        assert problem.cost_profile.hash == source.cost_profile.hash
        assert problem.operating_policy.batches == source.operating_policy.batches
        result = solve_exact(problem, time_limit=10)
        assert result['info']['proven_optimal']
        assert audit_restricted_solution(problem, result,
            fixed_assignment=reference['order_batch_assignment'] if group in 'AC' else None,
            fixed_pool=source.total if group in 'AB' else None)['valid']
        costs[group] = result['info']['objective']
    assert costs['D'] <= min(costs['B'], costs['C']) + 1e-6
    assert max(costs['B'], costs['C']) <= costs['A'] + 1e-6


def test_alns_enforces_fixed_pool_and_orders(exact_available):
    source = joint_fixture(pool_rate=30)
    reference = solve_exact(source, time_limit=10)
    domain = freeze_schedule_domain(source, reference, fix_assignment=True, fixed_pool=source.total)
    result = solve_alns(domain, max_time=1, max_iterations=100)
    assert audit_solution(domain, result)['valid']
    assert result['container_pool_size'] == source.total
    assert result['order_batch_assignment'] == reference['order_batch_assignment']
    changed = deepcopy(result)
    changed['container_pool_size'] = 1
    assert not audit_solution(domain, changed)['valid']
    changed = deepcopy(result)
    oid = next(iter(changed['order_batch_assignment']))
    changed['order_batch_assignment'][oid] = 'b2' if changed['order_batch_assignment'][oid] == 'b1' else 'b1'
    assert not audit_solution(domain, changed)['valid']


def test_invalid_reference_is_never_used_to_define_domain():
    with pytest.raises(ValueError, match='audited reference'):
        freeze_schedule_domain(joint_fixture(), {})
