"""Independent cost arithmetic, joint choices, release/cutoff audit and isolation."""
from copy import deepcopy
from dataclasses import replace
import itertools
import json
from pathlib import Path
import subprocess
import sys

import pytest

from models.common.cycle.instance import Order
from models.common.cycle.policy import MultiBatchPolicy
from models.common.cycle.joint import JointBatchInstance
from models.common.cycle.audit import audit_solution
from models.common.domain.node import Depot, Satellite, Customer
from models.common.domain.instance import ProblemInstance
from models.cost.joint_batch import JointCostProfile
from tools.data.build_model6_demo_suite import synthetic_profile,build_suite


def solve_exact(*args, **kwargs):
    from models.routing.model_6.exact import solve_exact as solve
    return solve(*args, **kwargs)


def solve_alns(*args, **kwargs):
    from models.routing.model_6.alns import solve_alns as solve
    return solve(*args, **kwargs)


def joint_fixture(pool_rate=5,cleaning=5,late=False,one_batch=False,zero=False,**options):
    policy=dict(policy_version='multi_batch_cutoff_v1',cycle_start=0,cycle_end=130,
                batches=[dict(id='b1',delivery_duration=40,return_duration=20),
                         dict(id='b2',delivery_duration=40,return_duration=20)],
                container_pool_size=2,cleaning_duration=cleaning,e2_max_trips_per_vehicle=4,time_tick_seconds=60)
    if one_batch:
        policy['batches']=policy['batches'][:1]
    p=MultiBatchPolicy.from_config(policy)
    nodes={'D':Depot('D',0,0),'S':Satellite('S',0 if zero else 5,0)}
    for i in (1,2):
        nodes['C'+str(i)]=Customer('C'+str(i),0 if zero else 5+i,0,0,0,(0,130),(0,130),'S')
    fleet=dict(E1_vehicle_count=1,vehicles_per_satellite=1,E1_Qk=100,E1_Ck=100,E2_Qv=100,E2_Cv=100,container_weight=1,container_volume=1)
    allowed=('b1',) if one_batch else ('b1','b2')
    orders=[Order('o1','C1','S','b1',1,1,(0,20 if late else 90),(65,70) if late else (0,95),0,2,0,allowed)]
    if not late:
        orders.append(Order('o2','C2','S','b1' if one_batch else 'b2',1,1,(0,90),(0,95),0,2,0,allowed))
    return JointBatchInstance(ProblemInstance(nodes,fleet),orders,p,options,synthetic_profile(pool_rate))


@pytest.fixture
def exact_available():
    gp=pytest.importorskip('gurobipy')
    try:
        m=gp.Model(); m.Params.OutputFlag=0; m.dispose()
    except gp.GurobiError as exc:
        pytest.skip(str(exc))


def test_hand_arithmetic_for_two_productive_batches(exact_available):
    p=joint_fixture()
    result=solve_exact(p,time_limit=10)
    assert result['audit']['valid'] and result['info']['proven_optimal']
    # 26 distance + 10 occupied-time + 15 physical vehicles + 4 trips
    # + 5 pool + .6 cleaning/tracking + .4 wear, investment separate.
    assert result['info']['objective']==pytest.approx(61)
    assert result['total_distance']==26
    assert result['container_pool_size']==1 and result['delivery_batch_count']==2
    assert result['cost']['initial_pool_investment']==10
    assert result['audit']['e1_vehicle_count']==1 and result['audit']['e1_trip_count']==2


@pytest.mark.parametrize('method', ['exact', 'alns'])
def test_basic_cost_coefficients_match_solver_and_independent_replay(method, exact_available):
    from tools.configuration.loader import load_json
    base = joint_fixture()
    profile = load_json('config/experiments/benchmark27_v1/economic.json')
    problem = JointBatchInstance(base.network, base.orders, base.operating_policy, {}, profile)
    result = (solve_exact(problem, time_limit=5) if method == 'exact' else
              solve_alns(problem, max_time=.5, max_iterations=100))
    audited = audit_solution(problem, result)
    assert audited['valid'] and audited['periodic_replay']['valid']
    expected = (.1*audited['distances']['e1'] + .05*audited['distances']['e2']
                + 5*result['container_pool_size'] + .02*problem.total)
    assert result['info']['objective'] == pytest.approx(expected)
    assert result['cost_components']['cleaning'] == pytest.approx(.01*problem.total)
    assert result['cost_components']['tracking'] == 0
    assert result['cost_components']['container_wear'] == pytest.approx(.01*problem.total)
    for layer in ('e1', 'e2'):
        for kind in ('work', 'vehicles', 'trips'):
            assert result['cost_components'][layer+'_'+kind] == 0
    assert result['cost']['initial_pool_investment'] == 5*result['container_pool_size']


@pytest.mark.parametrize('value', [-.01, None, float('nan'), float('inf')])
def test_explicit_per_use_wear_requires_a_finite_nonnegative_rate(value):
    config = synthetic_profile()
    config.update(container_lifetime_uses=None, container_wear_cost_per_use=value)
    with pytest.raises(ValueError):
        JointCostProfile(config)


def test_explicit_wear_is_independent_of_purchase_price_and_cannot_duplicate_depreciation():
    config = synthetic_profile()
    config.update(container_lifetime_uses=None, container_wear_cost_per_use=.01)
    assert JointCostProfile(config).constant_cost(7)['container_wear'] == pytest.approx(.07)
    config['container_price'] = 500
    assert JointCostProfile(config).constant_cost(7)['container_wear'] == pytest.approx(.07)
    config['container_lifetime_uses'] = 50
    with pytest.raises(ValueError, match='not both'):
        JointCostProfile(config)
    config.update(container_lifetime_uses=None, pool_cost_basis='rental', rental_includes=['wear'])
    with pytest.raises(ValueError, match='wear'):
        JointCostProfile(config)


def test_high_pool_charge_selects_two_batches_and_one_physical_box(exact_available):
    p=joint_fixture(pool_rate=30)
    r=solve_exact(p,time_limit=10)
    assert r['audit']['valid'] and r['info']['proven_optimal']
    assert r['container_pool_size']==1 and r['delivery_batch_count']==2
    assert r['total_distance']==26 and r['info']['objective']==pytest.approx(86)
    assert r['audit']['e1_vehicle_count']==1 and r['audit']['e1_trip_count']==2
    assert r['cost_components']['e1_vehicles']==10
    assert r['audit']['periodic_replay']['valid']


def test_joint_optimum_matches_all_fixed_partitions(exact_available):
    p=joint_fixture(pool_rate=30)
    full=solve_exact(p,time_limit=10)
    values=[]
    for bids in itertools.product(('b1','b2'),repeat=2):
        fixed=joint_fixture(pool_rate=30,fixed_assignment=dict(zip(('o1','o2'),bids)))
        r=solve_exact(fixed,time_limit=10)
        if len(set(bids)) < 2:
            assert r['info']['proven_infeasible']
            continue
        assert r['info']['proven_optimal'] and r['audit']['valid']
        values.append(r['info']['objective'])
    assert full['info']['objective']==pytest.approx(min(values))


def test_late_cleaning_cannot_supply_second_batch(exact_available):
    p=joint_fixture(cleaning=25,pool_max=1)
    r=solve_exact(p,time_limit=10)
    assert r['info']['proven_infeasible'] and r['info']['objective'] is None
    h=solve_alns(p,max_time=.1,max_iterations=100)
    assert h['info']['status']=='HEURISTIC_NO_SOLUTION'
    assert not h['info']['proven_infeasible']


def test_cutoff_equality_and_clean_release_equality_in_joint_milp(exact_available):
    base=joint_fixture()
    orders=[replace(base.orders[0],pickup_window=(39,39)),base.orders[1]]
    options=dict(pool_max=1,fixed_assignment={'o1':'b1','o2':'b2'})
    p=JointBatchInstance(base.network,orders,replace(base.operating_policy,cleaning_duration=15),options,synthetic_profile())
    r=solve_exact(p,time_limit=10)
    assert r['audit']['valid'] and r['info']['proven_optimal']
    assert r['audit']['clean_stock'][1]['available']==1
    assert any(t['return_complete']==40 for t in r['e2_trips'] if any(e['order_id']=='o1' and e['kind']=='p' for e in t['events']))
    assert [row['quantity'] for row in r['recovery_rounds']]==[1,1]
    late=JointBatchInstance(base.network,orders,replace(p.operating_policy,cleaning_duration=16),options,synthetic_profile())
    assert solve_exact(late,time_limit=10)['info']['proven_infeasible']


def test_nonzero_preparation_unloading_and_half_minute_ticks(exact_available):
    base=joint_fixture()
    policy=replace(base.operating_policy,time_tick_seconds=30,e1_load_time=3,
                   e2_dispatch_prep_time=2,e2_return_unload_time=1,depot_return_unload_time=2,
                   e1_turnaround_time=2,e2_turnaround_time=2)
    p=JointBatchInstance(base.network,base.orders,policy,{},synthetic_profile())
    r=solve_exact(p,time_limit=10)
    assert r['audit']['valid'] and r['info']['proven_optimal']
    for layer in ('e1','e2'):
        minutes=r['audit']['time_metrics']['working_minutes'][layer]
        assert r['cost_components'][layer+'_work']==pytest.approx(.1*minutes)
    h=solve_alns(p,max_time=.3,max_iterations=300)
    assert h['audit']['valid'] and h['info']['objective']>=r['info']['objective']-1e-7


def test_inactive_delivery_slot_is_not_an_actual_batch(exact_available):
    p=joint_fixture(late=True)
    r=solve_exact(p,time_limit=10)
    assert r['info']['proven_infeasible'] and r['info']['objective'] is None
    heuristic=solve_alns(p,max_time=.1,max_iterations=20)
    assert heuristic['info']['proven_infeasible']


def test_cost_search_delays_pickup_departure_without_changing_return_obligations(exact_available):
    source=joint_fixture(zero=True,fixed_assignment={'o1':'b1','o2':'b2'})
    orders=[replace(source.orders[0],delivery_window=(0,5),pickup_window=(35,35)),source.orders[1]]
    p=JointBatchInstance(source.network,orders,source.operating_policy,source.options,source.cost_profile.config)
    exact=solve_exact(p,time_limit=10)
    heuristic=solve_alns(p,max_time=1,max_iterations=1000,seed=42)
    assert heuristic['audit']['valid'] and heuristic['info']['objective']==pytest.approx(exact['info']['objective'])
    pickup=next(t for t in heuristic['e2_trips'] if all(e['kind']=='p' for e in t['events']))
    assert pickup['departure']==pickup['events'][0]['start']-p.travel('S','C1')
    assert len(heuristic['e1_trips'])==2 and heuristic['audit']['periodic_replay']['valid']


@pytest.mark.parametrize('one_batch,zero',[(True,False),(False,True)])
def test_single_candidate_and_zero_duration_causality(exact_available,one_batch,zero):
    p=joint_fixture(one_batch=one_batch,zero=zero)
    r=solve_exact(p,time_limit=10)
    assert r['info']['proven_optimal'] and r['audit']['valid']
    assert r['audit']['periodic_replay']['valid']


def test_release_is_independent_of_customer_ready_and_empty_domain_is_proven():
    p=joint_fixture()
    orders=[replace(o,release_time=61) for o in p.orders]
    blocked=JointBatchInstance(p.network,orders,p.operating_policy,{},synthetic_profile())
    assert all(not bs for bs in blocked.legal_batches.values())
    for solve,kwargs in [(solve_exact,dict(time_limit=1)),(solve_alns,dict(max_time=.1))]:
        r=solve(blocked,**kwargs)
        assert r['info']['proven_infeasible'] and r['info']['proof_scope']=='empty_necessary_order_batch_domain'


@pytest.mark.parametrize('mutation',[
    lambda r:r['order_batch_assignment'].pop('o1'),
    lambda r:r.update(container_pool_size=0),
    lambda r:r['info'].update(objective=0),
    lambda r:r['cost_components'].update(e1_distance=0),
    lambda r:r.update(economic_profile_hash='forged'),
    lambda r:r['delivery_batch_activation'].update(b1=not r['delivery_batch_activation']['b1']),
    lambda r:r['candidate_schedule'][0].update(cutoff=999),
])
def test_saved_joint_decisions_and_cost_tampering_are_rejected(mutation):
    p=joint_fixture()
    r=solve_alns(p,max_time=.2,max_iterations=50)
    assert r['audit']['valid']
    mutation(r)
    assert not audit_solution(p,r)['valid']


def test_json_reload_is_audited_against_original_orders():
    p=joint_fixture()
    r=json.loads(json.dumps(solve_alns(p,max_time=.2,max_iterations=100),allow_nan=False))
    assert audit_solution(p,r)['valid']


@pytest.mark.parametrize('field,value',[
    ('container_pool_cost_per_period',None),('e1_distance_cost_per_km',-1),
    ('container_price',float('nan')),('currency','CNY'),
    ('pool_cost_basis','purchase_price_divided_by_uses'),
])
def test_cost_parameters_do_not_hide_unknown_or_synthetic_money(field,value):
    config=synthetic_profile(); config[field]=value
    with pytest.raises(ValueError):
        JointCostProfile(config)


def test_rental_wear_and_cleaning_cannot_be_charged_twice():
    config={**synthetic_profile(),'pool_cost_basis':'rental','rental_includes':['wear']}
    with pytest.raises(ValueError,match='wear'):
        JointCostProfile(config)
    config.update(container_lifetime_uses=None,rental_includes=['cleaning'])
    with pytest.raises(ValueError,match='cleaning'):
        JointCostProfile(config)


def test_missing_real_evidence_never_becomes_a_calibrated_profile():
    config={**synthetic_profile(),'currency':'CNY','source_type':'calibrated','pool_cost_basis':'owned_capital_charge'}
    with pytest.raises(ValueError,match='evidence register'):
        JointCostProfile(config)


@pytest.mark.parametrize('fault',['unit','source','readiness','missing_value'])
def test_calibration_schema_rejects_mismatched_units_or_unverifiable_metadata(tmp_path,fault):
    from models.cost.joint_batch import CALIBRATED_UNITS
    # Artificial schema fixture, never used for an economic experiment.
    config={**synthetic_profile(),'currency':'CNY','source_type':'calibrated','pool_cost_basis':'owned_capital_charge'}
    evidence=dict(model=6,ready_for_calibrated_CNY_optimization=True,
                  sources={'fixture':dict(title='Artificial schema fixture',publisher='Test fixture',
                      url='https://example.org/fixture',checked_on='2026-10-04',verification='test_metadata_only')},
                  parameter_register={key:dict(calibrated_value=config[key],unit=unit,
                       applicability='matched_public_reference',sources=['fixture']) for key,unit in CALIBRATED_UNITS.items()})
    if fault=='unit':
        evidence['parameter_register']['e1_work_cost_per_minute']['unit']='CNY/year'
    elif fault=='source':
        evidence['sources']['fixture'].pop('publisher')
    elif fault=='readiness':
        evidence['ready_for_calibrated_CNY_optimization']='true'
    else:
        evidence['parameter_register']['container_price']['calibrated_value']=None
    path=tmp_path/'evidence.json'; path.write_text(json.dumps(evidence)); config['evidence_file']=str(path)
    with pytest.raises(ValueError):
        JointCostProfile(config)


def test_frozen_comparison_runs_matched_cost_ablation_and_all_seeds(tmp_path,exact_available):
    from experiments.runs.run_model6_comparison import main
    manifest=build_suite(tmp_path/'data')
    value=json.loads(manifest.read_text()); value['cases']=value['cases'][:1]
    manifest.write_text(json.dumps(value))
    out=main(['--manifest',str(manifest),'--exact-time','5','--time','.1','--iterations','50',
              # This checks the nine frozen jobs and audits, not process spawn
              # speed. Keep solver budgets unchanged and allow cold imports.
              '--seeds','42','43','--workers','2','--timeout-overhead','60',
              '--output',str(tmp_path/'comparison')])
    rows=json.loads((out/'rows.json').read_text())
    assert len(rows)==9 and all(r['valid'] for r in rows)
    exact=next(r for r in rows if r['treatment']=='M6_joint' and r['solver']=='exact')
    assert exact['proven_optimal'] and exact['cost']==pytest.approx(61)
    assert {r['treatment'] for r in rows}=={'M5_distance','M6_fixed','M6_pool_only','M6_batch_only','M6_joint'}
    for row in rows:
        if row['treatment']=='M6_joint' and row['solver']=='alns':
            assert row['joint_optimal_gap_percent']==pytest.approx(100*(row['cost']-exact['cost'])/exact['cost'])
            assert row['joint_optimal_gap_percent']>=-1e-6
    assert (out/'source_snapshot/models/routing/model_6/exact/model.py').exists()
    assert 'synthetic_units' in (out/'report.md').read_text()
    baseline=next(r for r in rows if r['treatment']=='M5_distance' and r['solver']=='exact')
    assert baseline['total_container_uses']==2 and baseline['order_count']==2
    assert baseline['minimum_pool_for_schedule']<=baseline['pool']==2
    report=(out/'report.md').read_text()
    assert 'M5预设箱池' in report and 'M5路线最小需要' in report
    from experiments.analysis.summarize_model6_resources import summarize
    resources=summarize(out)
    assert resources['valid_runs']==9 and not resources['no_incumbent_runs']
    proof=resources['cases'][0]['resource_certificate']
    assert proof['pool_minimum_proven']
    from tools.configuration.loader import load_routing_case
    from models.common.cycle import load_instance
    from experiments.analysis.summarize_model6_resources import resource_certificate
    p=load_instance(*load_routing_case(out/'cases/0_M6_joint.json',use_pool_config=False))
    direct=resource_certificate(p,json.loads((out/exact['run']/'result.json').read_text()))
    assert direct['cost_optimal_proven'] and direct['pool_minimum_proven']


def test_evidence_snapshot_is_shared_and_hashes_describe_rewritten_profile(tmp_path):
    import hashlib
    from experiments.runs.run_model6_comparison import freeze_case
    manifest=build_suite(tmp_path/'data')
    pair=json.loads(manifest.read_text())['cases'][0]
    source=Path(pair['m6_case']).parent/'economic.json'
    evidence=tmp_path/'evidence.json'; evidence.write_text(json.dumps({'model':6,'synthetic_fixture':True}))
    profile=json.loads(source.read_text()); profile['evidence_file']=str(evidence)
    source.write_text(json.dumps(profile))
    out=tmp_path/'snapshot'; (out/'inputs').mkdir(parents=True)
    c5,_=freeze_case(pair['m5_case'],out,'M5')
    c6,hashes=freeze_case(pair['m6_case'],out,'M6')
    assert Path(c5['economic_config']).read_bytes()==Path(c6['economic_config']).read_bytes()
    record=hashes[str(source)]
    assert record['sha256']==hashlib.sha256(source.read_bytes()).hexdigest()
    assert record['snapshot_sha256']==hashlib.sha256(Path(c6['economic_config']).read_bytes()).hexdigest()


def test_case_loading_service_dispatch_and_cli(tmp_path):
    from tools.configuration.loader import load_routing_case
    from models.common.cycle import load_instance
    from models.routing.service import solve_routing
    from cli.route import main
    from cli.cost import main as price
    manifest=build_suite(tmp_path/'data')
    case=json.loads(manifest.read_text())['cases'][0]['m6_case']
    config,fleet=load_routing_case(case)
    p=load_instance(config,fleet)
    r=solve_routing(model=6,method='alns',problem=p,alns_time=.1,alns_iter=50)
    assert r['audit']['valid'] and r['model']==6
    saved=main(['--case',case,'--time','.1','--iterations','50','--output',str(tmp_path/'run')])
    assert saved['audit']['valid']
    priced=price(['--case',case,'--route-result',str(tmp_path/'run/result.json'),
                  '--output',str(tmp_path/'cost')])
    assert priced['total']==saved['info']['objective']
    inputs=list((tmp_path/'run/inputs').iterdir())
    assert any('economic.json' in f.name for f in inputs)


@pytest.mark.parametrize('method',['exact','alns'])
def test_m6_algorithms_are_independent_in_fresh_process(method,exact_available):
    script=r'''
import importlib.abc,sys
method=sys.argv[1]
opposite='alns' if method=='exact' else 'exact'
class Guard(importlib.abc.MetaPathFinder):
    def find_spec(self,fullname,path=None,target=None):
        if (fullname.startswith('models.routing.model_') and '.'+opposite in fullname) or (method=='alns' and fullname.startswith('gurobipy')):
            raise AssertionError('Unexpected dependency '+fullname)
sys.meta_path.insert(0,Guard())
from tests.routing.test_model6_joint import joint_fixture
from models.routing.service import solve_routing
p=joint_fixture()
r=solve_routing(model=6,method=method,problem=p,exact_time=10,alns_time=.2,alns_iter=100)
assert r['audit']['valid'],r
print('M6_ISOLATED',method)
'''
    completed=subprocess.run([sys.executable,'-c',script,method],capture_output=True,text=True,
                             cwd=Path(__file__).resolve().parents[2],timeout=30)
    assert completed.returncode==0,completed.stdout+completed.stderr


def test_actual_alns_search_changes_batches_and_matches_small_exact_reference(exact_available):
    p=joint_fixture()
    exact=solve_exact(p,time_limit=10)
    for seed in (42,43,44):
        # Check the fixed search trajectory, not whether a loaded host can
        # finish it inside 0.3 seconds. Performance budgets are benchmarked
        # separately; the optimum and operator assertions remain unchanged.
        r=solve_alns(p,max_time=3,max_iterations=300,seed=seed)
        assert r['info']['iterations']==300
        assert r['audit']['valid'] and not r['info']['proven_optimal']
        assert r['info']['objective']==pytest.approx(exact['info']['objective'])
        assert r['info']['operator_attempts']['destroy_random_batch']>0


def test_resource_minima_can_be_certified_without_cost_optimality():
    from experiments.analysis.summarize_model6_resources import resource_certificate
    low=joint_fixture()
    low_result=solve_alns(low,max_time=.2,max_iterations=100,seed=42)
    # A feasible plan with an unused spare box need not attain the pool bound.
    from models.common.cycle.joint import lift_result
    low_result=lift_result(low,low_result,low_result['order_batch_assignment'],low.total,'alns')
    assert not resource_certificate(low,low_result)['pool_minimum_proven']
    high=joint_fixture(pool_rate=30)
    r=solve_alns(high,max_time=.2,max_iterations=100,seed=42)
    c=resource_certificate(high,r)
    assert c['pool_lower_bound']==1 and c['delivery_batch_lower_bound_given_pool']==2
    assert c['lexicographic_pool_then_batches_proven'] and not c['cost_optimal_proven']
    changed=deepcopy(r);changed['container_pool_size']=0
    with pytest.raises(ValueError,match='invalid schedule'):
        resource_certificate(high,changed)
