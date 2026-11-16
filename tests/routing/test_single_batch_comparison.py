"""Cross-model scope, free assignment, certificates and economic regression."""
from copy import deepcopy
from dataclasses import replace
import json

import pytest

from tools.configuration.loader import load_json, load_routing_case
from models.common.domain.node import Depot, Satellite, Customer
from models.common.domain.instance import ProblemInstance
from models.routing.service import solve_routing
from models.common.cycle import MultiBatchInstance, MultiBatchPolicy, Order, load_instance
from models.common.cycle.audit import audit_solution
from models.common.cycle.single_batch_cost import SingleBatchCostInstance
from models.common.cycle.state import empty_result
from models.common.cycle.timing import normalize_schedule
from models.cost.multibatch import evaluate_cycle_cost
from models.cost.single_batch import compare_single_batch, lifecycle_pair


def problem(model, window=(0, 25), pickup=(0, 30), pool=1, duplicate=False):
    policy = MultiBatchPolicy.from_config(dict(policy_version='multi_batch_cutoff_v1', cycle_start=0,
        cycle_end=65, batches=[dict(id='b1', delivery_duration=30, return_duration=30)],
        container_pool_size=pool, cleaning_duration=5, time_tick_seconds=60, e2_max_trips_per_vehicle=2))
    nodes = {'D': Depot('D', 0, 0), 'S': Satellite('S', 10, 0), 'T': Satellite('T', 1, 0),
             'C': Customer('C', 2, 0, 0, 0, (0, 65), (0, 65), 'S')}
    fleet = dict(E1_vehicle_count=1, vehicles_per_satellite=1, E1_Qk=50, E1_Ck=50,
                 E2_Qv=50, E2_Cv=50, container_weight=1, container_volume=1)
    orders = [Order('a', 'C', 'S', 'b1', 1, 1, window, pickup, 0)]
    if duplicate:
        orders.append(replace(orders[0], id='b'))
    return MultiBatchInstance(ProblemInstance(nodes, fleet), orders, policy, model=model)


def solve(p, method='alns', **options):
    return solve_routing(model=p.model_number, method=method, problem=p,
                         alns_time=.15, alns_iter=100, exact_time=5, **options)


@pytest.mark.parametrize('model', [1, 2, 3, 4])
@pytest.mark.parametrize('method', ['alns', 'exact'])
def test_four_models_audit_and_replay(model, method):
    if method == 'exact':
        pytest.importorskip('gurobipy')
    p = problem(model)
    result = solve(p, method)
    audit = audit_solution(p, result)
    assert audit['valid'], audit['errors']
    assert audit['periodic_replay']['valid']
    assert result['schedule_convention'] == 'earliest_feasible_fixed_route_B1'
    assert result['model'] == model
    assert audit['container_balance']['returned_to_depot'] == (1 if model >= 3 else 0)
    if model < 3:
        assert all(e['kind'] == 'd' for layer in ('e1', 'e2') for r in result[layer+'_trips'] for e in r['events'])
        assert audit['container_balance']['pool'] == 0
    if model in (2, 4) and method == 'exact':
        assert result['assignment'] == {'C': 'T'}
        assert result['info']['exact_strategy'] == 'gurobi_milp'
        assert 'assignments_closed' not in result['info']
        assert result['info']['proven_optimal']
        assert result['info']['objective'] == 4


def test_pure_delivery_ignores_pickup_feasibility_and_pool():
    p = problem(1, pickup=(0, 0), pool=0)
    result = solve(p)
    assert result['audit']['valid']
    assert not solve(problem(3, pickup=(0, 0)))['info']['objective']
    late = problem(1, window=(31, 35))
    assert solve(late)['info']['proven_infeasible']
    changed = deepcopy(result)
    changed['e2_trips'][0]['events'][0]['kind'] = 'p'
    assert not audit_solution(p, changed)['valid']


@pytest.mark.parametrize('model', [2, 4])
def test_free_assignment_can_rescue_fixed_infeasibility(model):
    fixed = problem(model-1, window=(0, 5))
    assert solve(fixed)['info']['proven_infeasible']
    free = problem(model, window=(0, 5), duplicate=True, pool=2)
    result = solve(free)
    assert result['audit']['valid'] and result['assignment'] == {'C': 'T'}
    assert len({t['satellite_id'] for t in result['e2_trips']}) == 1
    changed = deepcopy(result)
    changed['assignment'] = {'C': 'S'}
    assert not audit_solution(free, changed)['valid']
    changed['assignment'] = {'C': 'T', 'invented': 'T'}
    assert not audit_solution(free, changed)['valid']


def test_request_cannot_silently_switch_model():
    with pytest.raises(ValueError, match='does not match'):
        solve_routing(model=1, method='alns', problem=problem(3))




def test_normalization_removes_arbitrary_slack_and_preserves_distance():
    p = problem(1)
    result = solve(p)
    base = normalize_schedule(p, result)
    result['e2_trips'][0]['events'][0]['start'] += 2
    result['e2_trips'][0]['return_complete'] += 4
    assert audit_solution(p, result)['valid']
    normalized = normalize_schedule(p, result)
    assert normalized['e2_trips'] == base['e2_trips']
    assert normalized['audit']['objective'] == base['audit']['objective']
    assert normalized['audit']['time_metrics'] == base['audit']['time_metrics']


@pytest.mark.parametrize('lifetime_uses', [None, 50])
def test_cost_prices_only_the_packaging_actually_used(lifetime_uses):
    rates = load_json('config/cost/synthetic/single_batch_synthetic.json')
    if lifetime_uses is not None:
        rates['container_lifetime_uses'] = lifetime_uses
    p = problem(1)
    cost = evaluate_cycle_cost(p, solve(p), rates)
    assert cost['initial_pool_investment'] == 0
    assert cost['components']['single_use_purchase'] == 5
    assert cost['components']['single_use_disposal'] == .2
    assert 'cleaning' not in cost['components']
    if lifetime_uses is not None:
        assert cost['container_use_depreciation_per_cycle'] == 0
        assert cost['period_cost_with_use_depreciation'] == cost['period_operating_cost']


def test_replacement_can_undo_an_earlier_break_even():
    pure = dict(model=1, period_operating_cost=10, components={'e1_distance': 3})
    reuse = dict(model=3, period_operating_cost=4, initial_pool_investment=10)
    comparison = lifecycle_pair(pure, reuse, horizon=4, lifetime=2, packages=1, disposal_rate=0)
    assert [row['saving'] for row in comparison['timeline']] == [-4, 2, -2, 4]
    assert comparison['first_break_even_within_horizon'] == 2
    assert comparison['no_more_expensive_from_cycle_through_horizon'] == 4
    assert comparison['reusable_total'] == 36
    assert comparison['lifetime_average_single_use_purchase_price_threshold'] == 6
    reuse['period_operating_cost'] = 11
    assert lifecycle_pair(pure, reuse, horizon=100, lifetime=2, packages=1, disposal_rate=0)['first_break_even_within_horizon'] is None


def test_comparison_checks_shared_inputs_and_keeps_failed_pairs():
    problems = {i: problem(i) for i in range(1, 5)}
    results = {i: solve(p) for i, p in problems.items()}
    rates = load_json('config/cost/synthetic/single_batch_synthetic.json')
    compared = compare_single_batch(problems, results, rates)
    assert all(p['available'] for p in compared['comparisons'].values())
    assert not compared['cost_is_optimized']
    assert len(compared['sensitivity']) == 24
    low = next(r for r in compared['sensitivity'] if r['single_use_model'] == 1 and
               r['single_use_package_price'] == 1 and r['pool_lifetime_cycles'] == 10)
    high = next(r for r in compared['sensitivity'] if r['single_use_model'] == 1 and
                r['single_use_package_price'] == 5 and r['pool_lifetime_cycles'] == 10)
    assert high['horizon_saving']-low['horizon_saving'] == pytest.approx(4*30)
    results[4] = empty_result(problems[4], 'alns')
    compared = compare_single_batch(problems, results, rates)
    assert 4 in compared['failures'] and not compared['comparisons']['M2_vs_M4']['available']
    problems[4] = problem(4, pool=2)
    with pytest.raises(ValueError, match='share the same'):
        compare_single_batch(problems, results, rates)


@pytest.mark.parametrize(('pool_basis', 'method'), [
    ('owned_capital_charge', 'exact'), ('rental', 'alns'), ('synthetic_pool_charge', 'alns'),
])
def test_period_comparison_preserves_objective_and_handles_pool_accounting(pool_basis, method):
    rates = load_json('config/cost/synthetic/daily_routing.json')
    rates.update(pool_cost_basis=pool_basis, container_pool_cost_per_period=2,
                 e1_trip_cost=1.25, e2_trip_cost=2.5, e1_work_cost_per_minute=.1,
                 container_price=10, container_lifetime_uses=20, evaluation_cycles=3)
    if pool_basis == 'owned_capital_charge':
        rates['pool_lifetime_cycles'] = 2
    problems = {}
    for i in range(1, 5):
        base = problem(i)
        problems[i] = SingleBatchCostInstance(base.network, base.orders, base.operating_policy, rates, model=i)
    results = {i: solve(p, method, seed=42) for i, p in problems.items()}
    compared = compare_single_batch(problems, results, rates)
    assert compared['cost_is_optimized']
    for i, result in results.items():
        cost = compared['costs'][i]
        assert cost['total'] == pytest.approx(result['info']['objective'])
        assert cost['economic_profile_hash'] == result['economic_profile_hash']
        assert cost['components']['e1_trips'] == pytest.approx(result['audit']['e1_trip_count'] * 1.25)
        assert cost['components']['e2_trips'] == pytest.approx(result['audit']['e2_trip_count'] * 2.5)
    pure, reuse = compared['costs'][1], compared['costs'][3]
    assert reuse['components']['container_pool'] == 2
    assert reuse['components']['container_wear'] == .5
    pair = compared['comparisons']['M1_vs_M3']
    saving = pure['total'] - reuse['total']
    assert pair['period_saving'] == pytest.approx(saving)
    assert pair['period_saving_per_delivery'] == pytest.approx(saving)
    assert pair['period_single_use_purchase_price_threshold'] == pytest.approx(5 - saving)
    if pool_basis == 'owned_capital_charge':
        assert pair['timeline'][-1]['pool_purchases'] == 2
        assert pair['horizon_saving'] == pytest.approx(3 * (saving + .5) - 20)
    elif pool_basis == 'rental':
        assert pair['initial_pool_investment'] == 0
        assert pair['timeline'][-1]['pool_purchases'] == 0
        assert pair['horizon_saving'] == pytest.approx(3 * saving)
    else:
        assert not pair['lifecycle_available']
        assert 'horizon_saving' not in pair


@pytest.mark.parametrize('economic_override', [False, True])
def test_runner_freezes_all_models_and_reports_each_seed(tmp_path, economic_override):
    from experiments.runs.run_single_batch_comparison import main
    out = tmp_path/'study'
    args = ['--solver', 'alns', '--time', '.2', '--iterations', '0', '--seeds', '42', '--output', str(out),
            '--loss-rates', '0', '0.04', '--other-incidental-cost-per-use', '0.1']
    if economic_override:
        args.extend(['--economic-config', 'config/cost/synthetic/single_batch_synthetic.json'])
        args.extend(['--loss-replacement-cost-per-box', '20'])
    report = main(args)
    assert len(report['runs']) == 4 and set(report['comparisons']) == {'alns_seed42'}
    comparison = report['comparisons']['alns_seed42']
    assert all(row['valid'] for row in report['runs'])
    for row in report['runs']:
        assert comparison['costs'][row['model']]['total'] == pytest.approx(row['objective'])
    markdown = (out/'report.md').read_text(encoding='utf-8')
    assert '成本目标值' in markdown and '纸箱单价持平阈值' in markdown
    assert '偶然成本情景' in markdown and '4.00%' in markdown
    assert (out/'runs.csv').is_file()
    manifest = json.loads((out/'manifest.json').read_text(encoding='utf-8'))
    assert len(manifest['jobs']) == 4 and manifest['source_sha256']
    parameters = manifest['economic_parameters']
    assert parameters['e1_distance_cost_per_km'] == (1 if economic_override else .1)
    assert parameters['container_pool_cost_per_period'] == (0 if economic_override else 5)
    incidental = comparison['incidental_costs']['M1_vs_M3']
    assumptions = manifest['incidental_cost_parameters']
    price = 20 if economic_override else parameters['container_price']
    assert assumptions == dict(loss_rates=[0, .04], other_cost_per_use=.1, replacement_cost_per_box=price)
    pair = comparison['comparisons']['M1_vs_M3']
    scenario = incidental['scenarios'][1]
    extra = incidental['packages_per_period']*(.04*price+.1)
    assert scenario['period_incidental_cost'] == pytest.approx(extra)
    assert scenario['period_saving'] == pytest.approx(pair['period_saving']-extra)
    if economic_override:
        assert scenario['horizon_saving'] == pytest.approx(pair['horizon_saving']-pair['evaluation_cycles']*extra)
    else:
        assert scenario['horizon_saving'] is None
    for model in range(1, 5):
        cfg, fleet = load_routing_case(out/f'case_M{model}.json')
        assert cfg['model'] == model
        assert str(out/'inputs') in cfg['orders']
        assert load_json(cfg['economic_config']) == parameters
    with pytest.raises(ValueError, match='overwrite'):
        main(['--output', str(out)])
