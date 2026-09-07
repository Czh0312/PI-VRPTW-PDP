"""Summarize the 10-second large-scale M3/M4/M6/M7 ALNS batch."""
from __future__ import annotations

import csv
import json
import statistics
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "output/experiments/model3467_large_alns_10s"


def main():
    rows = []
    for path in sorted(OUT.glob("large_standard_same_day_t10_seed*.jsonl")):
        rows.extend(json.loads(line) for line in path.read_text(encoding="utf-8").splitlines())
    rows = [r for r in rows if r.get("model") in (3, 4, 6, 7)]
    lines = ["# 大规模 M3 / M4 / M6 / M7 ALNS 对比（10 秒）", "",
             "标准同日回收场景，18 个实例、seed=42/43/44、每例 10 秒、迭代上限 1,000,000。", "",
             "| 模型 | 可行率 | 平均距离 | 平均运行时间 | 触发中继 | 平均中继改善 |", "| --- | ---: | ---: | ---: | ---: | ---: |"]
    for model in (3, 4, 6, 7):
        subset = [r for r in rows if r["model"] == model]
        valid = [r for r in subset if r.get("feasible")]
        if valid:
            lines.append(f"| M{model} | {len(valid)}/{len(subset)} | {statistics.mean(r['objective'] for r in valid):.4f} | {statistics.mean(r['wall_time'] for r in subset):.3f}s | {sum(r.get('transshipment_count', 0) > 0 for r in valid)} | {statistics.mean(r.get('transshipment_improvement', 0.0) for r in valid):.4f} |")
        else:
            lines.append(f"| M{model} | 0/{len(subset)} | — | — | 0 | — |")

    lines += ["", "配对改善率按共同可行的实例与 seed 计算，正数表示后一个模型距离更短。", "", "| 比较 | 配对数 | 平均改善率 | 后者胜/平/负 |", "| --- | ---: | ---: | --- |"]
    for a, b in ((3, 4), (3, 6), (3, 7), (4, 6), (4, 7), (6, 7)):
        left = {(r["instance"], r["seed"]): r for r in rows if r["model"] == a and r.get("feasible")}
        right = {(r["instance"], r["seed"]): r for r in rows if r["model"] == b and r.get("feasible")}
        pairs = [(left[key], right[key]) for key in left.keys() & right.keys()]
        gains = [100 * (x["objective"] - y["objective"]) / x["objective"] for x, y in pairs]
        wins = sum(g > 1e-6 for g in gains)
        losses = sum(g < -1e-6 for g in gains)
        lines.append(f"| M{a} → M{b} | {len(pairs)} | {statistics.mean(gains) if gains else float('nan'):.3f}% | {wins}/{len(pairs)-wins-losses}/{losses} |")

    lines += ["", "这些是限时 ALNS 结果，不是全局最优性证明。M6/M7 的中继改善分别相对各自的交接前基线计算，不能直接解释为相对 M4 的改善。", "", "最大路线距离复算绝对误差：%.3g。" % max((abs(r["route_cost_delta"]) for r in rows if r.get("route_cost_delta") is not None), default=0)]
    (OUT / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    fields = list(dict.fromkeys(k for row in rows for k in row))
    with (OUT / "runs_merged.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader(); writer.writerows(rows)


if __name__ == "__main__":
    main()
