# -*- coding: utf-8 -*-
"""Audit Model 6 transshipment value with a dedicated second-stage budget."""
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from generate_cost_analysis import load_json, solve_route_model
from models.model_6.alns_solver import solve_alns as solve_m6
from utils.economic_cost import EconomicParams
from utils.matrix_loader import load_square_matrix


DEFAULT_CASE = ROOT / "data/real_case/processed/lade_hz_0823_0829_30aoi"
DEFAULT_OUTPUT = ROOT / "output/m6_transshipment_validation_lade_real_0829"


def resolve(path: str) -> Path:
    candidate = Path(path)
    return candidate if candidate.is_absolute() else ROOT / candidate


def sample_limit(value: int) -> int | None:
    return None if value <= 0 else value


def write_csv(path: Path, rows: list[dict], fields: list[str]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def fmt(value: float | int | None, digits: int = 4) -> str:
    if value is None:
        return "—"
    if isinstance(value, int):
        return str(value)
    return f"{value:.{digits}f}"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--data",
        default=str(DEFAULT_CASE / "instances/lade_hz_0829_30aoi.csv"),
    )
    parser.add_argument("--distance-matrix", default=str(DEFAULT_CASE / "distance_km.csv"))
    parser.add_argument("--duration-matrix", default=str(DEFAULT_CASE / "duration_min.csv"))
    parser.add_argument("--fleet-config", default="config/fleet_config_lade_real_case.json")
    parser.add_argument("--economic-config", default="config/economic_cost_config_lade_real_case.json")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--total-time", type=float, default=90.0)
    parser.add_argument("--stage1-ratio", type=float, default=0.75)
    parser.add_argument("--seeds", nargs="+", type=int, default=[42, 43, 44])
    parser.add_argument("--sample-pickups", type=int, default=0,
                        help="0 means enumerate all pickup candidates")
    parser.add_argument("--sample-locations", type=int, default=0,
                        help="0 means enumerate all handoff locations")
    parser.add_argument("--sample-carriers", type=int, default=0,
                        help="0 means enumerate all carrier routes")
    parser.add_argument("--position-top-k", type=int, default=0,
                        help="0 means enumerate all insertion positions")
    args = parser.parse_args()

    if not 0.0 <= args.stage1_ratio <= 1.0:
        raise ValueError("--stage1-ratio must be between 0 and 1")
    stage2_budget = args.total_time * (1.0 - args.stage1_ratio)
    if stage2_budget <= 0:
        raise ValueError("The requested split leaves no transshipment budget")

    data_path = resolve(args.data)
    distance_path = resolve(args.distance_matrix)
    duration_path = resolve(args.duration_matrix)
    fleet_cfg = load_json(resolve(args.fleet_config))
    params = EconomicParams.from_config(load_json(resolve(args.economic_config)))
    distance_matrix = load_square_matrix(distance_path)
    duration_matrix = load_square_matrix(duration_path)
    output = resolve(args.output)
    output.mkdir(parents=True, exist_ok=True)

    limits = {
        "pickups": sample_limit(args.sample_pickups),
        "locations": sample_limit(args.sample_locations),
        "carriers": sample_limit(args.sample_carriers),
        "positions": sample_limit(args.position_top_k),
    }
    runs: list[dict] = []
    events: list[dict] = []

    for seed in args.seeds:
        result = solve_route_model(
            6,
            solve_m6,
            data_path,
            fleet_cfg,
            args.total_time,
            seed,
            solver_kwargs={
                "return_info": True,
                "strong_initial_solution": True,
                "max_iterations": 1_000_000,
                "stage1_time_ratio": args.stage1_ratio,
                "transshipment_time_budget": stage2_budget,
                "transshipment_sample_pickups": limits["pickups"],
                "transshipment_sample_locations": limits["locations"],
                "transshipment_sample_carriers": limits["carriers"],
                "transshipment_position_top_k": limits["positions"],
                "refill_unused_time": False,
            },
            distance_matrix=distance_matrix,
            duration_matrix=duration_matrix,
        )
        info = result.get("solver_info") or {}
        stage1 = info.get("stage1_objective")
        after = result.get("cost")
        distance_saving = (
            float(stage1) - float(after)
            if stage1 is not None and after is not None
            else None
        )
        if distance_saving is not None and abs(distance_saving) <= 1e-9:
            distance_saving = 0.0
        row = {
            "seed": seed,
            "status": result["status"],
            "total_time_budget_seconds": args.total_time,
            "stage1_ratio": args.stage1_ratio,
            "stage1_budget_seconds": args.total_time * args.stage1_ratio,
            "stage2_budget_seconds": stage2_budget,
            "stage1_distance_km": stage1,
            "after_transshipment_distance_km": after,
            "transshipment_distance_saving_km": distance_saving,
            "transshipment_transport_saving_yuan": (
                distance_saving * params.e2_cost_per_km
                if distance_saving is not None else None
            ),
            "transshipment_count": info.get("transshipment_count", 0),
            "transshipment_search_elapsed_seconds": info.get(
                "transshipment_search_elapsed_seconds"
            ),
            "total_runtime_seconds": result.get("time"),
            "stage1_iterations": info.get("iterations"),
            "e1_distance_after_km": result.get("e1_distance"),
            "e2_distance_after_km": result.get("e2_distance"),
            "sample_pickups": "all" if limits["pickups"] is None else limits["pickups"],
            "sample_locations": "all" if limits["locations"] is None else limits["locations"],
            "sample_carriers": "all" if limits["carriers"] is None else limits["carriers"],
            "position_top_k": "all" if limits["positions"] is None else limits["positions"],
        }
        runs.append(row)
        for index, event in enumerate(info.get("transshipment_events", []), start=1):
            saving = event.get("actual_distance_saving")
            events.append({
                "seed": seed,
                "event_index": index,
                **event,
                "actual_transport_saving_yuan": (
                    saving * params.e2_cost_per_km if saving is not None else None
                ),
            })
        print(json.dumps(row, ensure_ascii=False), flush=True)

    feasible = [row for row in runs if row["status"] == "OK"]
    best = min(feasible, key=lambda row: row["after_transshipment_distance_km"])
    positive = [row for row in feasible if row["transshipment_count"] > 0]
    summary = {
        "data": str(data_path),
        "distance_matrix": str(distance_path),
        "duration_matrix": str(duration_path),
        "seeds": args.seeds,
        "total_time_budget_seconds_per_seed": args.total_time,
        "stage1_ratio": args.stage1_ratio,
        "stage1_budget_seconds": args.total_time * args.stage1_ratio,
        "stage2_budget_seconds": stage2_budget,
        "candidate_limits": limits,
        "refill_unused_time": False,
        "runs_with_transshipment": len(positive),
        "total_runs": len(feasible),
        "total_transshipments": sum(row["transshipment_count"] for row in feasible),
        "total_internal_distance_saving_km": sum(
            row["transshipment_distance_saving_km"] or 0.0 for row in feasible
        ),
        "best_seed": best["seed"],
        "best_after_transshipment_distance_km": best["after_transshipment_distance_km"],
        "runs": runs,
        "events": events,
    }
    (output / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    write_csv(output / "runs.csv", runs, list(runs[0]))
    event_fields = [
        "seed", "event_index", "satellite", "pickup_node",
        "original_pickup_location", "handoff_location",
        "beneficiary_route_index", "carrier_route_index",
        "estimated_net_distance_saving", "distance_before", "distance_after",
        "actual_distance_saving", "actual_transport_saving_yuan",
    ]
    write_csv(output / "transshipment_events.csv", events, event_fields)

    lines = [
        "# Model 6 中转阶段扩展预算验证", "",
        f"- 每个种子总预算：{args.total_time:g} 秒",
        f"- 第一阶段：{args.stage1_ratio:.0%}（{args.total_time * args.stage1_ratio:g} 秒）",
        f"- 中转阶段：{1 - args.stage1_ratio:.0%}（{stage2_budget:g} 秒）",
        f"- 随机种子：{', '.join(map(str, args.seeds))}",
        "- 候选抽样：回收点、交接位置、接收车辆和插入位置均不抽样（在时间预算内枚举）",
        "- 未使用的中转时间不返还普通 ALNS，保证中转前后差值只来自中转阶段。",
        "", "| seed | 中转前/km | 中转后/km | 同次运行中转节省/km | 中转节省/元 | 中转数 | 中转搜索/s |",
        "|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in runs:
        lines.append(
            f"| {row['seed']} | {fmt(row['stage1_distance_km'])} | "
            f"{fmt(row['after_transshipment_distance_km'])} | "
            f"{fmt(row['transshipment_distance_saving_km'])} | "
            f"{fmt(row['transshipment_transport_saving_yuan'], 2)} | "
            f"{row['transshipment_count']} | "
            f"{fmt(row['transshipment_search_elapsed_seconds'], 3)} |"
        )
    lines += [
        "", "## 解释", "",
        "中转收益严格按同一次 Model 6 运行的阶段一方案与阶段二方案之差计算，"
        "不再使用独立运行的 Model 3 减 Model 6。",
    ]
    if positive:
        lines.append(
            f"{len(positive)}/{len(feasible)} 个种子找到中转，共 "
            f"{summary['total_transshipments']} 次，累计直接减少 "
            f"{summary['total_internal_distance_saving_km']:.4f} km。"
        )
    else:
        lines.append(
            "本轮扩展预算和候选枚举仍未找到正收益中转；这比 0.5 秒抽样测试提供了"
            "更强的负证据，但仍不是该模型或实例不存在中转机会的数学证明。"
        )
    (output / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Wrote {output / 'report.md'}")


if __name__ == "__main__":
    main()
