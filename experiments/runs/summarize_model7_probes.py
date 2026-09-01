"""Compare M7 proof and ALNS probes, checking replay identity and route audits."""
import argparse
import json
from pathlib import Path
import statistics


def read(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("alns-before", "alns-after", "proof-before", "proof-after", "output"):
        parser.add_argument("--" + name, required=True, type=Path)
    parser.add_argument("--proof-extended", type=Path)
    args = parser.parse_args()
    before = {(r["instance"], r["seed"], r["mode"]): r for r in read(args.alns_before)}
    after = {(r["instance"], r["seed"], r["mode"]): r for r in read(args.alns_after)}
    assert before.keys() == after.keys(), "Unpaired ALNS results"
    pairs = []
    for identity, a in after.items():
        b = before[identity]
        assert b["replay_sha256"] == a["replay_sha256"], "Different inherited initial solution"
        bs, ast = b["stages"][0], a["stages"][0]
        pairs.append({"instance": a["instance"], "seed": a["seed"], "mode": a["mode"],
            "before_objective": b["objective"], "after_objective": a["objective"],
            "before_wall": b["wall_time"], "after_wall": a["wall_time"],
            "before_iterations": b["iterations"], "after_iterations": a["iterations"],
            "before_stage_wall": bs["elapsed"], "after_stage_wall": ast["elapsed"],
            "stage_routes_equal": all(bs[k] == ast[k] for k in ("e1_routes", "e2_routes")),
            "stage_objective_equal": bs["objective"] == ast["objective"],
            "final_routes_equal": all(b[k] == a[k] for k in ("routes_e1", "routes_e2")),
            "gain_pct": 100 * (b["objective"] - a["objective"]) / b["objective"],
            "feasible": a["feasible"], "route_cost_delta": a["route_cost_delta"],
            "inheritance_valid": a["objective"] <= a["initial_objective"] + 1e-6})
    summary = []
    for mode in ("fixed", "time"):
        rows = [r for r in pairs if r["mode"] == mode]
        means = {name: statistics.mean(r[name] for r in rows) for name in (
            "before_wall", "after_wall", "before_stage_wall", "after_stage_wall",
            "before_iterations", "after_iterations", "gain_pct")}
        summary.append({"mode": mode, "pairs": len(rows), **means,
            "wall_speedup": means["before_wall"] / means["after_wall"],
            "base_search_speedup": means["before_stage_wall"] / means["after_stage_wall"],
            "iteration_ratio": means["after_iterations"] / means["before_iterations"],
            "median_gain_pct": statistics.median(r["gain_pct"] for r in rows),
            "min_gain_pct": min(r["gain_pct"] for r in rows),
            "max_gain_pct": max(r["gain_pct"] for r in rows),
            "same_stage_routes": sum(r["stage_routes_equal"] for r in rows),
            "same_stage_objectives": sum(r["stage_objective_equal"] for r in rows),
            "same_final_routes": sum(r["final_routes_equal"] for r in rows),
            "better": sum(r["gain_pct"] > 1e-7 for r in rows),
            "worse": sum(r["gain_pct"] < -1e-7 for r in rows)})
    proof_before = {r["instance"]: r for r in read(args.proof_before)}
    proof_after = {r["instance"]: r for r in read(args.proof_after)}
    assert proof_before.keys() == proof_after.keys()
    proof = []
    for instance, a in proof_after.items():
        b = proof_before[instance]
        assert a["replay_sha256"] == b["replay_sha256"]
        proof.append({"instance": instance, **{f"{side}_{name}": row.get(name)
            for side, row in (("before", b), ("after", a)) for name in (
                "objective", "best_bound", "mip_gap", "wall_time", "status",
                "assignment_partitions_evaluated", "assignment_partitions_resolved",
                "partition_relaxation_pruned")}})
    result = {"sources": {name: str(value) for name, value in vars(args).items()},
        "alns_summary": summary, "alns_pairs": pairs, "proof_pairs": proof,
        "all_feasible": all(r["feasible"] for r in pairs),
        "all_inheritance_valid": all(r["inheritance_valid"] for r in pairs),
        "max_abs_cost_delta": max(abs(r["route_cost_delta"]) for r in pairs),
        "max_timed_wall": max(r["after_wall"] for r in pairs if r["mode"] == "time")}
    if args.proof_extended:
        extended = read(args.proof_extended)
        assert all(r["replay_sha256"] == proof_before[r["instance"]]["replay_sha256"] for r in extended)
        result["proof_extended"] = [{k: r.get(k) for k in (
            "instance", "time_budget", "status", "objective", "best_bound", "mip_gap",
            "wall_time", "assignment_partitions_evaluated", "assignment_partitions_resolved",
            "assignment_partitions_unresolved", "partition_relaxation_screened",
            "partition_relaxation_pruned", "assignment_partition_retries",
            "weakest_unresolved_assignment")} for r in extended]
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in result.items() if k != "alns_pairs"}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
