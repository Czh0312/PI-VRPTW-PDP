# -*- coding: utf-8 -*-
"""Extract an auditable parameter summary from the official R3PACK workbook."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SOURCE = ROOT / 'archive/datasets/legacy_20261015/data/real_case/raw' / "r3pack" / "D3.5_R3PACK_Dataset.xlsx"
DEFAULT_OUTPUT = ROOT / 'output/inspections/real_case' / "r3pack_parameter_audit.json"


def numeric_summary(
    frame: pd.DataFrame, column: str, include_unique: bool = True
) -> dict:
    series = pd.to_numeric(frame[column], errors="coerce").dropna()
    result = {
        "min": float(series.min()),
        "median": float(series.median()),
        "max": float(series.max()),
    }
    if include_unique:
        result["unique"] = sorted(float(value) for value in series.unique())
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    workbook = pd.ExcelFile(args.source)
    packaging = pd.read_excel(args.source, sheet_name="PackagingList")
    product = pd.read_excel(args.source, sheet_name="Product")
    parameters = pd.read_excel(args.source, sheet_name="PARAMETERS").iloc[0]
    transport = pd.read_excel(args.source, sheet_name="Transport").iloc[0]
    washing = pd.read_excel(args.source, sheet_name="WasherPackaging")
    sorting = pd.read_excel(args.source, sheet_name="Sorter_Packaging")
    distances = pd.read_excel(args.source, sheet_name="Distances")

    digest = hashlib.md5(args.source.read_bytes()).hexdigest()
    report = {
        "source": {
            "title": "R3PACK WP3 Dataset for Deliverable D3.5",
            "doi": "10.5281/zenodo.21392137",
            "url": "https://zenodo.org/records/21392137",
            "file": args.source.name,
            "md5": digest,
            "official_md5": "431ae25f99695d68d26747062918cff6",
            "checksum_matches": digest == "431ae25f99695d68d26747062918cff6",
            "license_note": "Zenodo record exposes no explicit license value; verify before redistribution.",
        },
        "workbook": {
            "sheet_count": len(workbook.sheet_names),
            "sheets": workbook.sheet_names,
        },
        "raw_parameter_summary": {
            "packaging_weight_wkg_m": numeric_summary(packaging, "wkg_m"),
            "packaging_production_cost_CostoProd_m": numeric_summary(packaging, "CostoProd_m"),
            "packaging_scrap_rate_ScrapRate_m": numeric_summary(packaging, "ScrapRate_m"),
            "product_turn_index": numeric_summary(product, "TurnIndex"),
            "washing_cost_c_wm": numeric_summary(washing, "c_wm"),
            "sorting_cost_c_om": numeric_summary(sorting, "c_om"),
            "transport": {
                "mode": str(transport["T"]),
                "capacity_cap_t": float(transport["cap_t"]),
                "unit_cost_dc_t": float(transport["dc_t"]),
                "environment_factor_e_t": float(transport["e_t"]),
            },
            "parameters": {
                "COCP": float(parameters["COCP"]),
                "average_speed_VelMedia": float(parameters["VelMedia"]),
                "CTemp": float(parameters["CTemp"]),
            },
            "distance_rows": int(len(distances)),
            "distance_value": numeric_summary(distances, "Distance", include_unique=False),
        },
        "mapping_decision": {
            "directly_usable": [
                "ScrapRate_m as an international scrap-rate sensitivity point (raw value 0.10)",
                "TurnIndex range as international reuse-rotation sensitivity points",
                "VelMedia=30 as a transparent urban-network speed reference",
            ],
            "not_directly_transferred_to_china_baseline": [
                "dc_t transport cost",
                "c_wm washing cost",
                "c_om sorting cost",
                "CostoProd_m packaging production cost",
            ],
            "reason": (
                "The workbook headers do not state currency/unit definitions and the network covers "
                "France, Belgium and Luxembourg. These values are retained as international "
                "sensitivity/calibration evidence, while the mainland-China monetary baseline remains separate."
            ),
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report["raw_parameter_summary"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
