# -*- coding: utf-8 -*-
"""Merge and summarize the Model 7 exact/ALNS experiment runs.

The script deliberately keeps the raw timestamped runs untouched.  It selects
one row per instance, writes stable machine-readable result files, and creates
the Markdown report used by the experiment index.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import re
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[2]


def _read_jsonl(paths: Iterable[Path]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in sorted(paths):
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if line:
                    rows.append(json.loads(line))
    return rows


def _small_score(row: dict[str, Any]) -> tuple[int, int, int, float]:
    """Prefer a proof, then an incumbent, then more completed partitions."""
    return (
        int(bool(row.get("proven_optimal"))),
        int(row.get("objective") is not None),
        int(row.get("assignment_partitions_evaluated") or 0),
        -float(row.get("objective") or math.inf),
    )


def _select_small(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    selected: dict[str, dict[str, Any]] = {}
    for row in rows:
        if int(row.get("model", -1)) != 7:
            continue
        if row.get("customers") is None or row.get("satellites") is None:
            match = re.match(r"Set1_(\d+)-(\d+)-", str(row.get("instance", "")))
            if not match:
                raise ValueError(f"cannot infer instance size from {row.get('instance')!r}")
            row.setdefault("customers", int(match.group(1)))
            row.setdefault("satellites", int(match.group(2)))
        key = str(row["instance"])
        if key not in selected or _small_score(row) > _small_score(selected[key]):
            selected[key] = row
    return sorted(selected.values(), key=lambda r: (int(r["customers"]), r["instance"]))


def _select_large(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    # Input paths are chronological, so later repair rows replace the original
    # SCRIPT_ERROR/NO_FEASIBLE_SOLUTION rows for the same instance.
    selected: dict[str, dict[str, Any]] = {}
    for row in rows:
        if int(row.get("model", -1)) == 7:
            selected[str(row["instance"])] = row
    return sorted(selected.values(), key=lambda r: (int(r["customers"]), r["instance"]))


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def _write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _fmt(value: Any, digits: int = 4) -> str:
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "是" if value else "否"
    if isinstance(value, (int, float)):
        return f"{value:.{digits}f}"
    return str(value)


def _mean(values: Iterable[float]) -> float | None:
    values = list(values)
    return statistics.mean(values) if values else None


def _small_report(rows: list[dict[str, Any]]) -> tuple[list[str], dict[str, Any]]:
    lines = [
        "## 小规模精确求解（120 秒/例）",
        "",
        "`OPTIMAL` 仅表示完整 Model 7 已获得全局最优性证明；`TIME_LIMIT/可行` "
        "表示有 incumbent 但证明未闭合；`TIME_LIMIT/无解` 仅表示限时内未得到 incumbent，"
        "并不等同于数学不可行。",
        "",
        "| 实例 | n | 状态 | 目标值 | 最佳下界 | gap | 时间/s | 分区进度 | 证明方式 |",
        "| --- | ---: | --- | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    grouped: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[int(row["customers"])].append(row)
        if row.get("proven_optimal"):
            status = "OPTIMAL"
        elif row.get("objective") is not None:
            status = "TIME_LIMIT/可行"
        else:
            status = "TIME_LIMIT/无 incumbent"
        progress = (
            f"{int(row.get('assignment_partitions_evaluated') or 0)}/"
            f"{int(row.get('assignment_partitions_total') or 0)}"
        )
        proof = "全局松弛证书" if row.get("certificate_type") else "限时分区枚举"
        lines.append(
            f"| {row['instance']} | {row['customers']} | {status} | "
            f"{_fmt(row.get('objective'))} | {_fmt(row.get('best_bound'))} | "
            f"{_fmt(row.get('mip_gap'))} | {_fmt(row.get('runtime'), 2)} | "
            f"{progress} | {proof} |"
        )

    lines += [
        "",
        "### 精确求解汇总",
        "",
        "| 客户数 | 实例数 | 已证明最优 | 限时可行 | 限时无 incumbent | 平均时间/s |",
        "| ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for n in sorted(grouped):
        group = grouped[n]
        proven = sum(bool(r.get("proven_optimal")) for r in group)
        feasible_unproven = sum(
            not r.get("proven_optimal") and r.get("objective") is not None for r in group)
        no_incumbent = len(group) - proven - feasible_unproven
        lines.append(
            f"| {n} | {len(group)} | {proven} | {feasible_unproven} | "
            f"{no_incumbent} | {_fmt(_mean(float(r['runtime']) for r in group), 2)} |"
        )
    total_proven = sum(bool(r.get("proven_optimal")) for r in rows)
    total_feasible_unproven = sum(
        not r.get("proven_optimal") and r.get("objective") is not None for r in rows)
    summary = {
        "instances": len(rows),
        "proven_optimal": total_proven,
        "feasible_unproven": total_feasible_unproven,
        "no_incumbent": len(rows) - total_proven - total_feasible_unproven,
    }
    lines.append(
        f"| 合计 | {len(rows)} | {summary['proven_optimal']} | "
        f"{summary['feasible_unproven']} | {summary['no_incumbent']} | "
        f"{_fmt(_mean(float(r['runtime']) for r in rows), 2)} |"
    )
    return lines, summary


def _large_report(rows: list[dict[str, Any]]) -> tuple[list[str], dict[str, Any]]:
    lines = [
        "## 大规模 ALNS（10 秒/例，seed=42）",
        "",
        "第一阶段执行 Model 4 自由分配 ALNS，第二阶段执行 Model 6 异步同箱中继后处理。"
        "“中继改善”按同一次运行的第一阶段目标值减最终目标值计算。",
        "",
        "| 实例 | n | 状态 | 最终目标值 | 第一阶段 | 中继改善 | 中继数 | 改派客户 | 时间/s | 成本复核误差 |",
        "| --- | ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in rows:
        audit_delta = (f"{float(row['route_cost_delta']):.3e}"
                       if row.get("route_cost_delta") is not None else "—")
        lines.append(
            f"| {row['instance']} | {row['customers']} | {row['status']} | "
            f"{_fmt(row.get('objective'))} | {_fmt(row.get('stage1_objective'))} | "
            f"{_fmt(row.get('transshipment_improvement'))} | "
            f"{_fmt(row.get('transshipment_count'), 0)} | "
            f"{_fmt(row.get('moved_customers'), 0)} | {_fmt(row.get('runtime'), 2)} | "
            f"{audit_delta} |"
        )

    feasible = [r for r in rows if r.get("feasible")]
    improvement_rows = [
        r for r in feasible if r.get("transshipment_improvement") is not None]
    audit_values = [abs(float(r["route_cost_delta"])) for r in feasible
                    if r.get("route_cost_delta") is not None]
    summary = {
        "instances": len(rows),
        "feasible": len(feasible),
        "infeasible_or_unresolved": len(rows) - len(feasible),
        "relay_instances": sum(int(r.get("transshipment_count") or 0) > 0 for r in feasible),
        "relay_count": sum(int(r.get("transshipment_count") or 0) for r in feasible),
        "relay_improvement_total": sum(
            float(r.get("transshipment_improvement") or 0.0) for r in feasible),
        "relay_improvement_mean": _mean(
            float(r.get("transshipment_improvement") or 0.0) for r in improvement_rows),
        "moved_customers_total": sum(int(r.get("moved_customers") or 0) for r in feasible),
        "runtime_mean": _mean(float(r["runtime"]) for r in rows),
        "max_abs_route_cost_delta": max(audit_values) if audit_values else None,
    }
    lines += [
        "",
        "### ALNS 汇总",
        "",
        f"- 可行率：{summary['feasible']}/{summary['instances']} "
        f"（{100.0 * summary['feasible'] / max(1, summary['instances']):.2f}%）。",
        f"- 触发中继：{summary['relay_instances']}/{summary['feasible']} 个可行实例，"
        f"共 {summary['relay_count']} 次中继。",
        f"- 中继总改善：{summary['relay_improvement_total']:.4f}；"
        f"可行实例平均改善：{summary['relay_improvement_mean']:.4f}。",
        f"- 自由分配相对原预分配共改派 {summary['moved_customers_total']} 个客户。",
        f"- 平均运行时间：{summary['runtime_mean']:.2f} 秒；"
        f"最大路线成本复核绝对误差：{summary['max_abs_route_cost_delta']:.3e}。",
    ]
    return lines, summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--small-dir", type=Path,
        default=ROOT / "output/results/model7_small_exact")
    parser.add_argument(
        "--large-dirs", type=Path, nargs="+",
        default=[ROOT / "output/results/model7_large_alns",
                 ROOT / "output/results/model7_large_alns_repair"])
    parser.add_argument(
        "--result-dir", type=Path,
        default=ROOT / "output/results/model7_exact_and_alns")
    parser.add_argument(
        "--report", type=Path,
        default=ROOT / "output/experiments/model7_exact_and_large_alns_experiment.md")
    args = parser.parse_args()

    small = _select_small(_read_jsonl(args.small_dir.glob("*.jsonl")))
    large_paths = [path for directory in args.large_dirs for path in directory.glob("*.jsonl")]
    large = _select_large(_read_jsonl(large_paths))
    if len(small) != 22:
        raise RuntimeError(f"expected 22 small instances, found {len(small)}")
    if len(large) != 18:
        raise RuntimeError(f"expected 18 large instances, found {len(large)}")

    args.result_dir.mkdir(parents=True, exist_ok=True)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    _write_jsonl(args.result_dir / "model7_small_exact_final.jsonl", small)
    _write_jsonl(args.result_dir / "model7_large_alns_t10_seed42_final.jsonl", large)
    _write_csv(
        args.result_dir / "model7_small_exact_final.csv", small,
        ["instance", "customers", "satellites", "status", "proven_optimal",
         "solution_type", "objective", "best_bound", "mip_gap", "runtime",
         "assignment_partitions_total", "assignment_partitions_evaluated",
         "certificate_type", "proof_scope", "transshipment_count", "assignment"])
    _write_csv(
        args.result_dir / "model7_large_alns_t10_seed42_final.csv", large,
        ["instance", "customers", "satellites", "status", "feasible", "objective",
         "stage1_objective", "transshipment_improvement", "transshipment_count",
         "moved_customers", "runtime", "iterations", "route_cost_audit",
         "route_cost_delta", "seed", "time_budget", "assignment"])

    small_lines, small_summary = _small_report(small)
    large_lines, large_summary = _large_report(large)
    report = [
        "# Model 7 小规模精确求解与大规模 ALNS 实验",
        "",
        "## 实验设置",
        "",
        "- 数据：Zhou `standard_same_day`；小规模为 Set1 的 7/10/12 客户共 22 例，"
        "大规模为 Set2 的 100/200 客户共 18 例。",
        "- 精确法：自由分配分区 + Model 6 异步同箱中继精确子问题；每例 120 秒。",
        "- ALNS：Model 4 自由分配搜索 + Model 6 中继后处理；每例 10 秒、seed=42。",
        "- 目标：E1 与 E2 总行驶距离；所有数值直接来自机器可读结果文件。",
        "",
        *small_lines,
        "",
        *large_lines,
        "",
        "## 结论",
        "",
        f"1. 精确法在 22 例中证明 {small_summary['proven_optimal']} 例全局最优，"
        f"另有 {small_summary['feasible_unproven']} 例得到限时 incumbent；"
        "自由分配分区数从 7 客户的 128 增至 12 客户的 4096，证明难度快速上升。",
        f"2. 大规模 ALNS 在 18 例中得到 {large_summary['feasible']} 个可行解；"
        f"其中 {large_summary['relay_instances']} 例实际采用中继，"
        f"同次运行内累计降低距离 {large_summary['relay_improvement_total']:.4f}。",
        "3. 精确法适合用来校验小实例和提供最优性证据；10 秒 ALNS 能覆盖 100/200 客户规模，"
        "体现了 Model 7 在大规模场景下的可计算性。",
        "4. `Set2_200-10-3` 在本次 10 秒预算与 seed=42 下未找到可行解；"
        "这是限时算法结果，不应解释为该实例在 Model 7 下数学不可行。",
        "",
        "## 可复现文件",
        "",
        "- `output/results/model7_exact_and_alns/model7_small_exact_final.jsonl` / `.csv`",
        "- `output/results/model7_exact_and_alns/model7_large_alns_t10_seed42_final.jsonl` / `.csv`",
        "- 原始时间戳结果保留在 `output/results/model7_small_exact/`、"
        "`output/results/model7_large_alns/` 和 `output/results/model7_large_alns_repair/`。",
    ]
    args.report.write_text("\n".join(report) + "\n", encoding="utf-8")
    print(json.dumps({"small": small_summary, "large": large_summary},
                     ensure_ascii=False, indent=2))
    print(f"Wrote {args.report}")


if __name__ == "__main__":
    main()
