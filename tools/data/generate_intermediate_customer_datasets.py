#!/usr/bin/env python3
"""Generate intermediate Solomon-style customer-scale datasets.

The repository already has deterministic reduced datasets at 5/10/50/100
customers.  This script derives 15/20-customer instances as nested prefixes of
the existing 50-customer instances, preserving the original customer rows and
keeping only satellites used by the selected customers.
"""

from __future__ import annotations

import argparse
import csv
from pathlib import Path


FIELDNAMES = [
    "node_id",
    "node_type",
    "x",
    "y",
    "delivery_demand",
    "delivery_ready",
    "delivery_due",
    "service_time",
    "pickup_ready",
    "pickup_due",
    "assigned_sat",
]


def build_dataset(source_dir: Path, output_dir: Path, customer_count: int) -> int:
    output_dir.mkdir(parents=True, exist_ok=True)
    written = 0

    for source_path in sorted(source_dir.glob("*.csv")):
        with source_path.open("r", encoding="utf-8-sig", newline="") as f:
            rows = list(csv.DictReader(f))

        depot_rows = [row for row in rows if row["node_type"].lower() == "d"]
        satellite_rows = [row for row in rows if row["node_type"].lower() == "s"]
        customer_rows = [row for row in rows if row["node_type"].lower() == "c"]

        if len(customer_rows) < customer_count:
            raise ValueError(
                f"{source_path} has only {len(customer_rows)} customers; "
                f"cannot derive {customer_count}."
            )

        selected_customers = customer_rows[:customer_count]
        active_satellites = {row["assigned_sat"] for row in selected_customers}
        selected_satellites = [
            row for row in satellite_rows if row["node_id"] in active_satellites
        ]

        output_path = output_dir / source_path.name
        with output_path.open("w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
            writer.writeheader()
            writer.writerows(depot_rows)
            writer.writerows(selected_satellites)
            writer.writerows(selected_customers)

        written += 1

    return written


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Derive intermediate customer-scale datasets from 50_customers."
    )
    parser.add_argument(
        "--source-dir",
        type=Path,
        default=Path("data/50_customers"),
        help="Directory containing source CSV instances.",
    )
    parser.add_argument(
        "--scales",
        type=int,
        nargs="+",
        default=[15, 20],
        help="Customer counts to generate.",
    )
    args = parser.parse_args()

    for scale in args.scales:
        output_dir = Path("data") / f"{scale}_customers"
        count = build_dataset(args.source_dir, output_dir, scale)
        print(f"wrote {count} files to {output_dir}")


if __name__ == "__main__":
    main()
