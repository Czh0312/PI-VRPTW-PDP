"""Public solver numbers, case routing, and the boundary with historical cases."""
from importlib import import_module
import json
from shutil import copyfile

import pytest

from models.common.cycle import load_instance
from models.common.cycle.assignment import input_assignment
from models.common.no_batch import load_instance as load_no_batch
from models.routing.catalog import MODEL_SPECS
from models.routing.service import solve_routing
from tools.configuration import loader
from tools.configuration.loader import BENCHMARK_ROOT, PROJECT_ROOT, load_routing_case


def test_catalog_matches_active_package_directories():
    packages = {path.name for path in (PROJECT_ROOT/'models/routing').glob('model_*') if path.is_dir()}
    assert packages == {f'model_{i}' for i in range(1, 7)}
    assert tuple(MODEL_SPECS) == tuple(range(1, 7))


@pytest.mark.parametrize('number', range(1, 7))
def test_dispatch_uses_each_numbered_public_entry(monkeypatch, number):
    config, fleet = load_routing_case('S01', model=number)
    assert config['data'] == config['orders'] == str(BENCHMARK_ROOT/'small/S01.csv')
    problem = (load_no_batch if number == 6 else load_instance)(config, fleet)
    if number in (1, 2):
        assert problem.operating_policy.batches[0].cutoff == problem.operating_policy.cycle_end
    for method in ('exact', 'alns'):
        module = import_module(f'models.routing.model_{number}.{method}')
        sentinel = object()
        monkeypatch.setattr(module, f'solve_{method}', lambda *args, **kwargs: sentinel)
        assert solve_routing(model=number, method=method, problem=problem) is sentinel


def test_dataset_csv_uses_its_own_rates(tmp_path, monkeypatch):
    datasets = tmp_path.resolve() / 'data'
    monkeypatch.setattr(loader, 'BENCHMARK_ROOT', datasets / 'basetest')
    dataset = datasets / 'alternate'
    table = dataset / 'small' / 'S01.csv'
    table.parent.mkdir(parents=True)
    for filename in (table.name, 'config.json'):
        copyfile(BENCHMARK_ROOT / 'small' / filename, table.parent / filename)
    economic = dataset / 'economic.json'
    rates = loader.load_json(BENCHMARK_ROOT / 'economic.json')
    rates['e1_distance_cost_per_km'] = 0.6
    economic.write_text(json.dumps(rates), encoding='utf-8')
    for model in range(1, 7):
        config, fleet = load_routing_case(table, model=model)
        assert config['economic_config'] == str(economic)
        problem = (load_no_batch if model == 6 else load_instance)(config, fleet)
        assert problem.cost_profile.rates['e1_distance_cost_per_km'] == 0.6
    economic.unlink()
    with pytest.raises(FileNotFoundError, match=r'alternate/economic\.json'):
        load_instance(*load_routing_case(table, model=3))


def test_same_unassigned_case_loads_fixed_and_free_warehouse_models():
    case = BENCHMARK_ROOT/'small/S03.csv'
    config3, fleet3 = load_routing_case('S03', model=3)
    config4, fleet4 = load_routing_case(case, model=4)
    fixed, free = load_instance(config3, fleet3), load_instance(config4, fleet4)
    assert config3['orders'] == config4['orders']
    assert fixed.operating_policy == free.operating_policy
    assert fixed.operating_policy.consolidated_handoff
    assert fixed.satellites == free.satellites == ('S0', 'S1')
    assert input_assignment(fixed) == input_assignment(free)
    assert set(input_assignment(fixed).values()) == {'S0'}
    assert not fixed.free_assignment and free.free_assignment
    for config, fleet in ((config3, fleet3), (config4, fleet4)):
        policy = load_instance({**config, 'no_work_window': False}, fleet).operating_policy
        assert policy.consolidated_handoff
        assert policy.repeat_interval is None
        assert policy.minutes(policy.cycle_end) == 1260
    prior = 'data/benchmark27_models_1_6_v1/model_3/small/S03/case.json'
    assert set(input_assignment(load_instance(*load_routing_case(prior))).values()) == {'S0', 'S1'}


def test_archived_s01_still_uses_the_old_delivery_cutoff():
    case = 'data/benchmark27_shared_v2/small/S01/case.json'
    policy = load_instance(*load_routing_case(case, model=1)).operating_policy
    assert policy.minutes(policy.batches[0].cutoff) == 1150.5


@pytest.mark.parametrize('number', [7, 8, 9, 10])
def test_retired_numbers_are_rejected(number):
    with pytest.raises(ValueError, match='1–6'):
        solve_routing(model=number, method='alns', problem=None)


def test_old_model5_case_requires_its_frozen_source():
    case = 'data/formal_benchmark27_uniform_7_20_50_v1/model_5/small/S01/case.json'
    with pytest.raises(ValueError, match='archived solver'):
        load_instance(*load_routing_case(case))


def test_old_uniform_model6_case_is_rejected_by_no_batch_cli():
    from cli.route import main

    case = 'data/formal_benchmark27_uniform_7_20_50_v1/model_6/small/S01/case.json'
    with pytest.raises(ValueError, match='archived solver'):
        main(['--case', case])
