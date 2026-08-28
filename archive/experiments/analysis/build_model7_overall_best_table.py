# experiments/analysis/build_model7_overall_best_table.py
# -*- coding: utf-8 -*-
"""Build compact best-of table across baseline, strategy, and advanced runs."""
import csv
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = ROOT / "output" / "experiments"

BASELINE_LABELS = {
    "m3_baseline": "Model 3",
    "m5_baseline": "Model 5",
    "m6_baseline": "Model 6",
}


def _read_rows(pattern):
    rows = []
    for path in sorted(OUT_DIR.glob(pattern)):
        if "combined" in path.name or "all_" in path.name:
            continue
        with path.open(encoding="utf-8") as f:
            rows.extend(csv.DictReader(f))
    return rows


def _best(rows, predicate):
    candidates = []
    for row in rows:
        if not predicate(row):
            continue
        if row.get("success") != "True" or row.get("cost") in ("", "None"):
            continue
        candidates.append((float(row["cost"]), row))
    if not candidates:
        return None
    return min(candidates, key=lambda item: item[0])[1]


def _fmt(row):
    if not row:
        return "-", "", ""
    cost = float(row["cost"])
    trans = int(row["transshipments"])
    return f"{cost:.2f} / {trans}", row["label"], cost


def main():
    strategy_rows = _read_rows("model7_strategies_*_iter300_seed42_summary.csv")
    advanced_rows = _read_rows("model7_advanced_*_iter300_seed42_summary.csv")
    datasets = sorted({
        row["dataset"] for row in strategy_rows + advanced_rows
    }, key=lambda name: (0 if name.startswith("10_") else 1, name))

    out_rows = []
    for dataset in datasets:
        s_rows = [row for row in strategy_rows if row["dataset"] == dataset]
        a_rows = [row for row in advanced_rows if row["dataset"] == dataset]
        all_rows = s_rows + a_rows

        row_out = {"dataset": dataset}
        for label in BASELINE_LABELS:
            best_row = _best(all_rows, lambda r, label=label: r["label"] == label)
            cell, _label, cost = _fmt(best_row)
            row_out[label] = cell
            row_out[f"{label}_cost"] = cost

        prev_best = _best(s_rows, lambda r: r["label"].startswith("m7_"))
        adv_best = _best(a_rows, lambda r: r["label"].startswith("m7_"))
        overall = _best(all_rows, lambda r: True)

        cell, label, cost = _fmt(prev_best)
        row_out["prev_m7_best"] = cell
        row_out["prev_m7_label"] = label
        row_out["prev_m7_cost"] = cost

        cell, label, cost = _fmt(adv_best)
        row_out["advanced_m7_best"] = cell
        row_out["advanced_m7_label"] = label
        row_out["advanced_m7_cost"] = cost

        cell, label, cost = _fmt(overall)
        row_out["overall_best"] = cell
        row_out["overall_label"] = BASELINE_LABELS.get(label, label)
        row_out["overall_cost"] = cost
        out_rows.append(row_out)

    csv_path = OUT_DIR / "model7_overall_best_table_iter300_seed42.csv"
    fields = [
        "dataset",
        "m3_baseline", "m5_baseline", "m6_baseline",
        "prev_m7_best", "prev_m7_label",
        "advanced_m7_best", "advanced_m7_label",
        "overall_best", "overall_label",
    ]
    with csv_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for row in out_rows:
            writer.writerow({field: row.get(field, "") for field in fields})

    md_path = OUT_DIR / "model7_overall_best_table_iter300_seed42.md"
    headers = [
        "算例", "Model 3", "Model 5", "Model 6",
        "上一轮最佳M7", "上一轮M7策略",
        "高级最佳M7", "高级M7策略",
        "全体最优", "最优方案",
    ]
    lines = [
        "# Model 7 总体最佳对比表（iter=300, seed=42）",
        "",
        "单元格格式：目标值 / 中转数。",
        "",
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join(["---"] + ["---:"] * 3
                          + ["---:", "---", "---:", "---", "---:", "---"])
        + " |",
    ]
    for row in out_rows:
        cells = [
            row["dataset"],
            row["m3_baseline"],
            row["m5_baseline"],
            row["m6_baseline"],
            row["prev_m7_best"],
            row["prev_m7_label"],
            row["advanced_m7_best"],
            row["advanced_m7_label"],
            row["overall_best"],
            row["overall_label"],
        ]
        lines.append("| " + " | ".join(cells) + " |")

    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Markdown table: {md_path}")
    print(f"CSV table: {csv_path}")


if __name__ == "__main__":
    main()
