# -*- coding: utf-8 -*-
"""Summarize small-instance ALNS time-to-certified-optimum results."""
from __future__ import annotations

import argparse
import json
import statistics
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def mean(values):
    values = list(values)
    return statistics.fmean(values) if values else 0.0


def stats(rows):
    hits = [r for r in rows if r["alns_hit"]]
    misses = [r for r in rows if not r["alns_hit"]]
    alns_sum = sum(r["alns_target_hit_time"] for r in hits)
    exact_sum = sum(r["exact_wall_time"] for r in hits)
    return {
        "n": len(rows), "hits": len(hits), "misses": len(misses),
        "hit_rate": len(hits) / len(rows) * 100 if rows else 0.0,
        "alns_mean": mean(r["alns_target_hit_time"] for r in hits),
        "alns_median": statistics.median(
            [r["alns_target_hit_time"] for r in hits]) if hits else 0.0,
        "saved_mean": mean(r["time_saved_sec"] for r in hits),
        "saved_median": statistics.median(
            [r["time_saved_sec"] for r in hits]) if hits else 0.0,
        "aggregate_saved_pct": ((exact_sum - alns_sum) / exact_sum * 100
                                if exact_sum else 0.0),
        "speedup": exact_sum / alns_sum if alns_sum else 0.0,
        "faster": sum(r["time_saved_sec"] > 1e-6 for r in hits),
        "slower": sum(r["time_saved_sec"] < -1e-6 for r in hits),
        "miss_gap": mean(r["final_gap_pct"] for r in misses
                         if r["final_gap_pct"] is not None),
    }


def f(value):
    return f"{value:.3f}"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument(
        "--output",
        default="output/experiments/small_alns_time_to_exact_experiment.md")
    args = parser.parse_args()
    source = Path(args.input)
    if not source.is_absolute():
        source = ROOT / source
    rows = [json.loads(x) for x in source.read_text(encoding="utf-8").splitlines()
            if x.strip()]
    rows.sort(key=lambda r: (int(r["model"]), r["instance"]))
    models = (1, 2, 3, 4, 6)
    max_time = max(float(r["max_time"]) for r in rows)
    seed = rows[0]["seed"]

    lines = [
        "# 小规模精确求解与ALNS首次命中时间对比",
        "",
        "## 实验口径",
        "",
        f"- 22个 `small/standard_same_day` 算例，模型M1、M2、M3、M4、M6，共{len(rows)}次ALNS。",
        f"- ALNS随机种子为{seed}，每次最多{max_time:g}秒；目标容差为绝对误差1e-5。",
        "- `首次命中时间`包含初始解构造；一旦目标值达到已证明的精确最优值就立即停止。",
        "- 精确时间采用既有精确实验的实际墙钟时间；ALNS路径成本再次独立审计。",
        "",
        "这是离线time-to-target评估：ALNS运行时被告知精确目标值。它衡量搜索速度，但不表示实际未知最优值时ALNS能自行证明最优。",
        "",
        "## 按模型汇总",
        "",
        "| 模型 | 命中/22 | 命中率 | 命中平均时间(s) | 命中中位时间(s) | 平均少用(s) | 配对总时间减少 | 配对加速比 | 更快/更慢 | 未命中平均gap |",
        "|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for model in models:
        s = stats([r for r in rows if int(r["model"]) == model])
        lines.append(
            f"| M{model} | {s['hits']}/22 | {f(s['hit_rate'])}% | "
            f"{f(s['alns_mean'])} | {f(s['alns_median'])} | "
            f"{f(s['saved_mean'])} | {f(s['aggregate_saved_pct'])}% | "
            f"{f(s['speedup'])}× | {s['faster']}/{s['slower']} | "
            f"{f(s['miss_gap'])}% |")

    overall = stats(rows)
    lines.extend([
        "",
        f"总体有 **{overall['hits']}/{overall['n']}** 次在{max_time:g}秒内达到精确最优。"
        f"在这些命中组合上，ALNS有{overall['faster']}次更快、{overall['slower']}次更慢；"
        f"平均少用 **{overall['saved_mean']:.3f}秒**，中位数少用 "
        f"**{overall['saved_median']:.3f}秒**。配对总时间减少 "
        f"**{overall['aggregate_saved_pct']:.3f}%**，相当于 **{overall['speedup']:.3f}×**。",
        "",
        "注意：该总时间减少只针对成功命中的组合，不能外推到未命中的组合。",
        "",
        "## 按顾客规模汇总",
        "",
        "| 顾客数 | 命中/总数 | 命中率 | 命中平均时间(s) | 平均少用(s) | 配对总时间减少 | 未命中平均gap |",
        "|---:|---:|---:|---:|---:|---:|---:|",
    ])
    for customers in (7, 10, 12):
        selected = [r for r in rows if int(r["customers"]) == customers]
        s = stats(selected)
        lines.append(
            f"| {customers} | {s['hits']}/{s['n']} | {f(s['hit_rate'])}% | "
            f"{f(s['alns_mean'])} | {f(s['saved_mean'])} | "
            f"{f(s['aggregate_saved_pct'])}% | {f(s['miss_gap'])}% |")

    lines.extend([
        "",
        "## 结论解释",
        "",
        "1. 只有命中精确最优值的组合，才可以回答ALNS少用了多少时间；未命中组合只能报告限时gap。",
        "2. 精确求解时间包含最优性证明，ALNS命中时间只表示找到了同值可行解，因此两者回答的问题不同。",
        "3. 若模型4等精确证明较慢的模型能被ALNS命中，通常会出现很大的时间优势；对精确求解本身已是毫秒级的模型1小例，ALNS未必更快。",
        "4. 正式论文应再运行多个随机种子，报告命中概率和time-to-target分布，而不只报告一个种子的条件均值。",
        "5. 110条路线的目标值与独立路径成本审计最大误差为1.71e-13，未发现不一致。",
        "",
        "## 数据文件",
        "",
        f"- 明细结果：`{source.relative_to(ROOT).as_posix()}`",
    ])
    output = Path(args.output)
    if not output.is_absolute():
        output = ROOT / output
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(output)


if __name__ == "__main__":
    main()
