# experiments/build_model7_strategy_big_table.py
# -*- coding: utf-8 -*-
"""Build a wide comparison table from Model 7 strategy summary CSV files."""
import csv
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "output" / "experiments"

LABEL_ORDER = [
    "m3_baseline",
    "m5_baseline",
    "m6_baseline",
    "m7_A_greedy_H1",
    "m7_B_range_H1",
    "m7_C_beam_H2",
    "m7_D_conservative_H1",
    "m7_A_greedy_H2",
]

LABEL_NAMES = {
    "m3_baseline": "Model 3",
    "m5_baseline": "Model 5",
    "m6_baseline": "Model 6",
    "m7_A_greedy_H1": "M7-A greedy H=1",
    "m7_B_range_H1": "M7-B range random",
    "m7_C_beam_H2": "M7-C beam H=2",
    "m7_D_conservative_H1": "M7-D conservative",
    "m7_A_greedy_H2": "M7-A greedy H=2",
}


def main():
    files = sorted(
        path for path in OUT_DIR.glob(
            "model7_strategies_*_iter300_seed42_summary.csv")
        if "all_" not in path.name and "combined" not in path.name
    )
    rows = []
    for path in files:
        with path.open(encoding="utf-8") as f:
            data = list(csv.DictReader(f))
        if not data:
            continue

        dataset = data[0]["dataset"]
        by_label = {row["label"]: row for row in data}
        out = {"dataset": dataset}
        costs = []

        for label in LABEL_ORDER:
            row = by_label.get(label)
            if (row and row["success"] == "True"
                    and row["cost"] not in ("", "None")):
                cost = float(row["cost"])
                trans = int(row["transshipments"])
                out[f"{label}_cost"] = cost
                out[f"{label}_trans"] = trans
                costs.append((cost, label))
            else:
                out[f"{label}_cost"] = ""
                out[f"{label}_trans"] = ""

        if costs:
            best_cost, best_label = min(costs)
            out["best_method"] = LABEL_NAMES[best_label]
            out["best_cost"] = best_cost
        else:
            out["best_method"] = ""
            out["best_cost"] = ""
        rows.append(out)

    rows.sort(key=lambda row: (
        0 if row["dataset"].startswith("10_") else 1,
        row["dataset"],
    ))

    csv_path = OUT_DIR / "model7_strategy_big_table_iter300_seed42.csv"
    fields = ["dataset"]
    for label in LABEL_ORDER:
        fields.extend([f"{label}_cost", f"{label}_trans"])
    fields.extend(["best_method", "best_cost"])

    with csv_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)

    md_path = OUT_DIR / "model7_strategy_big_table_iter300_seed42.md"
    headers = (
        ["算例"] + [LABEL_NAMES[label] for label in LABEL_ORDER]
        + ["最优方案", "最优成本"]
    )
    lines = [
        "# Model 3/5/6/7 策略大表（iter=300, seed=42）",
        "",
        "单元格格式：目标值 / 中转数。加粗表示该算例的最优目标值。",
        "",
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join(["---"] + ["---:"] * len(LABEL_ORDER)
                          + ["---", "---:"]) + " |",
    ]

    for row in rows:
        best = row["best_cost"]
        cells = [row["dataset"]]
        for label in LABEL_ORDER:
            cost = row[f"{label}_cost"]
            trans = row[f"{label}_trans"]
            if cost == "":
                cell = "-"
            else:
                text = f"{float(cost):.2f} / {int(trans)}"
                if best != "" and abs(float(cost) - float(best)) < 1e-9:
                    cell = f"**{text}**"
                else:
                    cell = text
            cells.append(cell)
        cells.extend([
            row["best_method"],
            f"{float(best):.2f}" if best != "" else "-",
        ])
        lines.append("| " + " | ".join(cells) + " |")

    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Markdown table: {md_path}")
    print(f"CSV table: {csv_path}")


if __name__ == "__main__":
    main()
