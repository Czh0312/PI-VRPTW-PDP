"""Independent restricted-domain checks and the exact 2x2 inclusion lattice."""
from copy import deepcopy
from dataclasses import replace

import pytest

from models.common.cycle.audit import audit_solution
from models.common.cycle.restrictions import ablation_domains, audit_restricted_solution, freeze_schedule_domain
from tests.routing.test_model6_joint import joint_fixture, solve_exact, solve_alns, exact_available
from models.common.cycle.joint import JointBatchInstance
from tools.data.build_thesis_restricted_stages import fixed_cutoff_options


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


def test_nonterminating_minute_cutoff_encoding_has_exact_singleton_domain(exact_available):
    source = joint_fixture(zero=True, optimize_cutoffs=True)
    policy = replace(source.operating_policy, time_tick_seconds=1)
    source = JointBatchInstance(source.network, source.orders, policy,
                               source.options, source.cost_profile.config)
    cuts = {'b1': 41, 'b2': 101}
    options = fixed_cutoff_options(source, {'batch_cutoffs': cuts})
    encoded = JointBatchInstance(source.network, source.orders, policy, options, source.cost_profile.config)
    direct = JointBatchInstance(source.network, source.orders,
        replace(policy, batches=tuple(replace(b, cutoff=cuts[b.id]) for b in policy.batches)),
        {}, source.cost_profile.config)
    assert encoded.cutoff_bounds == direct.cutoff_bounds == {'b1': (41, 41), 'b2': (101, 101)}
    for changed in ({'b1': 40, 'b2': 101}, {'b1': 42, 'b2': 101}):
        with pytest.raises(ValueError, match='outside configured bounds'):
            encoded.validated_cutoffs(changed)
    a, b = solve_exact(encoded, time_limit=5), solve_exact(direct, time_limit=5)
    assert a['info']['proven_optimal'] and b['info']['proven_optimal']
    assert audit_solution(encoded, a)['valid'] and audit_solution(direct, b)['valid']
    assert a['batch_cutoffs'] == b['batch_cutoffs'] == cuts
    assert a['info']['objective'] == pytest.approx(b['info']['objective'])


def test_unchanged_reference_does_not_introduce_cutoff_variables():
    source = joint_fixture()
    assert fixed_cutoff_options(source, {'batch_cutoffs': source.default_cutoffs}) == {}
