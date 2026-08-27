# -*- coding: utf-8 -*-
"""Build a reproducible semi-real Hangzhou closed-loop routing case.

Observed inputs come from LaDe-D.  Reusable-container return volumes and
pickup windows are scenario inputs because LaDe-D does not track packaging
returns.  The script writes both the observed/modelled flow table and solver
CSV instances so the distinction remains auditable.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import urllib.parse
import urllib.request
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = ROOT / "data" / "real_case" / "raw" / "lade_d" / "delivery_hz.parquet"
DEFAULT_OUTPUT = (
    ROOT / "data" / "real_case" / "processed" / "lade_hz_0823_0829_30aoi"
)

# Publicly documented coordinate near the old cargo centre at Hangzhou
# Xiaoshan International Airport.  It is an explicit central-hub proxy, not a
# depot observed in LaDe-D.
DEPOT = {
    "node_id": "D0",
    "name": "杭州萧山国际机场旧货运中心（中心仓代理点）",
    "lon": 120.425103,
    "lat": 30.225378,
    "source": "Wikimedia Commons geotag; YTO processing facility visible",
    "source_url": (
        "https://commons.wikimedia.org/wiki/"
        "File:202403_Old_Cargo_Center_of_Hangzhou_Xiaoshan_International_Airport.jpg"
    ),
}

FIELDNAMES = [
    "node_id",
    "node_type",
    "x",
    "y",
    "delivery_demand",
    "delivery_ready",
    "delivery_due",
    "service_time",
    "pickup_ready",
    "pickup_due",
    "assigned_sat",
    "container_count",
    "delivery_container_count",
    "pickup_container_count",
    "source_aoi_id",
    "source_region_id",
]


def parse_study_date(series: pd.Series) -> pd.Series:
    """Convert LaDe MMDD integers to an order-preserving anonymous-year date."""
    return pd.to_datetime(
        "2000" + series.astype(str).str.zfill(4), format="%Y%m%d"
    )


def minutes_from_timestamp(series: pd.Series) -> pd.Series:
    stamp = pd.to_datetime(series, format="%m-%d %H:%M:%S", errors="coerce")
    return stamp.dt.hour * 60 + stamp.dt.minute + stamp.dt.second / 60.0


def local_xy_km(lon: float, lat: float) -> tuple[float, float]:
    km_per_degree = 111.32
    x = (lon - DEPOT["lon"]) * km_per_degree * math.cos(math.radians(DEPOT["lat"]))
    y = (lat - DEPOT["lat"]) * km_per_degree
    return round(x, 6), round(y, 6)


def select_aois(
    week: pd.DataFrame, regions: list[int], aois_per_region: int, days: int
) -> pd.DataFrame:
    selected = []
    for region_id in regions:
        group = week[week["region_id"] == region_id]
        if group.empty:
            raise ValueError(f"region {region_id} has no rows in selected week")
        stats = group.groupby("aoi_id").agg(
            active_days=("date", "nunique"),
            weekly_orders=("aoi_id", "size"),
            lon=("lng", "median"),
            lat=("lat", "median"),
            aoi_type=("aoi_type", lambda s: int(s.mode().iloc[0])),
        )
        stats = stats[stats["active_days"] == days].sort_values(
            ["weekly_orders", "aoi_id"], ascending=[False, True]
        )
        if len(stats) < aois_per_region:
            raise ValueError(
                f"region {region_id} has only {len(stats)} AOIs active on all {days} days"
            )
        chosen = stats.head(aois_per_region).reset_index()
        chosen["region_id"] = region_id
        selected.append(chosen)
    return pd.concat(selected, ignore_index=True)


def derive_delivery_windows(week: pd.DataFrame, selected_ids: set[int]) -> dict[int, tuple[int, int]]:
    subset = week[week["aoi_id"].isin(selected_ids)].copy()
    subset["delivery_minute"] = minutes_from_timestamp(subset["delivery_time"])
    result: dict[int, tuple[int, int]] = {}
    for aoi_id, group in subset.groupby("aoi_id"):
        valid = group["delivery_minute"].dropna()
        if valid.empty:
            result[int(aoi_id)] = (480, 1080)
            continue
        ready = max(480, int(math.floor((valid.quantile(0.05) - 30) / 30) * 30))
        due = min(1200, int(math.ceil((valid.quantile(0.95) + 30) / 30) * 30))
        if due - ready < 360:
            centre = int(valid.median())
            ready = max(480, centre - 180)
            due = min(1200, ready + 360)
            ready = max(480, due - 360)
        result[int(aoi_id)] = (ready, due)
    return result


def expected_pickups(
    daily_counts: pd.Series,
    date: pd.Timestamp,
    return_rate: float,
    lag_weights: dict[int, float],
) -> int:
    expected = 0.0
    for lag, weight in lag_weights.items():
        expected += weight * float(daily_counts.get(date - pd.Timedelta(days=lag), 0))
    return max(0, int(math.floor(return_rate * expected + 0.5)))


def write_square_matrix(path: Path, node_ids: list[str], values: list[list[float]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["node_id", *node_ids])
        for node_id, row in zip(node_ids, values):
            writer.writerow([node_id, *[round(float(value), 6) for value in row]])


def fetch_osrm_table(nodes: list[dict], service_url: str) -> dict:
    coordinates = ";".join(f"{node['lon']:.7f},{node['lat']:.7f}" for node in nodes)
    url = (
        f"{service_url.rstrip('/')}/table/v1/driving/{coordinates}?"
        + urllib.parse.urlencode({"annotations": "distance,duration"})
    )
    request = urllib.request.Request(
        url, headers={"User-Agent": "reusable-container-thesis/1.0"}
    )
    with urllib.request.urlopen(request, timeout=120) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if payload.get("code") != "Ok":
        raise RuntimeError(f"OSRM returned {payload.get('code')}: {payload}")
    if any(value is None for row in payload["distances"] for value in row):
        raise RuntimeError("OSRM distance matrix contains unreachable pairs")
    return {"request_url": url, "response": payload}


def build(args: argparse.Namespace) -> dict:
    columns = [
        "region_id", "courier_id", "lng", "lat", "aoi_id", "aoi_type",
        "accept_gps_lng", "accept_gps_lat", "delivery_time", "ds",
    ]
    frame = pd.read_parquet(args.source, columns=columns)
    frame["date"] = parse_study_date(frame["ds"])
    start = pd.to_datetime(f"2000-{args.start}", format="%Y-%m-%d")
    dates = pd.date_range(start, periods=args.days, freq="D")
    week = frame[frame["date"].isin(dates)].copy()
    regions = [int(value) for value in args.regions]
    selected = select_aois(week, regions, args.aois_per_region, args.days)
    selected_ids = set(int(value) for value in selected["aoi_id"])
    windows = derive_delivery_windows(week, selected_ids)

    output_dir: Path = args.output_dir
    instances_dir = output_dir / "instances"
    instances_dir.mkdir(parents=True, exist_ok=True)

    satellites = []
    for region_id in regions:
        region = week[week["region_id"] == region_id]
        lon = float(region["accept_gps_lng"].median())
        lat = float(region["accept_gps_lat"].median())
        x, y = local_xy_km(lon, lat)
        satellites.append({
            "node_id": f"S{region_id}",
            "node_type": "s",
            "name": f"LaDe region {region_id} courier-accept anchor",
            "lon": lon,
            "lat": lat,
            "x": x,
            "y": y,
            "source": "LaDe-D median accept_gps coordinate in selected week",
            "source_region_id": region_id,
        })

    customer_nodes = []
    for _, row in selected.sort_values(["region_id", "weekly_orders"], ascending=[True, False]).iterrows():
        aoi_id = int(row["aoi_id"])
        lon, lat = float(row["lon"]), float(row["lat"])
        x, y = local_xy_km(lon, lat)
        customer_nodes.append({
            "node_id": f"C{aoi_id}",
            "node_type": "c",
            "name": f"LaDe AOI {aoi_id}",
            "lon": lon,
            "lat": lat,
            "x": x,
            "y": y,
            "source": "LaDe-D median stop coordinate in selected week",
            "source_aoi_id": aoi_id,
            "source_region_id": int(row["region_id"]),
            "aoi_type": int(row["aoi_type"]),
            "weekly_orders": int(row["weekly_orders"]),
        })

    depot_x, depot_y = local_xy_km(DEPOT["lon"], DEPOT["lat"])
    depot_node = {
        **DEPOT,
        "node_type": "d",
        "x": depot_x,
        "y": depot_y,
        "source_aoi_id": "",
        "source_region_id": "",
    }
    geo_nodes = [depot_node, *satellites, *customer_nodes]
    pd.DataFrame(geo_nodes).to_csv(output_dir / "nodes_geographic.csv", index=False, encoding="utf-8")

    selected_history = frame[frame["aoi_id"].isin(selected_ids)]
    history_counts = {
        int(aoi_id): group.groupby("date").size()
        for aoi_id, group in selected_history.groupby("aoi_id")
    }
    current_counts = week.groupby(["date", "aoi_id"]).size()
    lag_weights = {int(k): float(v) for k, v in args.lag_weights.items()}
    flow_rows = []

    for date in dates:
        instance_rows = [{
            "node_id": "D0", "node_type": "d", "x": depot_x, "y": depot_y,
            "delivery_demand": 0, "delivery_ready": 0, "delivery_due": 1440,
            "service_time": 0, "pickup_ready": 0, "pickup_due": 1440,
            "assigned_sat": "", "container_count": 0,
            "delivery_container_count": 0, "pickup_container_count": 0,
            "source_aoi_id": "", "source_region_id": "",
        }]
        for satellite in satellites:
            instance_rows.append({
                "node_id": satellite["node_id"], "node_type": "s",
                "x": satellite["x"], "y": satellite["y"],
                "delivery_demand": 0, "delivery_ready": 0, "delivery_due": 1440,
                "service_time": 0, "pickup_ready": 0, "pickup_due": 1440,
                "assigned_sat": "", "container_count": 0,
                "delivery_container_count": 0, "pickup_container_count": 0,
                "source_aoi_id": "", "source_region_id": satellite["source_region_id"],
            })

        for node in customer_nodes:
            aoi_id = int(node["source_aoi_id"])
            delivery_count = int(current_counts.get((date, aoi_id), 0))
            pickup_count = expected_pickups(
                history_counts[aoi_id], date, args.return_rate, lag_weights
            )
            delivery_ready, delivery_due = windows[aoi_id]
            pickup_ready = max(args.pickup_ready, delivery_due)
            service_time = min(
                20.0, max(5.0, 3.0 + args.service_minutes_per_container * max(delivery_count, pickup_count))
            )
            row = {
                "node_id": node["node_id"], "node_type": "c",
                "x": node["x"], "y": node["y"],
                "delivery_demand": round(delivery_count * args.payload_kg_per_order, 6),
                "delivery_ready": delivery_ready, "delivery_due": delivery_due,
                "service_time": round(service_time, 3),
                "pickup_ready": pickup_ready, "pickup_due": args.pickup_due,
                "assigned_sat": f"S{node['source_region_id']}",
                "container_count": delivery_count,
                "delivery_container_count": delivery_count,
                "pickup_container_count": pickup_count,
                "source_aoi_id": aoi_id,
                "source_region_id": node["source_region_id"],
            }
            instance_rows.append(row)
            flow_rows.append({
                "study_date": date.strftime("%m-%d"),
                "node_id": node["node_id"],
                "source_region_id": node["source_region_id"],
                "source_aoi_id": aoi_id,
                "observed_delivery_orders": delivery_count,
                "modelled_pickup_containers": pickup_count,
                "delivery_ready_min": delivery_ready,
                "delivery_due_min": delivery_due,
                "pickup_ready_min": pickup_ready,
                "pickup_due_min": args.pickup_due,
            })

        instance_path = instances_dir / f"lade_hz_{date.strftime('%m%d')}_30aoi.csv"
        with instance_path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=FIELDNAMES)
            writer.writeheader()
            writer.writerows(instance_rows)

    flows = pd.DataFrame(flow_rows)
    flows.to_csv(output_dir / "daily_flows.csv", index=False, encoding="utf-8")

    matrix_source = "local_equirectangular_straight_line"
    matrix_quality = {"status": "fallback; no road-network snapping"}
    if args.fetch_osrm:
        osrm = fetch_osrm_table(geo_nodes, args.osrm_url)
        (output_dir / "osrm_table_response.json").write_text(
            json.dumps(osrm, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        distances = [[value / 1000.0 for value in row] for row in osrm["response"]["distances"]]
        durations = [[value / 60.0 for value in row] for row in osrm["response"]["durations"]]
        matrix_source = "OSRM public driving table snapshot (OpenStreetMap road network)"
        matrix_quality = {
            "max_source_snap_distance_m": max(
                float(item["distance"]) for item in osrm["response"]["sources"]
            ),
            "max_destination_snap_distance_m": max(
                float(item["distance"]) for item in osrm["response"]["destinations"]
            ),
            "note": "The largest snap is the public cargo-hub proxy coordinate; the raw response is retained.",
        }
    else:
        distances = []
        durations = []
        for left in geo_nodes:
            distance_row, duration_row = [], []
            for right in geo_nodes:
                dx = left["x"] - right["x"]
                dy = left["y"] - right["y"]
                distance = math.hypot(dx, dy)
                distance_row.append(distance)
                duration_row.append(distance / args.fallback_speed_km_per_min)
            distances.append(distance_row)
            durations.append(duration_row)

    node_ids = [node["node_id"] for node in geo_nodes]
    write_square_matrix(output_dir / "distance_km.csv", node_ids, distances)
    write_square_matrix(output_dir / "duration_min.csv", node_ids, durations)

    daily_summary = flows.groupby("study_date").agg(
        delivery_containers=("observed_delivery_orders", "sum"),
        pickup_containers=("modelled_pickup_containers", "sum"),
    ).reset_index().to_dict(orient="records")
    metadata = {
        "case_name": "LaDe-D Hangzhou 3-region 30-AOI rolling-week case",
        "source_dataset": "Cainiao-AI/LaDe-D",
        "source_url": "https://huggingface.co/datasets/Cainiao-AI/LaDe-D",
        "source_license": "Apache-2.0 (Hugging Face dataset card)",
        "source_file_sha256": "00b6b4dca471cadee796a3fc7f07dcd919499ec58459ea67db784dcb06089e75",
        "study_year": "anonymous year; original file exposes MMDD only",
        "selected_period": [dates[0].strftime("%m-%d"), dates[-1].strftime("%m-%d")],
        "selected_regions": regions,
        "aois_per_region": args.aois_per_region,
        "customer_count": len(customer_nodes),
        "selection_rule": "top weekly orders among AOIs active on all seven selected days",
        "satellite_location_basis": "median LaDe accept_gps coordinate by region/week",
        "central_depot": DEPOT,
        "distance_matrix_source": matrix_source,
        "distance_matrix_quality": matrix_quality,
        "delivery_time_window_basis": (
            "LaDe observed delivery completion 5th-95th percentile, expanded by 30 minutes, "
            "rounded to 30 minutes, and widened to at least 6 hours; this is a service-band proxy, "
            "not an observed promised time window"
        ),
        "pickup_time_window_basis": (
            "designed window: pickup starts no earlier than both the configured afternoon threshold "
            "and the delivery due time; it ends at the configured evening cutoff"
        ),
        "return_model": {
            "eventual_return_rate": args.return_rate,
            "lag_day_probability": lag_weights,
            "formula": "pickup[i,t] = round(return_rate * sum_lag p[lag] * delivery[i,t-lag])",
            "status": "scenario assumption; LaDe-D has no reusable-packaging return records",
        },
        "payload_kg_per_order": {
            "value": args.payload_kg_per_order,
            "status": "scenario assumption; LaDe-D has no parcel-weight field",
        },
        "daily_summary": daily_summary,
    }
    (output_dir / "metadata.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return metadata


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--regions", nargs="+", default=[114, 124, 30], type=int)
    parser.add_argument("--aois-per-region", type=int, default=10)
    parser.add_argument("--start", default="08-23", help="MM-DD in LaDe anonymous year")
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--return-rate", type=float, default=0.97)
    parser.add_argument(
        "--lag-weights",
        type=json.loads,
        default={"1": 0.15, "2": 0.45, "3": 0.30, "4": 0.10},
        help='JSON object, e.g. {"1":0.15,"2":0.45,"3":0.30,"4":0.10}',
    )
    parser.add_argument("--pickup-ready", type=int, default=840)
    parser.add_argument("--pickup-due", type=int, default=1260)
    parser.add_argument("--payload-kg-per-order", type=float, default=2.0)
    parser.add_argument("--service-minutes-per-container", type=float, default=0.15)
    parser.add_argument("--fetch-osrm", action="store_true")
    parser.add_argument("--osrm-url", default="https://router.project-osrm.org")
    parser.add_argument("--fallback-speed-km-per-min", type=float, default=0.5)
    args = parser.parse_args()
    metadata = build(args)
    print(json.dumps(metadata["daily_summary"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
