"""Audit paired JSONL repair probes without mixing intermediate solver versions."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import statistics


def read_rows(path, model=None):
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    return [row for row in rows if model is None or row["model"] == model]


def key(row):
    return row["model"], row["mode"], row["instance"], row["seed"]


def same_routes(left, right):
    return all(left[name] == right[name] for name in ("e1_routes", "e2_routes"))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--before", type=Path, required=True)
    parser.add_argument("--after3", type=Path, required=True)
    parser.add_argument("--after6", type=Path, required=True)
    parser.add_argument("--repeat6", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    baseline = {key(row): row for row in read_rows(args.before)}
    current = {key(row): row for row in read_rows(args.after3, 3) + read_rows(args.after6, 6)}
    if baseline.keys() != current.keys():
        raise ValueError(f"Unpaired records: {baseline.keys() ^ current.keys()}")
    pairs = []
    for identity, after in current.items():
        before = baseline[identity]
        model, mode, instance, seed = identity
        if model == 6 and before["replay_sha256"] != after["replay_sha256"]:
            raise ValueError(f"Changed inherited initial solution: {identity}")
        stage_before = before["engine_stages"][0] if model == 6 else before
        stage_after = after["engine_stages"][0] if model == 6 else after
        pairs.append({"model": model, "mode": mode, "instance": instance, "seed": seed,
            "before_objective": before["objective"], "after_objective": after["objective"],
            "improvement_pct": 100 * (before["objective"] - after["objective"]) / before["objective"],
            "before_wall": before["wall_time"], "after_wall": after["wall_time"],
            "before_iterations": before["iterations"], "after_iterations": after["iterations"],
            "before_stage_seconds": stage_before.get("elapsed", before["wall_time"]),
            "after_stage_seconds": stage_after.get("elapsed", after["wall_time"]),
            "same_stage_routes": same_routes(stage_before, stage_after),
            "same_stage_objective": stage_before["objective"] == stage_after["objective"],
            "same_final_routes": same_routes(before, after),
            "route_cost_delta": after["route_cost_delta"], "feasible": after["feasible"],
            "inherited_improvement_pct": None if model != 6 else
                100 * (after["initial_objective"] - after["objective"]) / after["initial_objective"],
        })
    summary = []
    for model in (3, 6):
        for mode in ("fixed", "time"):
            rows = [row for row in pairs if row["model"] == model and row["mode"] == mode]
            means = {name: statistics.mean(row[name] for row in rows) for name in (
                "before_wall", "after_wall", "before_iterations", "after_iterations",
                "before_stage_seconds", "after_stage_seconds", "improvement_pct")}
            summary.append({"model": model, "mode": mode, "pairs": len(rows), **means,
                "wall_speedup": means["before_wall"] / means["after_wall"],
                "stage_speedup": means["before_stage_seconds"] / means["after_stage_seconds"],
                "iteration_ratio": means["after_iterations"] / means["before_iterations"],
                "better": sum(row["improvement_pct"] > 1e-7 for row in rows),
                "worse": sum(row["improvement_pct"] < -1e-7 for row in rows),
                "same_stage_routes": sum(row["same_stage_routes"] for row in rows),
                "same_final_routes": sum(row["same_final_routes"] for row in rows)})
    result = {"sources": {name: str(value) for name, value in vars(args).items() if value},
        "summary": summary, "pairs": pairs,
        "all_feasible": all(row["feasible"] for row in pairs),
        "max_abs_cost_delta": max(abs(row["route_cost_delta"]) for row in pairs),
        "max_timed_wall": max(row["after_wall"] for row in pairs if row["mode"] == "time"),
        "inheritance_violations": sum(row["inherited_improvement_pct"] < -1e-7
            for row in pairs if row["model"] == 6)}
    if args.repeat6:
        repeats = read_rows(args.repeat6, 6)
        result["hash_seed_repeat"] = {"pairs": len(repeats),
            "all_final_routes_identical": all(same_routes(row, current[key(row)]) for row in repeats),
            "all_objectives_identical": all(row["objective"] == current[key(row)]["objective"] for row in repeats),
            "hash_seeds": sorted({str(row["python_hash_seed"]) for row in repeats}
                | {str(current[key(row)]["python_hash_seed"]) for row in repeats})}
    if args.output:
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: value for key, value in result.items() if key != "pairs"}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
