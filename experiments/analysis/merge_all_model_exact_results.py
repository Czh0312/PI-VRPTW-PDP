# -*- coding: utf-8 -*-
"""Merge the canonical M1/M2/M3/M4/M6/M7 small exact results."""
from __future__ import annotations

import csv
import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SOURCES = (
    ROOT / "output/results/mainline_small_exact/small_exact_20260825_123124.jsonl",
    ROOT / "output/results/model6_standard_small_exact_unlimited/small_exact_20260826_171404.jsonl",
    ROOT / "output/results/model7_exact_and_alns/model7_small_exact_final.jsonl",
)
OUTPUT_DIR = ROOT / "output/results/mainline_small_exact"
STEM = "small_exact_models123467_final"


def main() -> None:
    rows = []
    for source in SOURCES:
        for line in source.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            match = re.match(r"Set1_(\d+)-(\d+)-", row["instance"])
            if not match:
                raise ValueError(f"unexpected instance name: {row['instance']}")
            row.setdefault("customers", int(match.group(1)))
            row.setdefault("satellites", int(match.group(2)))
            rows.append(row)
    rows.sort(key=lambda r: (int(r["customers"]), r["instance"], int(r["model"])))
    expected_models = {1, 2, 3, 4, 6, 7}
    keys = {(r["instance"], int(r["model"])) for r in rows}
    instances = {r["instance"] for r in rows}
    missing = {(instance, model) for instance in instances for model in expected_models} - keys
    if len(rows) != 132 or len(instances) != 22 or missing:
        raise RuntimeError(
            f"invalid merged exact set: rows={len(rows)}, instances={len(instances)}, "
            f"missing={sorted(missing)}")

    jsonl_path = OUTPUT_DIR / f"{STEM}.jsonl"
    csv_path = OUTPUT_DIR / f"{STEM}.csv"
    jsonl_path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8")
    fields = []
    for row in rows:
        for field in row:
            if field not in fields:
                fields.append(field)
    with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    proven = sum(bool(r.get("proven_optimal")) for r in rows)
    incumbents = sum(
        not r.get("proven_optimal") and r.get("objective") is not None for r in rows)
    print(json.dumps({
        "rows": len(rows), "instances": len(instances),
        "proven_optimal": proven, "unproven_incumbents": incumbents,
        "time_limit_without_incumbent": len(rows) - proven - incumbents,
        "jsonl": str(jsonl_path), "csv": str(csv_path),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
