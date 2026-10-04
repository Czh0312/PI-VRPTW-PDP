"""Cover budget is explicit and remains inside the shared hybrid deadline."""
from copy import deepcopy
from types import SimpleNamespace
import math

import pytest

from tests.routing.test_multibatch_cycle import fixture, hand_solution


@pytest.mark.parametrize('fraction', [0, .25, .5])
def test_subset_reservation_uses_same_total_cover_budget_and_zero_skips_selection(monkeypatch, fraction):
    import models.routing.service as service
    import models.routing.solvers.exact.multibatch_hybrid as hybrid
    import models.routing.solvers.exact.multibatch_route_cg as cg
    import models.routing.solvers.exact.multibatch_capacity_cuts as cuts
    import models.routing.solvers.exact.multibatch_route_pricing as pricing
    # Load the module before patching pricing: its module-level import must
    # retain the real route_context for later full-model tests.
    import models.routing.solvers.exact.multibatch_route_pool
    p = fixture(pool=2)
    initial = hand_solution(p)
    clock = [100.0]
    calls = []
    monkeypatch.setattr(hybrid, 'time', SimpleNamespace(perf_counter=lambda: clock[0]))
    monkeypatch.setattr(service, 'solve_routing', lambda **kw: deepcopy(initial))
    monkeypatch.setattr(pricing, 'route_context', lambda p, site: dict(satellite=site))
    def plain(p, **kw):
        calls.append(('plain', kw['time_limit']))
        clock[0] += kw['time_limit']
        return dict(bound=40, e1_bound=40, sites=[dict(satellite='S', bound=0, columns=[])],
                    components=[dict(satellite='S', bound=0)])
    def select(p, context, columns, **kw):
        calls.append(('select', kw['deadline']))
        return [[('A', 'd')]]
    def subset(p, context, **kw):
        calls.append(('subset', kw['deadline']-clock[0]))
        clock[0] = kw['deadline']
        return dict(satellite='S', bound=0, columns=[])
    monkeypatch.setattr(cg, 'route_cover_certificate', plain)
    monkeypatch.setattr(cuts, 'select_capacity_subsets', select)
    monkeypatch.setattr(cg, 'site_route_cover', subset)
    out = hybrid.solve_hybrid(p, time_limit=360, primal_time_limit=0, cover_time_limit=360,
                              conditional_time_limit=0, adaptive_finish=False,
                              subset_time_fraction=fraction)
    assert calls[0] == ('plain', 360*(1-fraction))
    assert out['runtime'] == 360 and out['bound'] <= out['upper_bound']
    assert not out['proven_optimal'] and not out['proven_infeasible']
    if fraction:
        assert [row[0] for row in calls] == ['plain', 'select', 'subset']
        assert calls[-1][1] == 360*fraction
    else:
        assert len(calls) == 1 and out['reference_certificate']['subset_runs'] == []


@pytest.mark.parametrize('value', [-.1, 1, math.inf, math.nan, True, '0'])
def test_invalid_subset_fraction_is_rejected_before_starting_solver(value):
    from models.routing.solvers.exact.multibatch_hybrid import solve_hybrid
    with pytest.raises(ValueError, match='subset_time_fraction'):
        solve_hybrid(fixture(pool=2), subset_time_fraction=value)
