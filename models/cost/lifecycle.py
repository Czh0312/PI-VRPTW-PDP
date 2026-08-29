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


def reusable_variable_cost_per_use(params: EconomicParams) -> float:
    """Reusable-container handling and replacement cost for one completed use."""
    return (
        params.reusable_cleaning_sorting_cost
        + params.reusable_tracking_scan_cost
        + params.reusable_loss_rate * params.reusable_container_price
    )


def single_use_variable_cost_per_use(params: EconomicParams) -> float:
    """Purchase and end-of-life cost for one single-use package."""
    return params.single_use_package_price + params.single_use_eol_cost


def route_period_cost(
    metrics: RouteMetrics,
    params: EconomicParams,
) -> float:
    """Transport cost for one solved operating period (normally one day)."""
    return transport_cost(metrics, params)


def value_decomposition(
    single_use_metrics: RouteMetrics,
    reusable_baseline_metrics: RouteMetrics,
    reusable_optimized_metrics: RouteMetrics,
    params: EconomicParams,
    container_uses_per_period: int,
    horizon_periods: int = 1,
    returned_container_uses_per_period: Optional[int] = None,
) -> Dict[str, float | int | None]:
    """Explain where reusable-container value is created or lost.

    The three route inputs deliberately separate two questions.  The first is
    the selected single-use route, the second is a reusable reference with the
    same assignment freedom, and the third is the selected reusable route.
    This supports either the fixed-assignment M1/M3 comparison or the fairer
    free-assignment M2/M4 comparison without changing the accounting identity.

    Route metrics describe one representative operating period and are repeated
    unchanged over ``horizon_periods``.  This is more transparent than scaling a
    single route solution by an ambiguous "number of orders" axis.
    """
    if container_uses_per_period <= 0:
        raise ValueError("container_uses_per_period must be positive")
    returned_uses = (
        container_uses_per_period
        if returned_container_uses_per_period is None
        else returned_container_uses_per_period
    )
    if returned_uses < 0:
        raise ValueError("returned_container_uses_per_period cannot be negative")
    if horizon_periods <= 0:
        raise ValueError("horizon_periods must be positive")

    su_transport = route_period_cost(single_use_metrics, params)
    ru_baseline_transport = route_period_cost(reusable_baseline_metrics, params)
    ru_optimized_transport = route_period_cost(reusable_optimized_metrics, params)

    su_pack_per_use = single_use_variable_cost_per_use(params)
    # Cleaning and scanning occur on returned containers; loss replacement is
    # attributed to outbound uses.  Legacy callers omit returned uses and keep
    # the former equal-flow result.
    ru_variable_per_period = (
        returned_uses
        * (params.reusable_cleaning_sorting_cost + params.reusable_tracking_scan_cost)
        + container_uses_per_period
        * params.reusable_loss_rate
        * params.reusable_container_price
    )
    ru_pack_per_use = ru_variable_per_period / container_uses_per_period
    packaging_saving_per_use = su_pack_per_use - ru_pack_per_use
    gross_packaging_saving = container_uses_per_period * packaging_saving_per_use

    closed_loop_transport_penalty = ru_baseline_transport - su_transport
    route_optimization_saving = ru_baseline_transport - ru_optimized_transport
    optimized_transport_penalty = ru_optimized_transport - su_transport
    net_variable_saving = gross_packaging_saving - optimized_transport_penalty

    fixed_pool_investment = (
        params.reusable_pool_size * params.reusable_container_price
    )
    break_even_periods = (
        fixed_pool_investment / net_variable_saving
        if net_variable_saving > 0
        else None
    )
    break_even_container_uses = (
        break_even_periods * container_uses_per_period
        if break_even_periods is not None
        else None
    )

    single_use_total = horizon_periods * (
        su_transport + container_uses_per_period * su_pack_per_use
    )
    reusable_baseline_total = fixed_pool_investment + horizon_periods * (
        ru_baseline_transport + ru_variable_per_period
    )
    reusable_optimized_total = fixed_pool_investment + horizon_periods * (
        ru_optimized_transport + ru_variable_per_period
    )
    horizon_net_saving = single_use_total - reusable_optimized_total

    # Single-use package purchase price at which the optimized reusable
    # scheme has the same variable cost before the initial pool investment.
    critical_single_use_package_price = (
        ru_pack_per_use
        + optimized_transport_penalty / container_uses_per_period
        - params.single_use_eol_cost
    )
    critical_uses_per_period: Optional[float]
    if packaging_saving_per_use <= 0:
        critical_uses_per_period = None
    elif optimized_transport_penalty <= 0:
        critical_uses_per_period = 0.0
    else:
        critical_uses_per_period = (
            optimized_transport_penalty / packaging_saving_per_use
        )

    return {
        "container_uses_per_period": container_uses_per_period,
        "returned_container_uses_per_period": returned_uses,
        "horizon_periods": horizon_periods,
        "single_use_transport_per_period": su_transport,
        "reusable_baseline_transport_per_period": ru_baseline_transport,
        "reusable_optimized_transport_per_period": ru_optimized_transport,
        "single_use_packaging_cost_per_use": su_pack_per_use,
        "reusable_variable_cost_per_use": ru_pack_per_use,
        "packaging_saving_per_use": packaging_saving_per_use,
        "gross_packaging_saving_per_period": gross_packaging_saving,
        "closed_loop_transport_penalty_per_period": closed_loop_transport_penalty,
        "route_optimization_saving_per_period": route_optimization_saving,
        "optimized_transport_penalty_per_period": optimized_transport_penalty,
        "net_variable_saving_per_period": net_variable_saving,
        "fixed_pool_investment": fixed_pool_investment,
        "break_even_periods": break_even_periods,
        "break_even_container_uses": break_even_container_uses,
        "critical_single_use_package_price_yuan": (
            critical_single_use_package_price
        ),
        "critical_container_uses_per_period": critical_uses_per_period,
        "single_use_total_at_horizon": single_use_total,
        "reusable_baseline_total_at_horizon": reusable_baseline_total,
        "reusable_optimized_total_at_horizon": reusable_optimized_total,
        "net_saving_at_horizon": horizon_net_saving,
        "saving_rate_at_horizon": (
            horizon_net_saving / single_use_total if single_use_total else None
        ),
        "roi_on_initial_pool": (
            horizon_net_saving / fixed_pool_investment
            if fixed_pool_investment
            else None
        ),
    }


def period_sweep(
    single_use_metrics: RouteMetrics,
    reusable_baseline_metrics: RouteMetrics,
    reusable_optimized_metrics: RouteMetrics,
    params: EconomicParams,
    container_uses_per_period: int,
    periods: Iterable[int],
    returned_container_uses_per_period: Optional[int] = None,
) -> List[Dict[str, float | int]]:
    """Build cumulative-cost curves by repeated operating period."""
    fixed_pool = params.reusable_pool_size * params.reusable_container_price
    su_variable = (
        route_period_cost(single_use_metrics, params)
        + container_uses_per_period * single_use_variable_cost_per_use(params)
    )
    returned_uses = (
        container_uses_per_period
        if returned_container_uses_per_period is None
        else returned_container_uses_per_period
    )
    ru_packaging_variable = (
        returned_uses
        * (params.reusable_cleaning_sorting_cost + params.reusable_tracking_scan_cost)
        + container_uses_per_period
        * params.reusable_loss_rate
        * params.reusable_container_price
    )
    ru_baseline_variable = (
        route_period_cost(reusable_baseline_metrics, params)
        + ru_packaging_variable
    )
    ru_optimized_variable = (
        route_period_cost(reusable_optimized_metrics, params)
        + ru_packaging_variable
    )

    rows: List[Dict[str, float | int]] = []
    for period in periods:
        if period <= 0:
            raise ValueError("periods must contain positive integers")
        su_total = period * su_variable
        ru_baseline_total = fixed_pool + period * ru_baseline_variable
        ru_optimized_total = fixed_pool + period * ru_optimized_variable
        rows.append(
            {
                "period": period,
                "cumulative_container_uses": period * container_uses_per_period,
                "single_use_total": su_total,
                "reusable_baseline_total": ru_baseline_total,
                "reusable_optimized_total": ru_optimized_total,
                "optimized_net_saving": su_total - ru_optimized_total,
                "route_optimization_value": (
                    ru_baseline_total - ru_optimized_total
                ),
                "optimized_saving_rate": (
                    (su_total - ru_optimized_total) / su_total
                    if su_total
                    else 0.0
                ),
            }
        )
    return rows
