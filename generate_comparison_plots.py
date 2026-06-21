# -*- coding: utf-8 -*-
"""
Regenerate comparison figures from saved comparison JSON.

This is output-only: it does not run solvers or change experiment results.
"""
import json
import shutil
from pathlib import Path

from run_comparison import plot_all


PROJECT_ROOT = Path(__file__).resolve().parent
OUTPUT_DIR = PROJECT_ROOT / "output" / "comparison"
RESULTS_FILE = OUTPUT_DIR / "00_data" / "data_01_comparison_summary_by_scale_model_solver.json"
PREVIOUS_RESULTS_FILE = OUTPUT_DIR / "00_data" / "comparison_results.json"
LEGACY_RESULTS_FILE = OUTPUT_DIR / "comparison_results.json"


def load_avg_results(path):
    with open(path, "r", encoding="utf-8") as f:
        raw = json.load(f)

    avg_results = {}
    for scale, entries in raw.items():
        avg_results[scale] = {}
        for key, data in entries.items():
            model_s, solver = key.split("_", 1)
            model = int(model_s[1:])
            avg_results[scale][(model, solver)] = {
                "cost_mean": data.get("cost_mean", 0),
                "cost_std": data.get("cost_std", 0),
                "time_mean": data.get("time_mean", 0),
                "time_std": data.get("time_std", 0),
                "e1_mean": data.get("e1_mean", 0),
                "e1_std": data.get("e1_std", 0),
                "e2_mean": data.get("e2_mean", 0),
                "e2_std": data.get("e2_std", 0),
                "n": data.get("n_ok", 0),
                "n_total": data.get("n_total", 0),
            }
    return avg_results


def main():
    if RESULTS_FILE.exists():
        results_file = RESULTS_FILE
    elif PREVIOUS_RESULTS_FILE.exists():
        results_file = PREVIOUS_RESULTS_FILE
    else:
        results_file = LEGACY_RESULTS_FILE
    if not results_file.exists():
        raise FileNotFoundError(f"Missing result file: {RESULTS_FILE}")
    if results_file != RESULTS_FILE:
        RESULTS_FILE.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(results_file, RESULTS_FILE)
        results_file = RESULTS_FILE
    avg_results = load_avg_results(results_file)
    plot_all(avg_results, OUTPUT_DIR)
    print(f"Read data from: {results_file}")
    print(f"Regenerated comparison figures in: {OUTPUT_DIR}")


if __name__ == "__main__":
    main()
