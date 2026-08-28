# -*- coding: utf-8 -*-
"""Generate Beijing fresh-retail two-echelon VRPTW-PDD datasets.

The datasets use real-world Beijing place coordinates as spatial anchors and
synthetic demand/time-window rules for reusable fresh-food containers.
"""
from __future__ import annotations

import csv
import json
import math
import random
import urllib.error
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = ROOT / "data" / "beijing_fresh"
META_PATH = OUT_DIR / "beijing_fresh_metadata.json"

RNG = random.Random(20260603)

# Approximate WGS84 coordinates. The central warehouse is Beijing Xinfadi
# Agricultural Products Wholesale Market; satellites are district-level
# front-warehouse anchors chosen from recognizable Beijing areas.
DEPOT = {
    "id": "D0",
    "name": "北京新发地农产品批发市场",
    "lat": 39.8141,
    "lon": 116.3319,
    "role": "central_fresh_wholesale_market",
}

SATELLITES = [
    {"id": "S0", "name": "丰台前置仓", "lat": 39.8587, "lon": 116.2865},
    {"id": "S1", "name": "海淀前置仓", "lat": 39.9835, "lon": 116.3151},
    {"id": "S2", "name": "朝阳望京前置仓", "lat": 39.9963, "lon": 116.4800},
    {"id": "S3", "name": "通州前置仓", "lat": 39.9027, "lon": 116.6638},
    {"id": "S4", "name": "大兴前置仓", "lat": 39.7270, "lon": 116.3414},
    {"id": "S5", "name": "西城前置仓", "lat": 39.9148, "lon": 116.3523},
    {"id": "S6", "name": "东城前置仓", "lat": 39.8990, "lon": 116.4170},
    {"id": "S7", "name": "亦庄前置仓", "lat": 39.8067, "lon": 116.5064},
]

# Fallback POI seeds are real-world-style Beijing fresh retail/market anchors
# with coordinates around commercial districts. The script prefers Overpass
# data when available; these seeds keep the benchmark reproducible offline.
FALLBACK_POIS = [
    ("CaiShiKou Fresh Store", "fresh_store", 39.8893, 116.3748),
    ("Yuegezhuang Market Retail", "market", 39.8885, 116.2675),
    ("Fangzhuang Fresh Supermarket", "supermarket", 39.8588, 116.4316),
    ("Majiapu Community Fresh", "fresh_store", 39.8430, 116.3710),
    ("Wukesong Fresh Retail", "supermarket", 39.9130, 116.2739),
    ("Yuetan Greengrocer", "greengrocer", 39.9148, 116.3523),
    ("Xizhimen Fresh Store", "fresh_store", 39.9406, 116.3556),
    ("Zhongguancun Supermarket", "supermarket", 39.9835, 116.3151),
    ("Shangdi Community Fresh", "fresh_store", 40.0333, 116.3190),
    ("Wangjing Fresh Retail", "supermarket", 39.9963, 116.4800),
    ("Sanyuanqiao Fresh Store", "fresh_store", 39.9616, 116.4573),
    ("Shuangjing Supermarket", "supermarket", 39.8931, 116.4688),
    ("Guomao Fresh Retail", "fresh_store", 39.9087, 116.4605),
    ("Shilipu Community Fresh", "fresh_store", 39.9231, 116.4985),
    ("Tongzhou Beiyuan Fresh", "supermarket", 39.9038, 116.6359),
    ("Liyuan Fresh Store", "fresh_store", 39.8820, 116.6607),
    ("Majuqiao Market Retail", "market", 39.7550, 116.5515),
    ("Yizhuang Fresh Retail", "supermarket", 39.8067, 116.5064),
    ("Huangcun Fresh Store", "fresh_store", 39.7317, 116.3414),
    ("Xihongmen Supermarket", "supermarket", 39.7898, 116.3283),
]


def fetch_overpass_pois() -> list[dict]:
    """Fetch Beijing retail POIs from Overpass API."""
    query = """
[out:json][timeout:40];
(
  node["shop"~"^(supermarket|convenience|greengrocer)$"](39.70,116.15,40.08,116.75);
  way["shop"~"^(supermarket|convenience|greengrocer)$"](39.70,116.15,40.08,116.75);
  relation["shop"~"^(supermarket|convenience|greengrocer)$"](39.70,116.15,40.08,116.75);
  node["amenity"="marketplace"](39.70,116.15,40.08,116.75);
  way["amenity"="marketplace"](39.70,116.15,40.08,116.75);
  relation["amenity"="marketplace"](39.70,116.15,40.08,116.75);
);
out center 300;
"""
    req = urllib.request.Request(
        "https://overpass-api.de/api/interpreter",
        data=query.encode("utf-8"),
        headers={
            "Content-Type": "text/plain; charset=utf-8",
            "User-Agent": "2E-VRPTW-PDD thesis dataset generator",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            raw = resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="ignore")
        print(f"Overpass HTTP {exc.code}: {body[:500]}")
        return []
    except Exception as exc:
        print(f"Overpass fetch failed: {exc}")
        return []

    payload = json.loads(raw)
    pois = []
    seen = set()
    for el in payload.get("elements", []):
        tags = el.get("tags", {})
        lat = el.get("lat") or el.get("center", {}).get("lat")
        lon = el.get("lon") or el.get("center", {}).get("lon")
        if lat is None or lon is None:
            continue
        name = tags.get("name:zh") or tags.get("name") or tags.get("brand") or "Unnamed POI"
        kind = tags.get("shop") or tags.get("amenity") or "retail"
        key = (round(float(lat), 5), round(float(lon), 5), name)
        if key in seen:
            continue
        seen.add(key)
        pois.append({"name": name, "kind": kind, "lat": float(lat), "lon": float(lon), "source": "openstreetmap_overpass"})
    return pois


def latlon_to_xy(lat: float, lon: float, ref_lat: float = DEPOT["lat"], ref_lon: float = DEPOT["lon"]) -> tuple[float, float]:
    """Equirectangular projection in kilometers, scaled for existing solver magnitudes."""
    km_per_deg_lat = 111.32
    km_per_deg_lon = 111.32 * math.cos(math.radians(ref_lat))
    x_km = (lon - ref_lon) * km_per_deg_lon
    y_km = (lat - ref_lat) * km_per_deg_lat
    return round(x_km * 3.0, 3), round(y_km * 3.0, 3)


def nearest_sat(lat: float, lon: float) -> str:
    best = None
    best_d = float("inf")
    for sat in SATELLITES:
        dx = (lon - sat["lon"]) * math.cos(math.radians(lat))
        dy = lat - sat["lat"]
        d = dx * dx + dy * dy
        if d < best_d:
            best = sat["id"]
            best_d = d
    return best or SATELLITES[0]["id"]


def demand_rule(kind: str) -> tuple[int, int]:
    if kind in {"market", "marketplace"}:
        return RNG.randint(18, 45), RNG.randint(35, 55)
    if kind == "supermarket":
        return RNG.randint(12, 32), RNG.randint(25, 45)
    if kind == "greengrocer":
        return RNG.randint(8, 24), RNG.randint(18, 32)
    return RNG.randint(6, 20), RNG.randint(15, 30)


def time_window_rule(kind: str) -> tuple[int, int, int, int]:
    # Minutes from midnight. Fresh retail usually receives before opening or
    # before the lunch peak; empty containers are picked up later.
    if kind in {"market", "marketplace"}:
        start = RNG.choice([300, 330, 360])
        width = RNG.randint(90, 150)
        pickup_lag = RNG.randint(300, 420)
    elif kind == "supermarket":
        start = RNG.choice([360, 390, 420])
        width = RNG.randint(120, 210)
        pickup_lag = RNG.randint(360, 540)
    else:
        start = RNG.choice([420, 450, 480])
        width = RNG.randint(120, 240)
        pickup_lag = RNG.randint(300, 540)
    return start, start + width, start + pickup_lag, 1320


def expand_pois(base: list[dict], target: int) -> list[dict]:
    """Add small nearby synthetic branches when public POIs are sparse."""
    pois = list(base)
    idx = 0
    while len(pois) < target:
        seed = base[idx % len(base)]
        idx += 1
        # 0.003 deg is about 250-330 m in Beijing, suitable for nearby stores.
        lat = seed["lat"] + RNG.uniform(-0.006, 0.006)
        lon = seed["lon"] + RNG.uniform(-0.006, 0.006)
        pois.append({
            "name": f"{seed['name']} branch {idx}",
            "kind": seed["kind"],
            "lat": lat,
            "lon": lon,
            "source": "synthetic_branch_from_real_anchor",
        })
    return pois


def select_spatially_balanced(pois: list[dict], n: int) -> list[dict]:
    """Prefer a balanced mix across satellite service areas."""
    buckets = {sat["id"]: [] for sat in SATELLITES}
    for poi in pois:
        buckets[nearest_sat(poi["lat"], poi["lon"])].append(poi)
    for vals in buckets.values():
        RNG.shuffle(vals)

    selected = []
    quota = max(1, n // len(SATELLITES))
    for sid in buckets:
        selected.extend(buckets[sid][:quota])
    rest = [p for vals in buckets.values() for p in vals[quota:]]
    RNG.shuffle(rest)
    selected.extend(rest)
    return selected[:n]


def rows_for_dataset(pois: list[dict]) -> tuple[list[dict], list[dict]]:
    rows = []
    poi_meta = []
    dx, dy = latlon_to_xy(DEPOT["lat"], DEPOT["lon"])
    rows.append({
        "node_id": DEPOT["id"], "node_type": "d", "x": dx, "y": dy,
        "delivery_demand": 0.0, "delivery_ready": 0.0, "delivery_due": 9999.0,
        "service_time": 0.0, "pickup_ready": 0.0, "pickup_due": 9999.0,
        "assigned_sat": "",
    })
    for sat in SATELLITES:
        sx, sy = latlon_to_xy(sat["lat"], sat["lon"])
        rows.append({
            "node_id": sat["id"], "node_type": "s", "x": sx, "y": sy,
            "delivery_demand": 0.0, "delivery_ready": 0.0, "delivery_due": 9999.0,
            "service_time": 0.0, "pickup_ready": 0.0, "pickup_due": 9999.0,
            "assigned_sat": "",
        })
    for i, poi in enumerate(pois):
        kind = poi["kind"]
        demand, service = demand_rule(kind)
        dr, dd, pr, pd = time_window_rule(kind)
        x, y = latlon_to_xy(poi["lat"], poi["lon"])
        sat_id = nearest_sat(poi["lat"], poi["lon"])
        node_id = f"C{i}"
        rows.append({
            "node_id": node_id, "node_type": "c", "x": x, "y": y,
            "delivery_demand": float(demand), "delivery_ready": float(dr),
            "delivery_due": float(dd), "service_time": float(service),
            "pickup_ready": float(pr), "pickup_due": float(pd),
            "assigned_sat": sat_id,
        })
        poi_meta.append({
            "node_id": node_id,
            "name": poi["name"],
            "kind": kind,
            "lat": round(poi["lat"], 7),
            "lon": round(poi["lon"], 7),
            "assigned_sat": sat_id,
            "source": poi["source"],
        })
    return rows, poi_meta


def write_csv(path: Path, rows: list[dict]) -> None:
    fields = [
        "node_id", "node_type", "x", "y", "delivery_demand",
        "delivery_ready", "delivery_due", "service_time",
        "pickup_ready", "pickup_due", "assigned_sat",
    ]
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    pois = fetch_overpass_pois()
    if len(pois) < 20:
        pois = [
            {"name": name, "kind": kind, "lat": lat, "lon": lon, "source": "curated_real_beijing_anchor"}
            for name, kind, lat, lon in FALLBACK_POIS
        ]
    max_n = 100
    pois = expand_pois(pois, max_n)

    metadata = {
        "scenario": "Beijing Xinfadi central warehouse + district front warehouses + fresh-retail customers",
        "generated_at": "2026-06-03",
        "random_seed": 20260603,
        "coordinate_system": "WGS84 POI coordinates projected by equirectangular approximation; CSV x/y are kilometers scaled by 3 relative to depot, preserving spatial layout while matching the repository's Solomon-style time scale.",
        "depot": DEPOT,
        "satellites": SATELLITES,
        "demand_rule": "Synthetic reusable-container payload demand by POI type, calibrated to the repository fleet capacity: marketplace 18-45, supermarket 12-32, greengrocer 8-24, other/convenience 6-20. The model separately adds one 20-unit reusable container per customer.",
        "time_window_rule": "Delivery windows are morning fresh-retail receiving windows; pickup windows start later the same day and end at 22:00.",
        "files": [],
    }

    for n in [30, 60, 100]:
        selected = select_spatially_balanced(pois, n)
        rows, poi_meta = rows_for_dataset(selected)
        csv_path = OUT_DIR / f"beijing_fresh_{n}.csv"
        write_csv(csv_path, rows)
        metadata["files"].append({
            "file": str(csv_path.relative_to(ROOT)),
            "customers": n,
            "nodes": len(rows),
            "poi_sources": sorted({p["source"] for p in poi_meta}),
            "pois": poi_meta,
        })

    META_PATH.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Wrote {OUT_DIR}")


if __name__ == "__main__":
    main()
