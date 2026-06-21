# -*- coding: utf-8 -*-
"""Economic post-processing for 2E-VRPTW reusable-container experiments."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional


@dataclass(frozen=True)
class RouteMetrics:
    """Path metrics produced by the routing model."""

    orders: int
    e1_distance_km: float
    e2_distance_km: float
    e1_vehicle_count: int = 0
    e2_vehicle_count: int = 0

    @property
    def total_distance_km(self) -> float:
        return self.e1_distance_km + self.e2_distance_km

    def per_order(self) -> "RouteMetrics":
        if self.orders <= 0:
            raise ValueError("orders must be positive")
        return RouteMetrics(
            orders=1,
            e1_distance_km=self.e1_distance_km / self.orders,
            e2_distance_km=self.e2_distance_km / self.orders,
            e1_vehicle_count=self.e1_vehicle_count,
            e2_vehicle_count=self.e2_vehicle_count,
        )


@dataclass(frozen=True)
class EconomicParams:
    """Currency parameters, all in yuan and kilometers."""

    single_use_package_price: float
    single_use_eol_cost: float
    reusable_container_price: float
    reusable_pool_size: int
    reusable_cleaning_sorting_cost: float
    reusable_loss_rate: float
    reusable_tracking_scan_cost: float
    e1_cost_per_km: float
    e2_cost_per_km: float

    @classmethod
    def from_config(cls, cfg: Dict) -> "EconomicParams":
        su = cfg["single_use"]
        ru = cfg["reusable"]
        tr = cfg["transport"]
        e1_cost = tr.get("e1_cost_yuan_per_km")
        e2_cost = tr.get("e2_cost_yuan_per_km")
        diesel_price = float(tr.get("diesel_price_yuan_per_liter", 0.0))
        if e1_cost is None:
            e1_cost = (
                float(tr["e1_fuel_consumption_l_per_100km"]) * diesel_price / 100.0
            )
        if e2_cost is None:
            e2_cost = (
                float(tr["e2_fuel_consumption_l_per_100km"]) * diesel_price / 100.0
            )

        return cls(
            single_use_package_price=float(su["package_price_yuan"]),
            single_use_eol_cost=float(su.get("end_of_life_cost_yuan", 0.0)),
            reusable_container_price=float(ru["container_price_yuan"]),
            reusable_pool_size=int(ru["initial_pool_size"]),
            reusable_cleaning_sorting_cost=float(ru["cleaning_sorting_cost_yuan"]),
            reusable_loss_rate=float(ru["loss_rate_per_cycle"]),
            reusable_tracking_scan_cost=float(ru.get("tracking_scan_cost_yuan", 0.0)),
            e1_cost_per_km=float(e1_cost),
            e2_cost_per_km=float(e2_cost),
        )


@dataclass(frozen=True)
class CostBreakdown:
    scheme: str
    demand: int
    transport_cost: float
    package_purchase_cost: float
    end_of_life_cost: float
    fixed_pool_cost: float
    cleaning_sorting_cost: float
    loss_replacement_cost: float
    tracking_scan_cost: float

    @property
    def total_cost(self) -> float:
        return (
            self.transport_cost
            + self.package_purchase_cost
            + self.end_of_life_cost
            + self.fixed_pool_cost
            + self.cleaning_sorting_cost
            + self.loss_replacement_cost
            + self.tracking_scan_cost
        )

    @property
    def unit_cost(self) -> float:
        return self.total_cost / self.demand if self.demand else float("inf")

    def as_dict(self) -> Dict[str, float]:
        return {
            "scheme": self.scheme,
            "demand": self.demand,
            "transport_cost": self.transport_cost,
            "package_purchase_cost": self.package_purchase_cost,
            "end_of_life_cost": self.end_of_life_cost,
            "fixed_pool_cost": self.fixed_pool_cost,
            "cleaning_sorting_cost": self.cleaning_sorting_cost,
            "loss_replacement_cost": self.loss_replacement_cost,
            "tracking_scan_cost": self.tracking_scan_cost,
            "total_cost": self.total_cost,
            "unit_cost": self.unit_cost,
        }


def transport_cost(metrics: RouteMetrics, params: EconomicParams) -> float:
    return (
        metrics.e1_distance_km * params.e1_cost_per_km
        + metrics.e2_distance_km * params.e2_cost_per_km
    )


def scale_metrics(per_order_metrics: RouteMetrics, demand: int) -> RouteMetrics:
    return RouteMetrics(
        orders=demand,
        e1_distance_km=per_order_metrics.e1_distance_km * demand,
        e2_distance_km=per_order_metrics.e2_distance_km * demand,
        e1_vehicle_count=per_order_metrics.e1_vehicle_count,
        e2_vehicle_count=per_order_metrics.e2_vehicle_count,
    )


def single_use_cost(
    per_order_metrics: RouteMetrics,
    params: EconomicParams,
    demand: int,
) -> CostBreakdown:
    metrics = scale_metrics(per_order_metrics, demand)
    return CostBreakdown(
        scheme="single_use",
        demand=demand,
        transport_cost=transport_cost(metrics, params),
        package_purchase_cost=demand * params.single_use_package_price,
        end_of_life_cost=demand * params.single_use_eol_cost,
        fixed_pool_cost=0.0,
        cleaning_sorting_cost=0.0,
        loss_replacement_cost=0.0,
        tracking_scan_cost=0.0,
    )


def reusable_cost(
    per_order_metrics: RouteMetrics,
    params: EconomicParams,
    demand: int,
    include_fixed_pool: bool = True,
) -> CostBreakdown:
    metrics = scale_metrics(per_order_metrics, demand)
    return CostBreakdown(
        scheme="reusable",
        demand=demand,
        transport_cost=transport_cost(metrics, params),
        package_purchase_cost=0.0,
        end_of_life_cost=0.0,
        fixed_pool_cost=(
            params.reusable_pool_size * params.reusable_container_price
            if include_fixed_pool
            else 0.0
        ),
        cleaning_sorting_cost=demand * params.reusable_cleaning_sorting_cost,
        loss_replacement_cost=(
            demand * params.reusable_loss_rate * params.reusable_container_price
        ),
        tracking_scan_cost=demand * params.reusable_tracking_scan_cost,
    )


def marginal_saving_per_order(
    single_use_per_order: RouteMetrics,
    reusable_per_order: RouteMetrics,
    params: EconomicParams,
) -> float:
    su = single_use_cost(single_use_per_order, params, demand=1).total_cost
    ru = reusable_cost(
        reusable_per_order, params, demand=1, include_fixed_pool=False
    ).total_cost
    return su - ru


def break_even_demand(
    single_use_per_order: RouteMetrics,
    reusable_per_order: RouteMetrics,
    params: EconomicParams,
) -> Optional[float]:
    saving = marginal_saving_per_order(single_use_per_order, reusable_per_order, params)
    if saving <= 0:
        return None
    return params.reusable_pool_size * params.reusable_container_price / saving


def demand_sweep(
    single_use_per_order: RouteMetrics,
    reusable_per_order: RouteMetrics,
    params: EconomicParams,
    demands: Iterable[int],
) -> List[Dict[str, float]]:
    rows = []
    for demand in demands:
        su = single_use_cost(single_use_per_order, params, demand)
        ru = reusable_cost(reusable_per_order, params, demand)
        cost_ratio = ru.total_cost / su.total_cost if su.total_cost else float("inf")
        saving_rate = (
            (su.total_cost - ru.total_cost) / su.total_cost
            if su.total_cost
            else float("-inf")
        )
        rows.append(
            {
                "demand": demand,
                "single_use_total": su.total_cost,
                "reusable_total": ru.total_cost,
                "single_use_unit": su.unit_cost,
                "reusable_unit": ru.unit_cost,
                "saving_total": su.total_cost - ru.total_cost,
                "cost_ratio_ru_to_su": cost_ratio,
                "saving_rate": saving_rate,
            }
        )
    return rows
