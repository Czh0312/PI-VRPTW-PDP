"""Public solver numbers, case routing, and the boundary with historical cases."""
from importlib import import_module

import pytest

from models.common.cycle import load_instance
from models.common.no_batch import load_instance as load_no_batch
from models.routing.catalog import MODEL_SPECS
from models.routing.service import solve_routing
from tools.configuration.loader import PROJECT_ROOT, load_routing_case


def test_catalog_matches_active_package_directories():
    packages = {path.name for path in (PROJECT_ROOT/'models/routing').glob('model_*') if path.is_dir()}
    assert packages == {f'model_{i}' for i in range(1, 7)}
    assert tuple(MODEL_SPECS) == tuple(range(1, 7))


@pytest.mark.parametrize('number', range(1, 7))
def test_dispatch_uses_each_numbered_public_entry(monkeypatch, number):
    config, fleet = load_routing_case(f'data/benchmark27_models_1_6_v1/model_{number}/small/S01/case.json')
    problem = (load_no_batch if number == 6 else load_instance)(config, fleet)
    for method in ('exact', 'alns'):
        module = import_module(f'models.routing.model_{number}.{method}')
        sentinel = object()
        monkeypatch.setattr(module, f'solve_{method}', lambda *args, **kwargs: sentinel)
        assert solve_routing(model=number, method=method, problem=problem) is sentinel


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
