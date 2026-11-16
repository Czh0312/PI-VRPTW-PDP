# -*- coding: utf-8 -*-
"""Regression checks for reusable-container economic value decomposition."""
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from models.cost.lifecycle import EconomicParams, RouteMetrics, period_sweep, value_decomposition
from models.cost.single_batch import incidental_cost_sensitivity


PARAMS = EconomicParams(
    single_use_package_price=4.0,
    single_use_eol_cost=0.3,
    reusable_container_price=36.5,
    reusable_pool_size=100,
    reusable_cleaning_sorting_cost=0.8,
    reusable_loss_rate=0.03,
    reusable_tracking_scan_cost=0.1,
    e1_cost_per_km=6.5,
    e2_cost_per_km=2.2,
)


def test_value_identity_and_break_even():
    su = RouteMetrics(orders=100, e1_distance_km=10, e2_distance_km=20)
    m3 = RouteMetrics(orders=100, e1_distance_km=12, e2_distance_km=25)
    optimized = RouteMetrics(orders=100, e1_distance_km=11, e2_distance_km=21)
    d = value_decomposition(su, m3, optimized, PARAMS, 100, 300)
    identity = (
        d["gross_packaging_saving_per_period"]
        - d["closed_loop_transport_penalty_per_period"]
        + d["route_optimization_saving_per_period"]
    )
    assert abs(identity - d["net_variable_saving_per_period"]) < 1e-9
    assert d["route_optimization_saving_per_period"] > 0
    assert d["break_even_periods"] is not None
    assert abs(d["break_even_container_uses"] - d["break_even_periods"] * 100) < 1e-9


def test_no_break_even_when_variable_saving_is_negative():
    su = RouteMetrics(orders=10, e1_distance_km=1, e2_distance_km=1)
    m3 = RouteMetrics(orders=10, e1_distance_km=20, e2_distance_km=20)
    d = value_decomposition(su, m3, m3, PARAMS, 10, 300)
    assert d["net_variable_saving_per_period"] < 0
    assert d["break_even_periods"] is None


def test_period_curve_preserves_route_optimization_value():
    su = RouteMetrics(orders=10, e1_distance_km=5, e2_distance_km=5)
    m3 = RouteMetrics(orders=10, e1_distance_km=8, e2_distance_km=8)
    optimized = RouteMetrics(orders=10, e1_distance_km=7, e2_distance_km=7)
    rows = period_sweep(su, m3, optimized, PARAMS, 10, [1, 10])
    assert len(rows) == 2
    assert abs(rows[1]["route_optimization_value"] - 10 * rows[0]["route_optimization_value"]) < 1e-9


def test_cleaning_cost_uses_returned_container_count():
    metrics = RouteMetrics(orders=100, e1_distance_km=0, e2_distance_km=0)
    result = value_decomposition(
        metrics,
        metrics,
        metrics,
        PARAMS,
        100,
        1,
        returned_container_uses_per_period=90,
    )
    expected = (90 * (0.8 + 0.1) + 100 * 0.03 * 36.5) / 100
    assert result["returned_container_uses_per_period"] == 90
    assert abs(result["reusable_variable_cost_per_use"] - expected) < 1e-9


def test_incidental_costs_change_period_and_horizon_break_even():
    pair = dict(period_saving=50, period_single_use_purchase_price_threshold=2.5,
                evaluation_cycles=4, horizon_saving=120)
    study = incidental_cost_sensitivity(pair, packages=100, loss_rates=[0, .01, .025],
                                        replacement_cost_per_box=20, other_cost_per_use=.1)
    assert study['period_max_loss_rate'] == pytest.approx(.02)
    assert study['horizon_max_loss_rate'] == pytest.approx(.01)
    rows = study['scenarios']
    assert [r['period_incidental_cost'] for r in rows] == pytest.approx([10, 30, 60])
    assert [r['period_saving'] for r in rows] == pytest.approx([40, 20, -10])
    assert [r['horizon_saving'] for r in rows] == pytest.approx([80, 0, -120])
    assert [r['period_paper_price_threshold'] for r in rows] == pytest.approx([2.6, 2.8, 3.1])
    assert pair['period_saving'] == 50 and pair['horizon_saving'] == 120


@pytest.mark.parametrize(('saving', 'price', 'packages', 'limit'), [
    (9, 20, 100, None), (10, 20, 100, 0), (10, 0, 100, 1),
    (50, .2, 100, 1), (0, 20, 0, None),
])
def test_incidental_loss_limit_respects_cost_and_rate_boundaries(saving, price, packages, limit):
    study = incidental_cost_sensitivity(dict(period_saving=saving), packages=packages,
                                        loss_rates=[0], replacement_cost_per_box=price, other_cost_per_use=.1)
    assert study['period_max_loss_rate'] == limit
    assert study['horizon_max_loss_rate'] is None and study['scenarios'][0]['horizon_saving'] is None
