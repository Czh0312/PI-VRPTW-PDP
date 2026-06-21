# experiments/build_model5_advanced_big_table.py
# -*- coding: utf-8 -*-
"""Build a wide table from Model 5 advanced comparison CSV files."""
import csv
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "output" / "experiments"

LABEL_ORDER = [
    "m2_baseline",
    "m3_baseline",
    "m4_baseline",
    "m5_base_greedy",
    "m5_conservative",
    "m5_greedy_prune",
    "m5_cons_prune",
    "m5_cons_prune_gate",
    "m5_cons_prune_refine",
    "m5_guard_greedy",
    "m5_guard_cons_gate",
    "m5_guard_refine",
]

LABEL_NAMES = {
    "m2_baseline": "Model 2",
    "m3_baseline": "Model 3",
    "m4_baseline": "Model 4",
    "m5_base_greedy": "M5 base greedy",
    "m5_conservative": "M5 conservative",
    "m5_greedy_prune": "M5 greedy+prune",
    "m5_cons_prune": "M5 cons+prune",
    "m5_cons_prune_gate": "M5 cons+prune+gate",
    "m5_cons_prune_refine": "M5 cons+prune+refine",
    "m5_guard_greedy": "M5 guard+greedy",
    "m5_guard_cons_gate": "M5 guard+cons+gate",
    "m5_guard_refine": "M5 guard+refine",
}


def main():
    files = sorted(
        path for path in OUT_DIR.glob(
            "model5_advanced_*_iter300_seed42_summary.csv")
        if "combined" not in path.name
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

    csv_path = OUT_DIR / "model5_advanced_big_table_iter300_seed42.csv"
    fields = ["dataset"]
    for label in LABEL_ORDER:
        fields.extend([f"{label}_cost", f"{label}_trans"])
    fields.extend(["best_method", "best_cost"])
    with csv_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)

    md_path = OUT_DIR / "model5_advanced_big_table_iter300_seed42.md"
    headers = (
        ["算例"] + [LABEL_NAMES[label] for label in LABEL_ORDER]
        + ["最优方案", "最优成本"]
    )
    lines = [
        "# Model 5 高级改进大表（iter=300, seed=42）",
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
                cell = (f"**{text}**"
                        if best != "" and abs(float(cost) - float(best)) < 1e-9
                        else text)
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
