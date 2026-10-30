"""Sound necessary-domain certificates and whole-vehicle symmetry reduction."""
from dataclasses import replace
from itertools import product
from types import SimpleNamespace as NS

import pytest

from models.common.cycle.audit import audit_solution
from models.common.cycle.calendar import CalendarCycleInstance
from models.routing.model_8.exact import solve_exact
from models.routing.model_8.exact.model import build_model
from models.routing.model_8.exact import preprocessing as prep
from tests.routing.test_model8_physical_bounds import instance
from tests.routing.test_productive_batches import productive_fixture


def test_hall_certificates_match_exhaustive_surjective_assignments():
    subsets = [tuple(i for i in range(3) if mask & (1 << i)) for mask in range(8)]
    for choices in product(subsets, repeat=3):
        domains = dict(enumerate(choices))
        certificate = prep.productive_matching_certificate(domains, list(range(3)))
        feasible = any(len(set(a)) == 3 for a in product(*choices))
        assert (certificate is None) == feasible, domains
        if certificate is not None:
            neighborhood = {o for o, d in domains.items() if set(d) & set(certificate['batches'])}
            assert neighborhood == set(certificate['neighboring_orders'])
            assert len(neighborhood) < len(certificate['batches'])


@pytest.mark.parametrize('productive', [False, True])
def test_temporal_support_counts_containers_not_orders(monkeypatch, productive):
    # One earlier three-container order can support two previous productive
    # return batches. Counting that order only once would unsafely remove b3.
    p = NS(orders=[NS(id='a', count=3), NS(id='b', count=1), NS(id='c', count=1)],
           legal_batches={o:list(range(1,4)) for o in ('a','b','c')},
           operating_policy=NS(batches=[NS(id=i) for i in range(1,4)],
                               cleaning_duration=0, time_tick_seconds=1),
           require_productive_batches=productive, pool_min=1, pool_max=5)
    monkeypatch.setattr(prep, 'necessary_time_bounds', lambda _:dict(pool_lower_bound=1, intervals=[
        dict(order=o, count=q, latest_loading=100, earliest_clean=r)
        for o,q,r in [('a',3,50),('b',1,150),('c',1,200)]]))
    bounds = prep.temporal_assignment_bounds(p)
    assert 3 in bounds['domains']['b'] and 3 in bounds['domains']['c']
    assert bounds['pool_lower_bound'] >= 3
    if productive:
        assert bounds['domains']['a'] == [1]
    else:
        assert all(d == [1,2,3] for d in bounds['domains'].values())
    assert bounds['certificate'] is None


@pytest.mark.parametrize('kind', ['ordinary','late_release','multiple_satellites','unequal_counts'])
@pytest.mark.parametrize('formulation', ['expanded','compact'])
def test_optimum_and_independent_two_cycle_audit_are_preserved(kind, formulation):
    p = instance(kind)
    results = []
    for domains, symmetry in [(False,False),(True,False),(False,True),(True,True)]:
        result = solve_exact(p, time_limit=25, preprocessing=domains, vehicle_symmetry=symmetry,
                             milp_options=dict(formulation=formulation))
        assert result['info']['proven_optimal'], result['info']
        audit = audit_solution(p, result)
        assert audit['valid'] and audit['periodic_replay']['valid']
        options = result['info']['solver_config']['formulation_options']
        assert options['preprocessing'] is domains and options['vehicle_symmetry'] is symmetry
        assert result['info']['batch_count_candidates'] == list(p.batch_count_candidates)
        assert result['info']['complete_count_domain']
        results.append(audit['objective'])
    assert results == pytest.approx([results[0]]*4)


def test_three_simultaneous_deliveries_retain_all_three_homogeneous_vehicles():
    source = productive_fixture(8, count=1, orders=3)
    orders = [replace(o, delivery_window=(10,10), pickup_window=(30,30), service_time=1)
              for o in source.orders]
    p = CalendarCycleInstance(source.network, orders, source.operating_policy,
                             dict(batch_count_candidates=[1]), source.cost_profile.config, model=8)
    baseline = solve_exact(p,time_limit=25,milp_options=dict(formulation='compact'))
    model, ctx = build_model(p,batch_count=1,compact_e2=True,preprocessing=True,vehicle_symmetry=True)
    try:
        model.Params.OutputFlag = 0
        model.Params.TimeLimit = 25
        model.optimize()
        assert model.Status == 2 and baseline['info']['proven_optimal']
        assert model.ObjVal == pytest.approx(baseline['info']['objective'])
        first = [r for r in ctx['e2'] if r['slot'] == 0]
        assert all(r['used'].X > .5 for r in first)
        keys = [sum((i+1)*r['x'][-1,i].X for i in range(len(r['nodes']))) for r in first]
        assert all(a+1 <= b+1e-6 for a,b in zip(keys,keys[1:]))
        assert ctx['vehicle_symmetry_rows'] == 2
    finally:
        model.dispose()


def test_screened_counts_remain_in_full_native_infeasibility_proof():
    p = instance('ordinary')
    p = CalendarCycleInstance(p.network,p.orders,p.operating_policy,
                             dict(batch_count_candidates=[3,4]),p.cost_profile.config,model=8)
    result = solve_exact(p,time_limit=15,preprocessing=True,vehicle_symmetry=True)
    assert result['info']['proven_infeasible']
    assert result['info']['complete_count_domain']
    assert result['info']['batch_count_candidates'] == [3,4]
    assert result['info']['built_batch_counts'] == []
    assert set(result['info']['screened_batch_counts']) == {3,4}


def test_options_are_explicit_booleans():
    with pytest.raises(ValueError,match='boolean'):
        solve_exact(instance('ordinary'),preprocessing='heuristic')
    with pytest.raises(ValueError,match='boolean'):
        build_model(instance('ordinary'),vehicle_symmetry=1)


@pytest.mark.parametrize('first_quantity', [1,3])
def test_three_staggered_productive_batches_preserve_the_optimum(first_quantity):
    source = productive_fixture(8,count=3,orders=3)
    orders = [replace(o, count=first_quantity if i == 0 else 1,
                      delivery_window=d, pickup_window=p)
              for i,(o,d,p) in enumerate(zip(source.orders,[(0,20),(40,65),(80,105)],
                                             [(30,50),(70,90),(110,125)]))]
    p = CalendarCycleInstance(source.network,orders,replace(source.operating_policy,cycle_end=133),
                             dict(batch_count_candidates=[1,2,3]),source.cost_profile.config,model=8)
    baseline = solve_exact(p,time_limit=25,milp_options=dict(formulation='compact'))
    enhanced = solve_exact(p,time_limit=25,preprocessing=True,vehicle_symmetry=True,
                           milp_options=dict(formulation='compact'))
    assert baseline['info']['proven_optimal'] and enhanced['info']['proven_optimal']
    assert baseline['info']['objective'] == pytest.approx(enhanced['info']['objective'])
    assert audit_solution(p,enhanced)['periodic_replay']['valid']
