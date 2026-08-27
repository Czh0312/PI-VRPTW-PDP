# -*- coding: utf-8 -*-
"""Print audit tables for the M1/M2/M3/M4/M6 standard-small benchmark."""
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OLD = ROOT / "results" / "mainline_small_exact" / "small_exact_20260825_123124.jsonl"
NEW_DIR = ROOT / "results" / "model6_standard_small_exact_unlimited"


def load(path):
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def instance_key(name):
    return int(name.split("_")[1].split("-")[0]), name


def main():
    new_path = max(NEW_DIR.glob("*.jsonl"), key=lambda path: path.stat().st_mtime)
    rows = load(OLD) + load(new_path)
    by_instance = {}
    for row in rows:
        by_instance.setdefault(row["instance"], {})[row["model"]] = row

    print("OBJECTIVE")
    for instance in sorted(by_instance, key=instance_key):
        values = by_instance[instance]
        cells = [f"{values[model]['objective']:.3f}" for model in (1, 2, 3, 4, 6)]
        reduction = (
            (values[3]["objective"] - values[6]["objective"])
            / values[3]["objective"] * 100.0
        )
        print(f"| {instance} | {' | '.join(cells)} | {reduction:.2f}% |")

    print("TIME")
    for instance in sorted(by_instance, key=instance_key):
        values = by_instance[instance]
        runtimes = [values[model]["runtime"] for model in (1, 2, 3, 4, 6)]
        cells = [f"{value:.3f}" for value in runtimes]
        print(f"| {instance} | {' | '.join(cells)} | {sum(runtimes):.3f} |")


if __name__ == "__main__":
    main()
