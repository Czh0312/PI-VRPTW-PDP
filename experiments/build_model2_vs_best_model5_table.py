# experiments/build_model2_vs_best_model5_table.py
# -*- coding: utf-8 -*-
"""Build compact Model 2 vs best Model 5 comparison table."""
import csv
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "output" / "experiments"


def main():
    all_rows = []
    for pattern in [
        "model5_strategies_*_iter300_seed42_summary.csv",
        "model5_advanced_*_iter300_seed42_summary.csv",
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
        model2 = min(
            (row for row in data_rows if row["label"] == "m2_baseline"),
            key=lambda row: float(row["cost"]),
        )
        best_m5 = min(
            (row for row in data_rows if row["label"].startswith("m5_")),
            key=lambda row: float(row["cost"]),
        )
        model2_cost = float(model2["cost"])
        best_m5_cost = float(best_m5["cost"])
        gap = best_m5_cost - model2_cost
        rows.append({
            "dataset": dataset,
            "model2_cost": model2_cost,
            "model2_trans": int(model2["transshipments"]),
            "best_m5_cost": best_m5_cost,
            "best_m5_trans": int(best_m5["transshipments"]),
            "best_m5_label": best_m5["label"],
            "gap": gap,
            "gap_pct": gap / model2_cost * 100.0,
        })

    csv_path = OUT_DIR / "model2_vs_best_model5_iter300_seed42.csv"
    fields = [
        "dataset", "model2_cost", "model2_trans",
        "best_m5_cost", "best_m5_trans", "best_m5_label",
        "gap", "gap_pct",
    ]
    with csv_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)

    md_path = OUT_DIR / "model2_vs_best_model5_iter300_seed42.md"
    lines = [
        "# Model 2 vs 最佳 Model 5（iter=300, seed=42）",
        "",
        "gap = Best M5 - Model 2；负数表示 Model 5 更好。",
        "",
        "| 算例 | Model 2 | 最佳 Model 5 | M5策略 | gap | gap% | 结论 |",
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
            f"| {row['model2_cost']:.2f} / {row['model2_trans']} "
            f"| {row['best_m5_cost']:.2f} / {row['best_m5_trans']} "
            f"| {row['best_m5_label']} "
            f"| {row['gap']:.2f} "
            f"| {row['gap_pct']:.2f}% "
            f"| {verdict} |"
        )
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(f"Markdown table: {md_path}")
    print(f"CSV table: {csv_path}")


if __name__ == "__main__":
    main()

