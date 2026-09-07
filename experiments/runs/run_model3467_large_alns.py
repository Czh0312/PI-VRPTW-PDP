"""Complete the paired large-scale M3/M4/M6/M7 ALNS comparison.

M3/M4/M7 rows are reused from the earlier paired run; M6 is evaluated with
the same instances, seeds and wall-time budget, then a compact summary is
written alongside the raw rows.
"""
from __future__ import annotations

import csv
import json
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from experiments.runs.run_model12346_alns_scaling import load_problem, route_cost, solve_one
SOURCE = ROOT / "output/experiments/model347_comparison_20260906/runs.jsonl"
OUT = ROOT / "output/experiments/model3467_large_comparison_20260906"


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    rows = [json.loads(line) for line in SOURCE.read_text(encoding="utf-8").splitlines()
            if json.loads(line).get("scale") == "large"]
    done = {(r["instance"], r["seed"], r["model"]) for r in rows}
    fleet = json.loads((ROOT / "config/routing/validation/zhou_large.json").read_text())
    data_dir = ROOT / "data/zhou_later_pickup/large/standard_same_day"
    for path in sorted(data_dir.glob("*.csv")):
        for seed in (42, 43, 44):
            if (path.stem, seed, 6) in done:
                continue
            problem = load_problem(path, fleet)
            started = time.perf_counter()
            row = {"scale": "large", "instance": path.stem, "model": 6,
                   "seed": seed, "time_budget": 3.0}
            try:
                r1, r2, assignment, info = solve_one(
                    6, problem, max_time=3.0, iterations=1_000_000, seed=seed)
                audited = route_cost(problem, r1, r2)
                objective = info.get("objective")
                delta = audited - objective if audited is not None and objective is not None else None
                feasible = bool(info.get("feasible")) and delta is not None and abs(delta) < 1e-6
                row.update(feasible=feasible, status=info.get("status"),
                           objective=objective if feasible else None,
                           wall_time=time.perf_counter() - started,
                           route_cost_delta=delta,
                           transshipment_count=info.get("transshipment_count", 0),
                           transshipment_improvement=info.get("transshipment_improvement", 0.0),
                           stage1_objective=info.get("stage1_objective"),
                           free_assignment_objective=info.get("free_assignment_objective"),
                           assignment=assignment, iterations=info.get("iterations"))
            except Exception as exc:
                row.update(feasible=False, status="ERROR", objective=None,
                           wall_time=time.perf_counter() - started,
                           route_cost_delta=None, transshipment_count=0,
                           transshipment_improvement=0.0, error=repr(exc))
            rows.append(row)
            print(f"M6 {len(rows)}/216 {path.stem} seed={seed}: {row['objective']} ({row['wall_time']:.2f}s)", flush=True)

    rows.sort(key=lambda r: (r["instance"], r["seed"], r["model"]))
    (OUT / "runs.jsonl").write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")
    fields = list(dict.fromkeys(key for row in rows for key in row))
    with (OUT / "runs.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader(); writer.writerows(rows)

    lines = ["# 大规模 M3 / M4 / M6 / M7 ALNS 对比", "",
             "标准同日回收场景，18 个实例、seed=42/43/44、每次 3 秒、迭代上限 1,000,000。四个模型使用相同实例和 seed；M6/M7 的中继统计是实际触发次数。", "",
             "| 模型 | 可行率 | 平均距离 | 平均运行时间 | 触发中继 | 平均中继改善 |", "| --- | ---: | ---: | ---: | ---: | ---: |"]
    for model in (3, 4, 6, 7):
        subset = [r for r in rows if r["model"] == model]
        valid = [r for r in subset if r.get("feasible")]
        lines.append(f"| M{model} | {len(valid)}/{len(subset)} | {statistics.mean(r['objective'] for r in valid):.4f} | {statistics.mean(r['wall_time'] for r in subset):.3f}s | {sum(r.get('transshipment_count', 0) > 0 for r in valid)} | {statistics.mean(r.get('transshipment_improvement', 0.0) for r in valid):.4f} |" if valid else f"| M{model} | 0/{len(subset)} | — | — | 0 | — |")
    lines += ["", "配对改善率按共同可行的实例与 seed 计算，正数表示后一个模型距离更短。", "", "| 比较 | 配对数 | 平均改善率 | 后者胜/平/负 |", "| --- | ---: | ---: | --- |"]
    for a, b in ((3, 4), (3, 6), (3, 7), (4, 6), (4, 7), (6, 7)):
        left = {(r["instance"], r["seed"]): r for r in rows if r["model"] == a and r.get("feasible")}
        right = {(r["instance"], r["seed"]): r for r in rows if r["model"] == b and r.get("feasible")}
        pairs = [(left[key], right[key]) for key in left.keys() & right.keys()]
        gains = [100 * (x["objective"] - y["objective"]) / x["objective"] for x, y in pairs]
        wins = sum(g > 1e-6 for g in gains); losses = sum(g < -1e-6 for g in gains)
        lines.append(f"| M{a} → M{b} | {len(pairs)} | {statistics.mean(gains) if gains else float('nan'):.3f}% | {wins}/{len(pairs)-wins-losses}/{losses} |")
    lines += ["", "解释：这些是短预算 ALNS 结果，不是全局最优性证明。M4/M7 的自由分仓和 M6/M7 的中继会分走搜索时间，因此最终距离同时反映机制和时间分配。M7 中继改善是相对其自身交接前解，不能直接当作相对 M4 的改善。", "", "最大路线距离复算误差：%.3g。" % max((abs(r["route_cost_delta"]) for r in rows if r.get("route_cost_delta") is not None), default=0)]
    (OUT / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
