"""User budget contract and independent economic benefit arithmetic."""
from types import SimpleNamespace as NS

import pytest

from experiments.runs.run_staged_model_comparison import (
    STAGES, make_jobs, price_physical, economic_threshold, economic_sensitivity)
from tools.data.build_model6_demo_suite import synthetic_profile


def test_all_27_cases_use_requested_stages_objectives_and_budgets():
    records = [dict(id=f'{prefix}{i:02}',size=size,customer_count=count,
        models={str(m):f'{prefix}{i}_m{m}.json' for m in (1,3,4,8)})
        for size,prefix,count in [('small','S',7),('medium','M',20),('large','L',50)]
        for i in range(1,10)]
    jobs = make_jobs(records)
    assert len(jobs)==117
    assert sum(j['budget'] for j in jobs)==121500  # 33h45m, before early exact termination.
    assert [j['stage'] for j in jobs]==sorted((j['stage'] for j in jobs),key=STAGES.index)
    assert {j['seed'] for j in jobs}=={42}
    a,b,c = [[j for j in jobs if j['stage']==stage] for stage in STAGES]
    assert len(a)==27 and {(j['model'],j['method'],j['budget']) for j in a}=={
        (3,'exact',300),(8,'exact',300),(8,'alns',300)}
    assert len(b)==36 and {j['model'] for j in b}=={3,8}
    assert all(j['method']=='alns' and j['objective_mode']=='cost' and
               j['budget']==(600 if j['size']=='medium' else 1200) for j in b)
    assert len(c)==54 and {j['model'] for j in c}=={1,3,4}
    assert all(j['method']=='alns' and j['objective_mode']=='distance' and
               j['budget']==(1200 if j['size']=='medium' else 1800) for j in c)


def test_single_use_is_not_charged_reusable_pool_cleaning_or_wear():
    economics = synthetic_profile()
    economics.update(e1_distance_cost_per_km=.1,e2_distance_cost_per_km=.05,
        e1_vehicle_cost_per_cycle=0,e2_vehicle_cost_per_cycle=0,
        e1_trip_cost=0,e2_trip_cost=0,tracking_cost_per_use=0,
        container_pool_cost_per_period=5,container_price=5,container_lifetime_uses=None,
        container_wear_cost_per_use=.01,cleaning_cost_per_return=.01)
    audit = dict(distances=dict(e1=100,e2=200),time_metrics=dict(working_minutes=dict(e1=0,e2=0)),
        e1_vehicle_count=1,e2_vehicle_count=1,e1_trip_count=1,e2_trip_count=1,
        container_balance=dict(issued=10,returned_to_depot=10))
    single = price_physical(NS(reusable=False,total=10),audit,economics,10)
    reuse = price_physical(NS(reusable=True,total=10),audit,economics,10)
    assert single['total'] is None and single['transport']==pytest.approx(20)
    assert reuse['total']==pytest.approx(70.2)
    assert reuse['pool_acquisition']==50
    assert reuse['recurring_packaging']==pytest.approx(.2)
    assert 'container_pool' not in single['components']


def test_purchase_is_counted_once_in_threshold_and_repeated_cycle_saving():
    # M1: transport20 + 10*p. M3: transport22 + pool50 + cleaning/wear .2.
    single = dict(case='M01',size='medium',seed=42,valid=True,Q=10,transport_cost=20,
                  cost_components=dict(e1_distance=10,e2_distance=10))
    reuse = dict(transport_cost=22,pool_acquisition=50,recurring_packaging=.2,valid=True,
        cost_components=dict(e1_distance=11,e2_distance=11,cleaning=.1,tracking=0,container_wear=.1))
    threshold = economic_threshold(single,reuse)
    assert threshold['first_cycle_single_use_price_threshold']==pytest.approx(5.22)
    assert threshold['recurring_single_use_price_threshold']==pytest.approx(.22)
    scenario = economic_sensitivity(threshold,prices=[0,2,5.22])
    assert scenario[0]['first_break_even_cycle'] is None
    assert scenario[1]['first_cycle_saving']==pytest.approx(-32.2)
    assert scenario[1]['recurring_saving']==pytest.approx(17.8)
    assert scenario[1]['first_break_even_cycle']==3
    assert scenario[2]['first_cycle_saving']==pytest.approx(0)
    assert scenario[2]['first_break_even_cycle']==1


def test_failed_run_has_no_economic_threshold_or_selected_favourable_scenario():
    threshold = economic_threshold(dict(case='L01',size='large',seed=42,valid=False),dict(valid=True))
    assert not threshold['both_valid']
    assert 'first_cycle_single_use_price_threshold' not in threshold
    assert economic_sensitivity(threshold)==[]
