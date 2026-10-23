"""Certificate soundness without Gurobi; licensed integration checks separately."""
import itertools
from importlib import import_module
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from models.common.cycle.count_screening import infeasible_count_certificate
from tests.routing.test_model6_joint import exact_available


def assignment_problem(domains, batches, productive=True):
    return NS(orders=[NS(id=i) for i in range(len(domains))],
              operating_policy=NS(batches=[NS(id=b) for b in batches]),
              legal_batches=dict(enumerate(domains)), require_productive_batches=productive)


def test_certificates_never_exclude_a_surjective_assignment():
    # Exhaust all 512 domain graphs on three orders and three batches.
    # The oracle enumerates assignments rather than reproducing the precheck.
    subsets = [tuple(i for i in range(3) if mask & (1 << i)) for mask in range(8)]
    for domains in itertools.product(subsets, repeat=3):
        p = assignment_problem(domains, range(3))
        feasible = any(len(set(a)) == 3 for a in itertools.product(*domains))
        if infeasible_count_certificate(p) is not None:
            assert not feasible, domains


def test_pigeonhole_and_empty_order_certificates():
    p = assignment_problem([(0, 1, 2), (0, 1, 2)], range(3))
    assert infeasible_count_certificate(p)['reason'] == 'more_productive_batches_than_orders'
    p = assignment_problem([(), (0,)], range(1))
    assert infeasible_count_certificate(p)['orders'] == [0]


def test_empty_batches_are_allowed_when_productivity_is_not_required():
    p = assignment_problem([(0,)], (0, 1), productive=False)
    assert infeasible_count_certificate(p) is None


def test_no_certificate_does_not_claim_feasibility():
    # Hall violation, intentionally outside this limited precheck: batches 1
    # and 2 both need order 2. Every individual domain/neighborhood is nonempty.
    p = assignment_problem([(0,), (0,), (1, 2)], range(3))
    assert infeasible_count_certificate(p) is None
    assert not any(len(set(a)) == 3 for a in itertools.product(*p.legal_batches.values()))


def test_unknown_batch_is_an_input_error_not_an_infeasibility_proof():
    with pytest.raises(ValueError, match='unknown batch'):
        infeasible_count_certificate(assignment_problem([(1,)], (0,)))


@pytest.mark.parametrize('number', [6, 7, 8])
def test_s04_uses_each_models_actual_calendar_domain(number):
    from models.common.cycle import load_instance
    from tools.configuration.loader import load_routing_case
    root = Path(__file__).resolve().parents[2]
    p = load_instance(*load_routing_case(
        root / f'data/formal_benchmark27_spacetime_v2/model_{number}/small/S04/case.json'))
    rejected = [k for k in p.batch_count_candidates if infeasible_count_certificate(p.for_count(k))]
    assert rejected == ([] if number == 8 else [3, 4, 5, 6, 7])


@pytest.mark.parametrize('number', [6, 7, 8])
def test_screened_unified_retains_optimum_and_full_proof_scope(number, exact_available):
    from models.common.cycle.unified_milp import solve_unified_milp
    from tests.routing.test_unified_batch_milp import instance
    p = instance(number, counts=(1, 2, 3))
    module = f'models.routing.model_{number}.exact'
    baseline = import_module(module).solve_exact(p, time_limit=15)
    build = import_module(module + '.formulation').build_model
    calls = []

    def tracked(child, **kwargs):
        calls.append(len(child.operating_policy.batches))
        return build(child, **kwargs)

    screened = solve_unified_milp(p, build_fixed=tracked,
        extract_fixed=import_module(module + '.solution').extract_solution,
        time_limit=15, count_screening=True)
    assert 3 not in calls
    assert screened['info']['proven_optimal'] and baseline['info']['proven_optimal']
    assert screened['audit']['valid'] and screened['audit']['periodic_replay']['valid']
    assert screened['info']['objective'] == pytest.approx(baseline['info']['objective'])
    assert screened['info']['batch_count_candidates'] == [1, 2, 3]
    assert screened['info']['complete_count_domain']
    assert 3 in screened['info']['screened_batch_counts']


@pytest.mark.parametrize('number', [6, 7, 8])
def test_all_rejected_counts_are_still_proven_by_unified_model(number, exact_available):
    from models.common.cycle.unified_milp import solve_unified_milp
    from tests.routing.test_unified_batch_milp import instance

    def forbidden(*args, **kwargs):
        raise AssertionError('No fixed-K construction or extraction should occur')

    result = solve_unified_milp(instance(number, counts=(3, 4)),
        build_fixed=forbidden, extract_fixed=forbidden, time_limit=10, count_screening=True)
    assert result['info']['proven_infeasible']
    assert result['info']['complete_count_domain']
    assert result['info']['built_batch_counts'] == []
    assert set(result['info']['screened_batch_counts']) == {3, 4}
