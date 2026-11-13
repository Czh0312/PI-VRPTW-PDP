"""Simultaneous issue certificates must not reject feasible reuse across K."""
from types import SimpleNamespace

import pytest

from models.common.cycle.pool_bound import forced_batch_pool_violation
from models.routing.service import solve_routing
from tests.routing.test_model6_uniform import uniform_fixture
from tests.routing.test_model78_calendar import calendar_fixture
from tests.routing.test_model6_joint import exact_available


def test_flexible_orders_are_not_counted_as_forced_issue():
    problem = SimpleNamespace(operating_policy=SimpleNamespace(batches=[SimpleNamespace(id=b) for b in ('a', 'b')]),
        orders=[SimpleNamespace(id='x', count=2), SimpleNamespace(id='y', count=2)],
        legal_batches={'x': ('a',), 'y': ('a', 'b')}, pool_max=2)
    assert forced_batch_pool_violation(problem) is None
    problem.legal_batches['y'] = ('a',)
    proof = forced_batch_pool_violation(problem)
    assert proof['forced_batch_demands'] == {'a': 4, 'b': 0}
    assert proof['violating_batches'] == ['a']


@pytest.mark.parametrize('number', [6, 7, 8])
def test_k1_pool_certificate_matches_exact_and_k2_remains_feasible(number, exact_available):
    def instance(counts):
        return (uniform_fixture(counts=counts, pool_max=1) if number == 6 else
                calendar_fixture(number, counts=counts, pool_max=1))
    p = instance((1,))
    alns = solve_routing(model=number, method='alns', problem=p, alns_time=.5, alns_iter=1000)
    exact = solve_routing(model=number, method='exact', problem=p, exact_time=3)
    assert alns['info']['proven_infeasible'] and exact['info']['proven_infeasible']
    assert alns['info']['iterations'] == 0
    both = instance((1, 2))
    result = solve_routing(model=number, method='alns', problem=both, alns_time=1, alns_iter=1000)
    assert result['audit']['valid'] and not result['info']['proven_infeasible']
    assert result['cycle_batch_count'] == 2
    assert next(row for row in result['count_search'] if row['batch_count'] == 1)['proven_infeasible']
