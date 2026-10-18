"""M5 seed correctness at coupled inventory/vehicle/time boundaries."""
from copy import deepcopy
from dataclasses import replace
import time

import pytest

from tools.configuration.loader import load_routing_case
from models.common.cycle import MultiBatchInstance, load_instance
from models.common.cycle.audit import audit_solution
from models.routing.model_5.exact.initialization import construct_incumbent
from tests.routing.test_multibatch_cycle import fixture


def seed(problem):
    result = construct_incumbent(problem, time.perf_counter()+2)
    assert result is not None
    audit = audit_solution(problem, result)
    assert audit['valid'], audit['errors']
    assert audit['periodic_replay']['valid']
    assert not result['info']['proven_optimal']
    assert not result['info']['proven_infeasible']
    return result


@pytest.mark.parametrize('pickup,quantities', [(28, [1, 1]), (29, [1, 1]), (30, [0, 2])])
def test_unload_before_at_and_after_cutoff(pickup, quantities):
    base = fixture(pool=2)
    problem = MultiBatchInstance(base.network,
        [replace(base.orders[0], pickup_window=(pickup, pickup)), base.orders[1]], base.operating_policy)
    result = seed(problem)
    assert result['e2_trips'][0]['return_complete'] == pickup+1
    assert [r['quantity'] for r in result['cutoff_snapshots']] == quantities


def test_cleaning_boundary_and_construction_failure_are_not_proofs():
    result = seed(fixture(cleaning=20))
    assert result['depot_releases'][0]['clean_ready'] == 60
    assert result['audit']['minimum_pool_for_schedule'] == 1
    assert construct_incumbent(fixture(cleaning=21), time.perf_counter()+2) is None
    assert construct_incumbent(fixture(), 0) is None


def test_later_batch_can_collect_old_boxes_without_any_delivery():
    base = fixture()
    problem = MultiBatchInstance(base.network, [replace(base.orders[0], pickup_window=(31, 35))],
                                 base.operating_policy)
    result = seed(problem)
    last = result['e1_trips'][-1]
    assert last['batch_id'] == 'b2' and last['departure'] == 90
    assert [e['kind'] for e in last['events']] == ['p']
    assert result['e1_trips'][0]['return_complete'] == 20


@pytest.mark.parametrize('zero', [True, False])
def test_preparation_unloading_turnaround_and_zero_time_causality(zero):
    problem = fixture(zero=zero, e1_load_time=2, e2_dispatch_prep_time=2,
                      e2_return_unload_time=1, depot_return_unload_time=1,
                      e1_turnaround_time=2, e2_turnaround_time=2)
    result = seed(problem)
    before = deepcopy(result)
    repeated = seed(problem)
    assert repeated == before  # deterministic and no mutation of the input
    assert [r['trip_index'] for r in result['e2_trips']] == [0, 1]


def test_active_trip_slots_are_preserved_and_deliveries_do_not_mix_batches():
    base = fixture(pool=4, e2_max_trips_per_vehicle=2)
    orders = list(base.orders)+[replace(o, id=o.id+'_extra') for o in base.orders]
    problem = MultiBatchInstance(base.network, orders, base.operating_policy)
    result = seed(problem)
    assert len(result['e2_trips']) == 2
    for trip in result['e2_trips']:
        assert len({problem.order_map[e['order_id']].batch for e in trip['events'] if e['kind'] == 'd'}) == 1
    # Tight capacity prevents merging, and one physical vehicle has only two
    # slots. Failure must not leak a partial schedule as a valid incumbent.
    problem.fleet.e2_volume_cap = 1
    assert construct_incumbent(problem, time.perf_counter()+2) is None


def test_deadline_keeps_the_last_fully_audited_seed(monkeypatch):
    from models.routing.model_5.exact.accelerators.multibatch import primal as multibatch_primal
    expired = False
    def audited(*args, **kwargs):
        nonlocal expired
        result = audit_solution(*args, **kwargs)
        expired = True
        return result
    monkeypatch.setattr(multibatch_primal, 'audit_solution', audited)
    monkeypatch.setattr(multibatch_primal.time, 'perf_counter', lambda: 2 if expired else 0)
    result = construct_incumbent(fixture(), 1)
    assert result is not None and audit_solution(fixture(), result)['valid']


def test_multibatch_seed_is_accepted_by_full_milp_and_survives_build_timeout(monkeypatch):
    gp = pytest.importorskip('gurobipy')
    from models.routing.model_5.exact.search import engine as solver
    from models.routing.model_5.exact.formulations.full import build_model
    problem = load_instance(*load_routing_case('config/experiments/examples/multibatch_demo.json'))
    result = seed(problem)
    saved = deepcopy(result)
    model, ctx = build_model(problem)
    try:
        assert solver._set_start(problem, model, ctx, result)
        model.update()
        # Fix every supplied value: finding some unrelated feasible solution
        # would not establish that this particular MIP start is compatible.
        for variable in model.getVars():
            if variable.Start != gp.GRB.UNDEFINED:
                variable.LB = variable.UB = variable.Start
        model.Params.TimeLimit = 5
        model.optimize()
        assert model.SolCount and model.ObjVal == pytest.approx(result['info']['objective'])
    finally:
        model.dispose()
    def timeout(*args, **kwargs):
        raise TimeoutError('forced build timeout')
    monkeypatch.setattr(solver, 'build_model', timeout)
    solved = solver.solve_exact(problem, time_limit=1)
    assert solved['info']['status'] == 'TIME_LIMIT_BUILD'
    assert solved['info']['primal_seed_found']
    assert solved['info']['incumbent_source'] == 'deterministic_multibatch_primal_seed'
    assert audit_solution(problem, solved)['valid']
    assert not solved['info']['proven_optimal'] and not solved['info']['proven_infeasible']
    assert result == saved
