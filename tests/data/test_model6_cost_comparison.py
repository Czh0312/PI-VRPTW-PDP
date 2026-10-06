"""Regression coverage for post-pricing, baseline selection and matched prices."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from experiments.analysis.summarize_model6_costs import cost_change, summarize
from models.cost.comparison import evaluate_comparison_cost
from models.routing.common.cycle import load_instance
from models.routing.common.cycle.audit import audit_solution
from configuration.loader import load_routing_case
from tools.data.build_model6_demo_suite import build_suite


def test_m5_cli_prices_pool_trips_and_wear_after_distance_solving(tmp_path):
    from cli.route import main as route
    from cli.cost import main as price
    manifest = json.loads(build_suite(tmp_path/'data').read_text(encoding='utf-8'))
    case = manifest['cases'][0]['m5_case']
    run = tmp_path/'route'
    result = route(['--case',case,'--solver','alns','--time','.3','--iterations','30','--output',str(run)])
    original = deepcopy(result)
    cost = price(['--case',case,'--route-result',str(run/'result.json'),'--output',str(tmp_path/'cost')])
    problem = load_instance(*load_routing_case(case, use_pool_config=False))
    audited = audit_solution(problem,result)
    # Fixture prices: 5/box, 1/trip, 10/50 wear/use. These were missing in
    # the old M5 cost command, even when the M6 economic profile was supplied.
    assert cost['components']['container_pool'] == 5*problem.operating_policy.container_pool_size
    assert cost['components']['e1_trips'] == audited['e1_trip_count']
    assert cost['components']['e2_trips'] == audited['e2_trip_count']
    assert cost['components']['container_wear'] == pytest.approx(problem.total*10/50)
    assert cost['total'] == pytest.approx(sum(cost['components'].values()))
    assert cost['route_objective_mode']=='distance' and not cost['cost_is_optimized']
    reference = cost['schedule_minimum_pool_reference']
    assert reference['container_pool_size']==audited['minimum_pool_for_schedule']
    assert cost['total']-reference['total']==pytest.approx(
        5*(problem.operating_policy.container_pool_size-audited['minimum_pool_for_schedule']))
    assert result==original
    invalid = deepcopy(result)
    invalid['e2_trips']=[]
    with pytest.raises(ValueError, match='invalid route'):
        evaluate_comparison_cost(problem,invalid,load_routing_case(case,use_pool_config=False)[0]['economic_config'])


def test_m3_cli_uses_joint_prices_without_changing_single_batch_route(tmp_path):
    from cli.cost import main as price
    from configuration.loader import load_json
    case = 'config/cases/single_batch_demo.json'
    source = 'output/experiments/static_batch_suite_v1/data_audit_20260929/mechanism_forced_late_b1.json'
    config, fleet = load_routing_case(case, use_pool_config=False)
    problem = load_instance(config, fleet)
    result = load_json(source)
    original = deepcopy(result)
    economic = 'data/model6/uniform_v2_20261005/lade_0823_10aoi/spread_medium/economic.json'
    parameters = load_json(economic)
    audit = audit_solution(problem, result)
    assert audit['valid']
    cost = price(['--case', case, '--route-result', source,
                  '--economic-config', economic, '--output', str(tmp_path/'m3_cost')])
    from models.cost.joint_batch import JointCostProfile
    reference = JointCostProfile(parameters).evaluate(audit, problem.operating_policy.container_pool_size)
    assert cost['model'] == 3
    assert cost['components'] == pytest.approx(reference['components'])
    assert cost['total'] == pytest.approx(reference['total'])
    assert cost['economic_profile_hash'] == reference['economic_profile_hash']
    assert cost['components']['e1_trips'] == audit['e1_trip_count']*parameters['e1_trip_cost']
    assert cost['components']['container_wear'] == problem.total*parameters['container_price']/parameters['container_lifetime_uses']
    assert cost['route_objective_mode'] == 'distance' and not cost['cost_is_optimized']
    assert result == original
    corrupt = deepcopy(result)
    corrupt['e2_trips'] = []
    with pytest.raises(ValueError, match='invalid route'):
        evaluate_comparison_cost(problem, corrupt, parameters)


def _priced_row(root, run, model, seed, distance, cost, pool=2, profile='same'):
    folder=root/run
    folder.mkdir()
    value=dict(total=cost, components={'container_pool':5*pool,'transport':cost-5*pool},
               economic_profile_hash=profile,currency='synthetic_units',evaluation_period='one cycle',
               cost_basis='disjoint_distance_time_fixed',work_time_basis='occupied_route_minutes',
               initial_pool_investment=pool*10)
    if model=='M5_distance':
        value['schedule_minimum_pool_reference']={'total':cost-5,'container_pool_size':pool-1}
    (folder/'comparison_cost.json').write_text(json.dumps(value),encoding='utf-8')
    return dict(case='case',run=run,treatment=model,solver='alns',seed=seed,valid=True,status='FEASIBLE',
                cost=cost,distance=distance,pool=pool,minimum_pool_for_schedule=pool-1,
                batches=2,e1_trips=2,e2_trips=2,proven_optimal=False,economic_profile_hash=profile)


def test_cost_summary_uses_m5_distance_and_preserves_failures(tmp_path):
    # A smaller pool must not select the longer-distance M5 solution.
    rows=[_priced_row(tmp_path,'m5_long','M5_distance',42,20,100,pool=2),
          _priced_row(tmp_path,'m5_short','M5_distance',43,10,120,pool=4),
          _priced_row(tmp_path,'m6','M6_uniform',42,25,90,pool=3)]
    failure={**rows[-1],'run':'failed','seed':43,'valid':False,'cost':None,'status':'TIME_LIMIT'}
    rows.append(failure)
    result=summarize(tmp_path,rows,[{'name':'case'}])
    selected=result['cases'][0]
    assert selected['m5']['run']=='m5_short'
    assert selected['comparison']=={'savings':30,'savings_percent':25}
    assert selected['schedule_minimum_pool_comparison']['savings']==25
    assert selected['methods']['alns']['M5_distance']['mean']==110
    assert selected['methods']['alns']['M6_uniform']['valid']==1
    pair=result['paired_runs'][1]
    assert pair['m6_status']=='TIME_LIMIT' and pair['savings'] is None
    assert result['valid_runs']==3 and result['planned_runs']==4
    assert all((tmp_path/name).exists() for name in (
        'cost_report.md','cost_summary.json','cost_comparison.csv','cost_pairs.csv','cost_components.csv'))
    assert cost_change(0,5)=={'savings':-5,'savings_percent':None}
    assert cost_change(10,12)=={'savings':-2,'savings_percent':-20}


def test_summary_rejects_different_cost_contracts(tmp_path):
    rows=[_priced_row(tmp_path,'m5','M5_distance',42,10,100),
          _priced_row(tmp_path,'m6','M6_uniform',42,10,90,profile='different')]
    with pytest.raises(ValueError,match='same economic profile'):
        summarize(tmp_path,rows,[{'name':'case'}])


def test_uniform_runner_post_prices_m5_and_exports_cost_pairs(tmp_path):
    from experiments.runs.run_model6_uniform_comparison import main
    manifest=build_suite(tmp_path/'data')
    payload=json.loads(manifest.read_text(encoding='utf-8'))
    record=payload['cases'][0]
    path=Path(record['m6_case'])
    config=json.loads(path.read_text(encoding='utf-8'))
    config['joint_batch_options']={'schedule_mode':'uniform_cycle','batch_count_candidates':[1,2]}
    path.write_text(json.dumps(config),encoding='utf-8')
    payload.update(schedule_mode='uniform_cycle',cases=[{**record,'total_container_uses':2}])
    manifest.write_text(json.dumps(payload),encoding='utf-8')
    out=main(['--manifest',str(manifest),'--output',str(tmp_path/'study'),
              '--solvers','alns','--time','.4','--iterations','30','--seeds','42'])
    rows=json.loads((out/'rows.json').read_text(encoding='utf-8'))
    assert len(rows)==2 and all(r['valid'] for r in rows)
    summary=json.loads((out/'uniform_summary.json').read_text(encoding='utf-8'))
    assert summary['cases'][0]['m5']['cost'] is not None
    assert summary['cases'][0]['cost_comparison']['savings'] is not None
    cost=json.loads((out/rows[0]['run']/'comparison_cost.json').read_text(encoding='utf-8'))
    assert cost['route_objective_mode']=='distance' and 'schedule_minimum_pool_reference' in cost
    # Refuse to run an unmatched pair before dispatching any worker.
    profile=Path(config['economic_config'])
    changed=json.loads(profile.read_text(encoding='utf-8'))
    changed['container_pool_cost_per_period']+=1
    other=tmp_path/'different.json'
    other.write_text(json.dumps(changed),encoding='utf-8')
    config['economic_config']=str(other)
    path.write_text(json.dumps(config),encoding='utf-8')
    with pytest.raises(ValueError,match='same economic profile'):
        main(['--manifest',str(manifest),'--output',str(tmp_path/'mismatch'),'--solvers','alns'])
