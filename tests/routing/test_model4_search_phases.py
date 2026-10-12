"""A second learning phase must preserve incumbents and shared budgets."""
from tools.configuration.loader import load_json
from pathlib import Path

import pytest

from archive.routing_baselines.alns.common.engine import ALNSResult
from archive.routing_baselines.alns import model_4
from tests.routing.baseline_helpers import load_problem

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def problem():
    fleet = load_json(ROOT / 'config/vehicles/zhou_small.json')
    return load_problem(ROOT / 'data/zhou_later_pickup/small/standard_same_day/Set1_7-2-1b.csv', fleet)


@pytest.mark.parametrize('stop', ['none', 'target', 'iterations'])
def test_shared_budget_and_incumbent_survive_second_phase(problem, monkeypatch, stop):
    calls = []
    first_cost = []

    def fake_search(**kwargs):
        config = kwargs['config']
        initial = kwargs['initial_solution']
        calls.append(kwargs)
        if len(calls) == 1:
            first_cost.append(initial.cost)
            best = initial.copy()
            cost = initial.cost
        else:
            assert initial is not calls[0]['initial_solution']
            assert initial.cost == first_cost[0]
            # Simulate an interrupted later phase with no feasible incumbent.
            best, cost = None, float('inf')
        return ALNSResult(initial, best, cost,
            config.max_iterations if stop == 'iterations' else 2,
            0.1, 0.01, 0.01 if stop == 'target' else None,
            stop, 0, 0, {})

    monkeypatch.setattr(model_4, 'run_alns_search', fake_search)
    _, _, _, info = model_4.solve_alns(problem, problem.fleet.e2_per_sat_count,
        max_time=10, max_iterations=7, search_phase_fraction=0.75,
        verbose=False, return_info=True, exchange_polish=False)
    assert info['feasible']
    assert info['objective'] == first_cost[0]
    if stop == 'none':
        assert len(calls) == 2
        assert calls[0]['config'].max_time == 7.5
        assert calls[1]['config'].max_time == 10
        assert calls[0]['start_time'] == calls[1]['start_time']
        assert calls[1]['config'].max_iterations == 5
        assert info['iterations'] == 4
    else:
        assert len(calls) == 1


@pytest.mark.parametrize('fraction', [0, -1, 1.1, float('nan')])
def test_invalid_phase_fraction(problem, fraction):
    with pytest.raises(ValueError):
        model_4.solve_alns(problem, 4, search_phase_fraction=fraction)
