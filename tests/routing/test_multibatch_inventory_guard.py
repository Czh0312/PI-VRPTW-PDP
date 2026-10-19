"""Disputed complete-master claims and exhausted checks cannot close a gap."""
import time

import pytest

from models.routing.model_5.exact.bounds.inventory.inventory_guard import check_inventory_bound
from models.routing.model_5.exact.bounds.inventory.conditional_inventory import build_conditional_inventory, solve_conditional_inventory
from tests.routing.test_multibatch_cycle import fixture


def test_independent_complete_replay_rejects_inflated_claim_without_changing_primary(tmp_path):
    gp = pytest.importorskip('gurobipy')
    p = fixture(pool=2)
    conditions = [dict(site='S', batch='b1', threshold=10, bound=2),
                  dict(site='S', batch='b2', threshold=70, bound=3)]
    model, _ = build_conditional_inventory(p, {'S': 1}, conditions)
    try:
        model.Params.Seed = 53
        model.optimize()
        optimum = model.ObjVal
        primary = (model.NumVars, model.NumConstrs, model.NumGenConstrs,
                   model.Params.Seed, model.Params.Cuts, model.ObjBound)
        checked = check_inventory_bound(model, optimum+14, fallback_bound=40,
            deadline=time.perf_counter()+2, diagnostics_path=tmp_path/'replay.mps.gz')
        assert checked['status'] == 'PRIMARY_BOUND_REJECTED'
        assert checked['reported_bound_consistent'] is False
        assert checked['accepted_raw_bound'] == pytest.approx(optimum, abs=1e-6)
        assert checked['witness']['valid']
        assert checked['witness']['active_indicator_violation'] <= 1e-7
        assert primary == (model.NumVars, model.NumConstrs, model.NumGenConstrs,
                           model.Params.Seed, model.Params.Cuts, model.ObjBound)
        assert (tmp_path/'replay.mps.gz').is_file()
    finally:
        model.dispose()


def test_exhausted_replay_cannot_accept_primary_bound():
    gp = pytest.importorskip('gurobipy')
    model, _ = build_conditional_inventory(fixture(pool=2), {'S': 1}, [])
    try:
        model.optimize()
        checked = check_inventory_bound(model, model.ObjBound, fallback_bound=0, deadline=0)
        assert checked['status'] == 'VERIFICATION_BUDGET_EXHAUSTED'
        assert checked['accepted_raw_bound'] is None
    finally:
        model.dispose()


def test_solve_keeps_previous_floor_when_independent_verification_fails(monkeypatch):
    pytest.importorskip('gurobipy')
    import models.routing.model_5.exact.bounds.inventory.inventory_guard as guard
    def unavailable(model, raw, **kwargs):
        return dict(accepted_raw_bound=None, reported_raw_bound=raw,
                    status='REPLAY_WITNESS_REJECTED', reported_bound_consistent=None)
    monkeypatch.setattr(guard, 'check_inventory_bound', unavailable)
    result = solve_conditional_inventory(fixture(pool=2), {'S': 1}, [],
        fallback_bound=40, deadline=time.perf_counter()+2)
    assert result['reported_raw_bound'] > 40
    assert result['bound'] == 40 and result['raw_bound'] is None
    assert not result['relaxation_optimal']


def test_normal_complete_inventory_bound_is_checked_within_total_deadline(tmp_path):
    pytest.importorskip('gurobipy')
    begun = time.perf_counter()
    result = solve_conditional_inventory(fixture(pool=2), {'S': 1}, [],
        deadline=begun+2, diagnostics_path=tmp_path/'master.mps.gz')
    assert result['bound_verification']['status'] == 'REPLAY_BOUND_CHECKED'
    assert result['relaxation_optimal'] and result['bound'] <= 41+1e-6
    assert time.perf_counter()-begun < 2.25
    assert (tmp_path/'master_verification.mps.gz').is_file()
