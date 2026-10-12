"""Linear-link equivalence and homogeneous multi-batch vehicle relabeling."""
from copy import deepcopy
from dataclasses import replace
from unittest.mock import patch

import pytest

from models.common.cycle import MultiBatchInstance
from models.common.cycle.audit import audit_solution
from models.common.cycle.state import empty_result
from tests.routing.test_multibatch_cycle import fixture, hand_solution
from tests.routing.test_single_batch_solver_audit import tiny_problem


@pytest.mark.parametrize('seed', [0, 2, 5])
def test_all_link_and_symmetry_variants_match_on_nonmetric_networks(seed):
    pytest.importorskip('gurobipy')
    from models.routing.model_5.exact import engine as solver
    from models.routing.model_5.exact.formulation import build_model
    base = tiny_problem(3, seed)
    policy = fixture(pool=4, e2_max_trips_per_vehicle=3,
                     e2_dispatch_prep_time=1, e2_return_unload_time=1,
                     e2_turnaround_time=1, depot_return_unload_time=1).operating_policy
    orders = [replace(o, batch='b1' if i == 0 else 'b2',
                      delivery_window=(0, 25) if i == 0 else (60, 85),
                      pickup_window=(8, 75) if i == 0 else (68, 88)) for i, o in enumerate(base.orders)]
    problem = MultiBatchInstance(base.network, orders, policy)
    problem.fleet.e2_per_sat_count = 2
    objectives = []
    for linear, symmetry in ((False, False), (False, True), (True, False), (True, True)):
        with patch.object(solver, 'build_model', side_effect=lambda *a, **kw: build_model(
                *a, **kw, linear_e2_links=linear, trip_symmetry=symmetry)):
            result = solver.solve_exact(problem, time_limit=5)
        assert result['info']['proven_optimal'], result['info']
        assert audit_solution(problem, result)['valid']
        objectives.append(result['info']['objective'])
    assert objectives == pytest.approx([objectives[0]]*4)


@pytest.mark.parametrize('one_vehicle', [False, True])
def test_zero_travel_split_trips_fit_reduced_slots_after_relabeling(one_vehicle):
    gp = pytest.importorskip('gurobipy')
    from models.routing.model_5.exact.formulation import build_model
    from models.routing.model_5.exact.engine import _set_start
    problem = fixture(pool=2, zero=True, e2_max_trips_per_vehicle=4)
    problem.fleet.e2_per_sat_count = 2
    result = empty_result(problem, 'manual')
    result['e1_trips'] = hand_solution(problem)['e1_trips']
    for trip, batch in zip(result['e1_trips'], problem.operating_policy.batches):
        trip.update(departure=batch.start, return_complete=batch.cutoff)
        trip['events'][0]['start'] = batch.start
    tasks = [('o1', 'd', 0), ('o1', 'p', 1), ('o2', 'd', 60), ('o2', 'p', 61)]
    for index, (oid, kind, when) in enumerate(tasks):
        # Before relabeling the higher-numbered vehicle has three trips and
        # the lower-numbered one has one; their slots must be swapped intact.
        vehicle = 0 if one_vehicle or index == 0 else 1
        slot = index if one_vehicle else max(0, index-1)
        result['e2_trips'].append(dict(trip_id=f't{index}', vehicle_id=vehicle, satellite_id='S',
            trip_index=slot, departure=when, return_complete=when,
            events=[dict(kind=kind, order_id=oid, start=when, quantity=1)]))
    audit = audit_solution(problem, result)
    assert audit['valid'], audit['errors']
    saved = deepcopy(result)
    for linear in (False, True):
        model, ctx = build_model(problem, linear_e2_links=linear)
        try:
            assert [sum(r['vehicle'] == v for r in ctx['e2']) for v in range(2)] == [4, 2]
            assert _set_start(problem, model, ctx, result)
            model.update()
            for variable in model.getVars():
                if variable.Start != gp.GRB.UNDEFINED:
                    variable.LB = variable.UB = variable.Start
            model.Params.TimeLimit = 3
            model.optimize()
            assert model.SolCount and model.ObjVal == pytest.approx(audit['objective'])
        finally:
            model.dispose()
    assert result == saved


def test_linear_links_reduce_indicator_expansion_and_slot_copies():
    pytest.importorskip('gurobipy')
    from models.routing.model_5.exact.formulation import build_model
    problem = fixture(pool=2, e2_max_trips_per_vehicle=4)
    problem.fleet.e2_per_sat_count = 2
    sizes = []
    for compact in (False, True):
        model, ctx = build_model(problem, linear_e2_links=compact, trip_symmetry=compact)
        try:
            sizes.append((model.NumVars, model.NumGenConstrs, len(ctx['e2'])))
        finally:
            model.dispose()
    assert all(new < old for old, new in zip(sizes[0], sizes[1]))
