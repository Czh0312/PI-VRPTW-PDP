# experiments/build_model3_vs_best_model7_table.py
# -*- coding: utf-8 -*-
"""Build compact Model 3 vs best Model 7 comparison table."""
import csv
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "output" / "experiments"


def main():
    all_rows = []
    for pattern in [
        "model7_strategies_*_iter300_seed42_summary.csv",
        "model7_advanced_*_iter300_seed42_summary.csv",
    ]:
        for path in OUT_DIR.glob(pattern):
            if "combined" in path.name or "all_" in path.name:
                continue
            with path.open(encoding="utf-8") as f:
                all_rows.extend(csv.DictReader(f))

    rows = []
    datasets = sorted(
        {row["dataset"] for row in all_rows},
        key=lambda name: (0 if name.startswith("10_") else 1, name),
    )
    for dataset in datasets:
        data_rows = [
            row for row in all_rows
            if (row["dataset"] == dataset
                and row["success"] == "True"
                and row["cost"] not in ("", "None"))
        ]
        model3 = min(
            (row for row in data_rows if row["label"] == "m3_baseline"),
            key=lambda row: float(row["cost"]),
        )
        best_m7 = min(
            (row for row in data_rows if row["label"].startswith("m7_")),
            key=lambda row: float(row["cost"]),
        )
        model3_cost = float(model3["cost"])
        best_m7_cost = float(best_m7["cost"])
        gap = best_m7_cost - model3_cost
        rows.append({
            "dataset": dataset,
            "model3_cost": model3_cost,
            "model3_trans": int(model3["transshipments"]),
            "best_m7_cost": best_m7_cost,
            "best_m7_trans": int(best_m7["transshipments"]),
            "best_m7_label": best_m7["label"],
            "gap": gap,
            "gap_pct": gap / model3_cost * 100.0,
        })

    csv_path = OUT_DIR / "model3_vs_best_model7_iter300_seed42.csv"
    fields = [
        "dataset", "model3_cost", "model3_trans",
        "best_m7_cost", "best_m7_trans", "best_m7_label",
        "gap", "gap_pct",
    ]
    with csv_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)

    md_path = OUT_DIR / "model3_vs_best_model7_iter300_seed42.md"
    lines = [
        "# Model 3 vs 最佳 Model 7（iter=300, seed=42）",
        "",
        "gap = Best M7 - Model 3；负数表示 Model 7 更好。",
        "",
        "| 算例 | Model 3 | 最佳 Model 7 | M7策略 | gap | gap% | 结论 |",
        "| --- | ---: | ---: | --- | ---: | ---: | --- |",
    ]
    for row in rows:
        if row["gap"] < -1e-9:
            verdict = "更好"
        elif abs(row["gap"]) < 1e-9:
            verdict = "持平"
        else:
            verdict = "更差"
        lines.append(
            f"| {row['dataset']} "
            f"| {row['model3_cost']:.2f} / {row['model3_trans']} "
            f"| {row['best_m7_cost']:.2f} / {row['best_m7_trans']} "
            f"| {row['best_m7_label']} "
            f"| {row['gap']:.2f} "
            f"| {row['gap_pct']:.2f}% "
            f"| {verdict} |"
        )
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(f"Markdown table: {md_path}")
    print(f"CSV table: {csv_path}")


if __name__ == "__main__":
    main()

