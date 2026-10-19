"""Preserve useful routes and the shared budget without inventing new bounds."""
from copy import deepcopy

import pytest

from models.routing.model_5.hybrid.solver import solve_hybrid
from models.routing.model_5.exact.primal.hybrid_primal import collect_pricing_columns, near_target_primal_budget
from tests.routing.test_multibatch_cycle import fixture, hand_solution


def test_column_archive_keeps_weaker_subset_and_conditional_routes_without_mutation():
    p = fixture(pool=2)
    a, b, c = [dict(tasks=[(key, 'd')], cost=cost)
               for key, cost in [('a', 5), ('b', 6), ('c', 7)]]
    ref = dict(sites=[dict(satellite='S', columns=[a])],
        subset_runs=[dict(certificate=dict(satellite='S', columns=[a, b]))])
    cond = dict(conditions=[dict(certificate=dict(satellite='S', columns=[c]))])
    before = deepcopy((ref, cond))
    archived = collect_pricing_columns(p, ref, cond)
    assert archived == {'S': [a, b, c]} and (ref, cond) == before


def test_near_target_budget_for_actual_failed_seed_preserves_certification_time():
    upper, lower = 3294.7041546096525, 3071.3434936359736
    kw = dict(target_gap=.05, gap_limit=.10, time_limit=120)
    assert near_target_primal_budget(upper, lower, remaining=100, **kw) == 90
    assert near_target_primal_budget(upper, lower, remaining=300, **kw) == 120
    assert near_target_primal_budget(3204.2882786993478, lower, remaining=100, **kw) == 0
    assert near_target_primal_budget(upper, 2500, remaining=100, **kw) == 0
    assert near_target_primal_budget(upper, lower, remaining=0, **kw) == 0


def test_public_revisit_occurs_before_conditions_and_does_not_change_floor(monkeypatch):
    import models.routing.service as service
    import models.routing.model_5.exact.primal.route_pool as pool
    import models.routing.model_5.exact.bounds.route_cover_cg.route_cg as cg
    import models.routing.model_5.exact.bounds.inventory.conditional_inventory as inventory
    p, initial = fixture(pool=2), hand_solution(fixture(pool=2))
    calls = []
    monkeypatch.setattr(service, 'solve_routing', lambda **kwargs: deepcopy(initial))
    def cover(*args, **kwargs):
        return dict(bound=40, e1_bound=38, sites=[dict(satellite='S', bound=2, columns=[])],
                    components=[dict(satellite='S', bound=2)])
    monkeypatch.setattr(cg, 'route_cover_certificate', cover)
    def primal(p, result, **kwargs):
        calls.append(('primal', kwargs['time_limit']))
        assert kwargs['additional_columns'] == {'S': []}
        return result
    monkeypatch.setattr(pool, 'reoptimize_route_pool', primal)
    def conditional(*args, **kwargs):
        calls.append(('conditional', None))
        return dict(bound=40, runtime=0)
    monkeypatch.setattr(inventory, 'refine_conditional_bound', conditional)
    result = solve_hybrid(p, time_limit=1, primal_time_limit=0, cover_time_limit=.1,
        conditional_time_limit=.1, max_subset_cuts_per_batch=0, adaptive_finish=False,
        primal_revisit_time_limit=.2)
    assert [c[0] for c in calls] == ['primal', 'conditional']
    assert calls[0][1] == pytest.approx(.2)
    assert result['bound'] == 40 and result['upper_bound'] == 44
    assert not result['target_reached']
    assert [s['stage'] for s in result['stages']] == [
        'alns', 'route_cover', 'route_pool_revisit', 'conditional_inventory']


@pytest.mark.parametrize('options', [dict(primal_revisit_time_limit=-1),
    dict(primal_revisit_gap_limit=float('nan')), dict(primal_revisit_gap_limit=1),
    dict(retain_all_pricing_columns=1), dict(primal_mip_focus=True), dict(primal_mip_focus=4)])
def test_invalid_policy_is_rejected_before_search(options):
    with pytest.raises(ValueError):
        solve_hybrid(fixture(pool=2), **options)


def test_primal_focus_keeps_physical_fleet_and_never_promotes_pool_bound():
    pytest.importorskip('gurobipy')
    from models.routing.model_5.exact.primal.route_pool import reoptimize_route_pool
    from models.common.cycle.audit import audit_solution
    from tests.routing.test_multibatch_extra_slots import split_fixture
    p, initial = split_fixture()
    original = deepcopy(initial)
    proposal = {'S': [dict(tasks=[(oid, 'd'), (oid, 'p')]) for oid in 'ABC']}
    result = reoptimize_route_pool(p, initial, time_limit=2, mip_focus=1,
        additional_columns=proposal, extra_slots_per_vehicle=2)
    assert result['info']['objective'] == 6
    assert result['info']['route_pool_mip_focus'] == 1
    assert result['info']['best_bound'] is None and not result['info']['proven_optimal']
    assert audit_solution(p, result)['periodic_replay']['valid']
    assert initial == original
    for value in (-1, True, 1.5, 4):
        with pytest.raises(ValueError, match='mip_focus'):
            reoptimize_route_pool(p, initial, time_limit=1e-12, mip_focus=value)
