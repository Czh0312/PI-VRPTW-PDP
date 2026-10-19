"""Calendar domain extensions, exhaustive MILP oracle, physical replay/isolation."""
from copy import deepcopy
from dataclasses import replace
from importlib import import_module
import itertools
import json
from pathlib import Path
import subprocess
import sys

import pytest

from models.common.cycle.calendar import CalendarCycleInstance
from models.common.cycle.joint import JointBatchInstance, joint_empty_result
from models.common.cycle.audit import audit_solution
from models.routing.service import solve_routing
from tests.routing.test_model6_joint import joint_fixture, exact_available


def calendar_fixture(number=8, counts=(1, 2), late_release=False, **options):
    source = joint_fixture(pool_rate=30)
    orders = source.orders
    if late_release:
        orders = [replace(orders[0], delivery_window=(0, 20)),
                  replace(orders[1], release_time=70, delivery_window=(75, 90))]
    return CalendarCycleInstance(source.network, orders, source.operating_policy,
                                 dict(batch_count_candidates=list(counts), **options),
                                 source.cost_profile.config, model=number)


@pytest.mark.parametrize('number', [7, 8])
def test_public_entry_identity_dispatch_and_cost(number, exact_available):
    from models.cost.comparison import evaluate_comparison_cost
    p = calendar_fixture(number)
    for method in ('exact', 'alns'):
        entry = import_module(f'models.routing.model_{number}.{method}')
        assert entry.MODEL_NUMBER == number
        r = solve_routing(model=number, method=method, problem=p, exact_time=10, alns_time=.5, alns_iter=200)
        assert r['model'] == number and r['model_module'] == entry.__name__
        assert r['solver_module'] == entry.__name__+'.solver'
        assert r['batch_mode'] == entry.BATCH_MODE
        assert r['audit']['valid'] and r['audit']['periodic_replay']['valid']
        cost = evaluate_comparison_cost(p, r, p.cost_profile.config)
        assert cost['total'] == pytest.approx(r['info']['objective'])
        assert cost['cost_is_optimized'] and cost['container_pool_size'] == r['container_pool_size']
        if method == 'alns':
            assert r['info']['iterations'] <= 200
            assert sum(row['iterations'] for row in r['count_search']) == r['info']['iterations']
            assert not r['info']['proven_optimal']


def test_m7_independent_cutoffs_strictly_improve_shared_cutoffs(exact_available):
    from tests.routing.test_model6_uniform import uniform_fixture
    p = calendar_fixture(7, counts=(2,))
    m6 = solve_routing(model=6, method='exact', problem=uniform_fixture(pool_rate=30, counts=(2,)), exact_time=10)
    m7 = solve_routing(model=7, method='exact', problem=p, exact_time=10)
    assert m6['info']['proven_optimal'] and m7['info']['proven_optimal']
    assert m6['info']['objective'] == pytest.approx(80.2)
    assert m7['info']['objective'] == pytest.approx(80.)
    assert m7['batch_durations_ticks'] == [65, 65]
    assert len(set(m7['cutoff_offsets_ticks'].values())) == 2
    assert not m7['shared_cutoff']


def test_m8_moving_boundary_restores_release_feasibility(exact_available):
    fixed, flexible = (calendar_fixture(n, counts=(2,), late_release=True) for n in (7, 8))
    assert solve_routing(model=7, method='exact', problem=fixed, exact_time=10)['info']['proven_infeasible']
    assert flexible.for_count(2).legal_batches['o2'] == ('b2',)
    for method in ('exact', 'alns'):
        r = solve_routing(model=8, method=method, problem=flexible, exact_time=10, alns_time=1, alns_iter=500)
        assert r['audit']['valid'] and r['audit']['periodic_replay']['valid']
        assert 70 <= r['batch_boundaries'][1] <= 83
        assert r['order_batch_assignment']['o2'] == 'b2'
        assert r['container_pool_size'] == 1
        if method == 'alns':
            assert r['info']['operator_attempts']['shift_boundary'] > 0
            assert r['info']['operator_attempts']['align_boundary'] > 0
            assert r['info']['operator_attempts']['redistribute_lengths'] > 0


def test_integer_milp_matches_exhaustive_fixed_calendar_oracle(exact_available):
    from models.routing.model_6.exact import solve_exact
    source = joint_fixture(zero=True, cleaning=1, pool_rate=30)
    policy = replace(source.operating_policy, cycle_end=8,
                     batches=tuple(replace(b, start=i*4, cutoff=i*4+2, end=(i+1)*4)
                                   for i, b in enumerate(source.operating_policy.batches)))
    orders = [replace(o, delivery_window=(0, 6), pickup_window=(0, 7)) for o in source.orders]
    # M7's fixed calendars are a subset of M8's. The physical input and M6
    # reference formulation are identical for each shared (boundaries, cuts).
    # Reuse only reference scalar results; still solve M7 and M8 separately.
    fixed_optima = {}
    for number in (7, 8):
        p = CalendarCycleInstance(source.network, orders, policy, dict(batch_count_candidates=[1, 2]),
                                  source.cost_profile.config, model=number)
        full = solve_routing(model=number, method='exact', problem=p, exact_time=15)
        assert full['info']['proven_optimal'] and full['audit']['valid'], number
        costs = []
        for boundaries in [(0, 8)]+[(0, t, 8) for t in ([4] if number == 7 else range(2, 7))]:
            child = p.fixed_calendar(len(boundaries)-1, boundaries)
            for cuts in itertools.product(*(range(a+1, b) for a, b in zip(boundaries, boundaries[1:]))):
                key = boundaries, cuts
                if key not in fixed_optima:
                    fixed_policy = replace(policy, batches=tuple(replace(b, cutoff=c) for b, c in
                                                                 zip(child.operating_policy.batches, cuts)))
                    fixed = JointBatchInstance(child.network, child.orders, fixed_policy, {}, p.cost_profile.config)
                    r = solve_exact(fixed, time_limit=3)
                    assert r['info']['proven_optimal'] or r['info']['proven_infeasible'], key
                    fixed_optima[key] = r['info']['objective'] if r.get('audit', {}).get('valid') else None
                if fixed_optima[key] is not None:
                    costs.append(fixed_optima[key])
        assert full['info']['objective'] == pytest.approx(min(costs)), number


@pytest.mark.parametrize('number', [7, 8])
def test_calendar_changes_preserve_absolute_physical_inputs(number):
    p = calendar_fixture(number)
    for count in p.batch_count_candidates:
        child = p.fixed_calendar(count)
        assert child.network is p.network and child._travel is p._travel
        for old, new in zip(p.orders, child.orders):
            for field in ('id', 'customer', 'satellite', 'count', 'goods_weight', 'delivery_window',
                          'pickup_window', 'release_time', 'service_time', 'min_use_time'):
                assert getattr(old, field) == getattr(new, field)
        assert child.operating_policy.cleaning_duration == p.operating_policy.cleaning_duration
        assert child.operating_policy.batches[-1].end == 130


@pytest.mark.parametrize('number', [7, 8])
@pytest.mark.parametrize('mutation', [
    lambda r: r['batch_boundaries'].__setitem__(1, 1),
    lambda r: r['batch_boundaries'].__setitem__(-1, 131),
    lambda r: r['batch_durations_ticks'].__setitem__(0, 1),
    lambda r: r['cutoff_offsets_ticks'].update(b1=0),
    lambda r: r['cutoff_ratios'].update(b1=.99999),
    lambda r: r['calendar_config'].update(minimum_phase_ticks=0),
    lambda r: r['batches'][0].update(end=131),
    lambda r: r['operating_policy']['batches'][0].update(start=1),
    lambda r: r['candidate_schedule'][0].update(start=1),
    lambda r: r['batch_cutoffs'].update(b1=0),
    lambda r: r.update(model=6),
])
def test_saved_calendar_cannot_expand_its_own_domain(number, mutation):
    p = calendar_fixture(number, counts=(2,))
    r = solve_routing(model=number, method='alns', problem=p, alns_time=.08, alns_iter=0)
    assert r['audit']['valid']
    changed = json.loads(json.dumps(r))
    mutation(changed)
    assert not audit_solution(p, changed)['valid']


@pytest.mark.parametrize('number', [7, 8])
def test_initial_incumbent_is_retained_and_proof_is_cleared(number, exact_available):
    p = calendar_fixture(number, counts=(2,))
    initial = solve_routing(model=number, method='exact', problem=p, exact_time=10)
    copied = deepcopy(initial)
    r = solve_routing(model=number, method='alns', problem=p, alns_time=.05, alns_iter=0, initial_result=initial)
    assert r['audit']['valid'] and r['info']['objective'] <= initial['info']['objective']+1e-8
    assert not r['info']['proven_optimal'] and r['info']['best_bound'] is None
    assert initial == copied


@pytest.mark.parametrize('number', [7, 8])
def test_unresolved_k_never_inherits_another_counts_optimal_proof(number, exact_available, monkeypatch):
    solver = import_module(f'models.routing.model_{number}.exact.enhanced_solver')
    p = calendar_fixture(number)
    original = solver.solve_exact
    def partial(child, **kwargs):
        if len(child.operating_policy.batches) == 1:
            return original(child, **kwargs)
        result = joint_empty_result(child, 'exact')
        result['info'].update(status='TIME_LIMIT', best_bound=0.)
        return result
    monkeypatch.setattr(solver, 'solve_exact', partial)
    r = solve_routing(model=number, method='exact', problem=p, exact_time=10)
    assert r['audit']['valid'] and not r['info']['proven_optimal']
    from models.common.cycle.pool_bound import necessary_cost_lower_bound
    expected = min(r['count_search'][0]['objective'],
                   necessary_cost_lower_bound(p, batch_count=2, relaxed_loading=True))
    assert not r['info']['proven_infeasible']
    assert r['info']['best_bound'] == pytest.approx(expected)
    assert r['info']['best_bound'] < r['info']['objective']
    assert len(r['count_search']) == 2 and r['count_search'][1]['status'] == 'TIME_LIMIT'


def test_m8_accepts_nondivisible_horizon_and_exact_phase_ticks():
    source = joint_fixture()
    policy = replace(source.operating_policy, cycle_end=131)
    p = CalendarCycleInstance(source.network, source.orders, policy, dict(batch_count_candidates=[3]),
                              source.cost_profile.config, model=8)
    assert p.balanced_boundaries(3) == (0, 43, 87, 131)
    equal = CalendarCycleInstance(source.network, source.orders, policy, dict(batch_count_candidates=[3]),
                                  source.cost_profile.config, model=7)
    child = equal.for_count(3)
    assert child.operating_policy.time_tick_subdivision == 3
    assert {child.operating_policy.minutes(b.end-b.start) for b in child.operating_policy.batches} == {131/3}
    # Seven-second ticks deliberately have nonterminating minute fractions.
    policy = replace(source.operating_policy, time_tick_seconds=7)
    p = CalendarCycleInstance(source.network, source.orders, policy,
                              dict(batch_count_candidates=[2], min_phase_minutes=1), source.cost_profile.config, model=7)
    child = p.fixed_calendar(2)
    assert child.cutoff_bounds == {'b1': (9, 56), 'b2': (74, 121)}


@pytest.mark.parametrize('options', [dict(batch_count_candidates=[]), dict(batch_count_candidates=[True]),
    dict(batch_count_candidates=[1, 1]), dict(batch_count_candidates=[100]), dict(min_phase_minutes=0),
    dict(schedule_mode='uniform_cycle'), dict(optimize_cutoffs=False)])
def test_invalid_calendar_domains_are_rejected(options):
    source = joint_fixture()
    with pytest.raises(ValueError):
        CalendarCycleInstance(source.network, source.orders, source.operating_policy, options,
                              source.cost_profile.config, model=8)


def test_repartitioning_never_silently_drops_source_batch_restrictions():
    source = joint_fixture()
    orders = [replace(o, allowed_batch_ids=(o.batch,)) for o in source.orders]
    with pytest.raises(ValueError, match='all source batches'):
        CalendarCycleInstance(source.network, orders, source.operating_policy, {}, source.cost_profile.config, model=8)
    with pytest.raises(ValueError, match='Model 7'):
        import_module('models.routing.model_7.alns').solve_alns(calendar_fixture(8))


@pytest.mark.parametrize('number', [7, 8])
@pytest.mark.parametrize('method', ['exact', 'alns'])
def test_opposite_solver_cannot_be_imported(number, method, exact_available):
    script = r'''
import importlib.abc, sys
number, method = int(sys.argv[1]), sys.argv[2]
opposite = 'alns' if method == 'exact' else 'exact'
class Guard(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if ((fullname.startswith('models.routing.model_') and '.'+opposite in fullname)
            or (method == 'alns' and fullname.startswith('gurobipy'))):
            raise AssertionError('Unexpected dependency '+fullname)
sys.meta_path.insert(0, Guard())
from tests.routing.test_model78_calendar import calendar_fixture
from models.routing.service import solve_routing
r = solve_routing(model=number, method=method, problem=calendar_fixture(number),
                  exact_time=10, alns_time=.2, alns_iter=40)
assert r['audit']['valid'], r
'''
    run = subprocess.run([sys.executable, '-c', script, str(number), method], capture_output=True, text=True,
                         cwd=Path(__file__).resolve().parents[2], timeout=30)
    assert run.returncode == 0, run.stdout+run.stderr


@pytest.mark.parametrize('number', [7, 8])
def test_cli_native_cases_override_and_snapshot(tmp_path, number):
    from cli.route import main
    from tools.configuration.loader import load_routing_case
    from models.common.cycle.instance import load_instance
    case = f'config/experiments/examples/model{number}_demo.json'
    config, fleet = load_routing_case(case)
    assert load_instance(config, fleet).model_number == number
    r = main(['--case', case, '--solver', 'alns', '--time', '.3', '--iterations', '50', '--output', str(tmp_path/'run')])
    assert r['audit']['valid'] and r['model'] == number
    manifest = json.loads((tmp_path/'run/manifest.json').read_text())
    assert manifest['calendar_config']['model'] == number
    assert (tmp_path/f'run/source_snapshot/models/routing/model_{number}/exact/model.py').is_file()
    # Keep the override fixture portable; archived generated cases contain
    # absolute paths from the machine that originally produced the dataset.
    portable = json.loads(Path(case).read_text(encoding='utf-8'))
    portable.update(model=6)
    portable['joint_batch_options']['schedule_mode'] = 'uniform_cycle'
    source_case = tmp_path/'source_m6.json'
    source_case.write_text(json.dumps(portable), encoding='utf-8')
    override = main(['--case', str(source_case), '--model', str(number),
                     '--batch-count-candidates', '1', '2', '--time', '.2', '--iterations', '30',
                     '--output', str(tmp_path/'override')])
    assert override['audit']['valid'] and override['model'] == number
