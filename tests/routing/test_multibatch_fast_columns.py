"""Early negative columns retain complete-domain pricing and physical audits."""
from copy import deepcopy
import time

import pytest

from tests.routing.test_multibatch_hybrid import capacity_problem
from models.routing.model_5.exact.pickup_route_pricing import route_context


@pytest.mark.parametrize('value', [0, 1, None, 'false'])
def test_non_boolean_fast_column_option_is_rejected_before_search(value):
    from tests.routing.test_multibatch_cycle import fixture
    from models.routing.model_5.exact.multibatch_hybrid import solve_hybrid
    with pytest.raises(ValueError, match='route_cover_batch_pricing'):
        solve_hybrid(fixture(pool=2), route_cover_batch_pricing=value)


@pytest.mark.parametrize('batch', [False, True])
def test_both_pricing_modes_match_complete_capacity_route_oracle(batch):
    pytest.importorskip('gurobipy')
    from models.routing.model_5.exact.multibatch_route_cg import site_route_cover
    p = capacity_problem()
    row = site_route_cover(p, route_context(p, 'S'), deadline=time.perf_counter()+3,
        heuristic_pricing=False, batch_pricing=batch, dual_repair=True,
        delivery_count_cuts=True, count_dual_repair=True, capacity_dual_repair=True,
        subset_cuts=[[('A', 'd'), ('B', 'd')], [('B', 'd'), ('C', 'd')]])
    assert row['lp_converged'] and row['bound'] == pytest.approx(4, abs=1e-5)
    # Even early termination must report the exact union of all categories;
    # unvisited categories contribute analytic bounds rather than vanishing.
    reports = [it['pricing']['groups'] for it in row['iterations'] if 'pricing' in it]
    assert reports and all({g['category'] for g in groups} == {None, 'b1'} for groups in reports)


@pytest.mark.parametrize('batch', [False, True])
def test_public_mode_keeps_full_periodic_audit_and_disposes_live_models(monkeypatch, batch):
    pytest.importorskip('gurobipy')
    import models.routing.service as service
    import models.routing.model_5.exact.multibatch_route_cg as cg
    from models.routing.model_5.exact.multibatch_hybrid import solve_hybrid
    from models.common.cycle.audit import audit_solution
    from tests.routing.test_multibatch_cycle import fixture, hand_solution
    p = fixture(pool=2)
    initial = hand_solution(p)
    owned = []
    constructor = cg.SiteRouteCoverSession
    def build(*args, **options):
        session = constructor(*args, **options)
        owned.append(session)
        return session
    monkeypatch.setattr(cg, 'SiteRouteCoverSession', build)
    monkeypatch.setattr(service, 'solve_routing', lambda **kw: deepcopy(initial))
    out = solve_hybrid(p, time_limit=2, primal_time_limit=0, cover_time_limit=1,
        conditional_time_limit=0, adaptive_pricing_mode='resume',
        count_dual_repair=True, capacity_dual_repair=True, route_cover_batch_pricing=batch)
    audit = audit_solution(p, out['result'])
    assert audit['valid'] and audit['periodic_replay']['valid']
    assert out['target_reached'] and out['bound'] <= out['upper_bound'] + 1e-6
    assert owned and all(s._disposed for s in owned)
    assert not out['proven_optimal']
