"""Guard the scientific distinction between true gaps and incumbent comparisons."""
import importlib.util
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location('m8_gap_driver', Path(__file__).with_name('driver.py'))
driver = importlib.util.module_from_spec(spec)
spec.loader.exec_module(driver)


def test_proved_optimum_supports_true_gap():
    metrics = driver.gap_metrics(
        dict(valid=True, proven_optimal=True, objective=100., best_bound=100.),
        dict(valid=True, objective=105.))
    assert metrics['optimality_gap_percent'] == pytest.approx(5.)


@pytest.mark.parametrize('alns_cost,incumbent_deviation', [(90., -10.), (110., 10.)])
def test_open_mip_never_becomes_optimality_reference(alns_cost, incumbent_deviation):
    metrics = driver.gap_metrics(
        dict(valid=True, proven_optimal=False, objective=100., best_bound=80.),
        dict(valid=True, objective=alns_cost))
    assert metrics['optimality_gap_percent'] is None
    assert metrics['deviation_to_mip_incumbent_percent'] == pytest.approx(incumbent_deviation)
    assert metrics['gap_upper_bound_from_lower_bound_percent'] == pytest.approx(100*(alns_cost-80)/80)


def test_no_exact_incumbent_still_supports_valid_lower_bound():
    metrics = driver.gap_metrics(
        dict(valid=False, proven_optimal=False, objective=None, best_bound=80.),
        dict(valid=True, objective=100.))
    assert metrics['optimality_gap_percent'] is None
    assert metrics['deviation_to_mip_incumbent_percent'] is None
    assert metrics['gap_upper_bound_from_lower_bound_percent'] == pytest.approx(25.)


@pytest.mark.parametrize('exact', [
    dict(valid=True, proven_optimal=True, objective=100., best_bound=100.),
    dict(valid=False, proven_optimal=False, objective=None, best_bound=100.),
])
def test_conflicting_bounds_are_rejected(exact):
    with pytest.raises(ValueError):
        driver.gap_metrics(exact, dict(valid=True, objective=90.))


def test_missing_alns_is_not_zero_gap():
    metrics = driver.gap_metrics(dict(valid=True, proven_optimal=True, objective=100., best_bound=100.), None)
    assert all(value is None for value in metrics.values())
