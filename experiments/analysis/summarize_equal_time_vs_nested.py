# -*- coding: utf-8 -*-
"""Compare independent equal-time ALNS with nested incremental ALNS."""
from __future__ import annotations

import argparse
import csv
import json
import statistics
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def load(path):
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines()
            if x.strip()]


def avg(values):
    values = list(values)
    return statistics.fmean(values) if values else 0.0


def f(value):
    return f"{value:.3f}"


def write_rows(rows, directory):
    stem = "nested_large_base10_ext2_seed42_final"
    jsonl = directory / f"{stem}.jsonl"
    csv_path = directory / f"{stem}.csv"
    jsonl.write_text("".join(
        json.dumps(r, ensure_ascii=False) + "\n" for r in rows),
        encoding="utf-8")
    fields = []
    for row in rows:
        for key in row:
            if key not in fields:
                fields.append(key)
    with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    return jsonl


def normalize_m6_inherited_objective(row):
    """Use the objective actually recomputed from the inherited M3 routes.

    The standalone M3 info objective is retained for audit.  M6's
    ``transshipment_improvement`` is measured from its own stage-1 cost, so it
    gives the exact inherited incumbent under the extension evaluator.
    """
    row = dict(row)
    row["base_solver_objective"] = float(row["base_objective"])
    inherited = float(row["objective"]) + float(
        row.get("transshipment_improvement", 0.0))
    row["base_objective"] = inherited
    gain = inherited - float(row["objective"])
    row["incremental_gain"] = gain
    row["incremental_gain_pct"] = gain / inherited * 100.0
    row["dominance_preserved"] = gain >= -1e-7
    return row


def nested_stats(rows, model, customers=None):
    selected = [r for r in rows if int(r["model"]) == model
                and (customers is None or int(r["customers"]) == customers)]
    tol = 1e-7
    gains = [r["incremental_gain_pct"] for r in selected]
    return {
        "n": len(selected), "mean": avg(gains),
        "median": statistics.median(gains), "max": max(gains),
        "positive": sum(g > tol for g in gains),
        "tie": sum(abs(g) <= tol for g in gains),
        "dominance": sum(r["dominance_preserved"] for r in selected),
        "runtime": avg(r["extension_runtime"] for r in selected),
        "max_runtime": max(r["extension_runtime"] for r in selected),
    }


def equal_stats(rows, base, model):
    lookup = {(r["instance"], int(r["model"])): r for r in rows}
    values = []
    for instance in sorted({r["instance"] for r in rows}):
        b, m = lookup[(instance, base)], lookup[(instance, model)]
        values.append((b["objective"] - m["objective"]) / b["objective"] * 100)
    tol = 1e-7
    return {
        "mean": avg(values), "median": statistics.median(values),
        "better": sum(v > tol for v in values),
        "tie": sum(abs(v) <= tol for v in values),
        "worse": sum(v < -tol for v in values),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--equal-time", required=True)
    parser.add_argument("--nested-base", required=True)
    parser.add_argument("--nested-m6", required=True)
    parser.add_argument(
        "--output",
        default="output/experiments/alns_equal_time_vs_nested_experiment.md")
    args = parser.parse_args()
    resolve = lambda x: Path(x) if Path(x).is_absolute() else ROOT / x
    equal_path = resolve(args.equal_time)
    nested_base_path = resolve(args.nested_base)
    nested_m6_path = resolve(args.nested_m6)
    equal = load(equal_path)
    nested = [r for r in load(nested_base_path) if int(r["model"]) != 6]
    nested.extend(normalize_m6_inherited_objective(r)
                  for r in load(nested_m6_path))
    nested.sort(key=lambda r: (r["instance"], int(r["model"])))
    final_path = write_rows(nested, nested_base_path.parent)

    pairs = ((2, 1, "M2←M1"), (4, 3, "M4←M3"), (6, 3, "M6←M3"))
    lines = [
        "# ALNS等时间实验与嵌套增量实验对比",
        "",
        "## 1. 两种实验回答不同问题",
        "",
        "- 等时间实验：M1、M2、M3、M4、M6分别独立运行10秒，回答当前各求解器在相同墙钟时间内能得到什么解。",
        "- 嵌套增量实验：M1/M3先运行10秒；M2继承M1最终解，M4/M6继承M3最终解，再运行2秒扩展阶段，回答新增分配或中转机制在已有好解上还能产生多少收益。",
        "",
        "因此，等时间实验适合比较算法性能；嵌套实验适合验证模型扩展的结构价值。嵌套扩展总时间约12秒，不能和独立10秒结果直接作纯速度比较。",
        "",
        "## 2. 等时间实验结果",
        "",
        "| 配对 | 平均改善 | 中位改善 | 更好/持平/更差 |",
        "|---|---:|---:|---:|",
    ]
    for model, base, name in pairs:
        s = equal_stats(equal, base, model)
        lines.append(
            f"| {name.replace('←', ' vs ')} | {f(s['mean'])}% | "
            f"{f(s['median'])}% | {s['better']}/{s['tie']}/{s['worse']} |")

    lines.extend([
        "",
        "等时间结果中M2有1个实例差于M1，M6有10个实例差于M3。这不违反理论包含关系，因为各模型独立搜索，扩展模型没有保存基础模型最终 incumbent。",
        "",
        "## 3. 嵌套增量实验结果",
        "",
        "| 扩展 | 样本数 | 平均增量改善 | 中位改善 | 最大改善 | 改善/持平 | 支配保持 | 平均增量时间(s) | 最大增量时间(s) |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ])
    for model, base, name in pairs:
        s = nested_stats(nested, model)
        lines.append(
            f"| {name} | {s['n']} | {f(s['mean'])}% | {f(s['median'])}% | "
            f"{f(s['max'])}% | {s['positive']}/{s['tie']} | "
            f"{s['dominance']}/{s['n']} | {f(s['runtime'])} | "
            f"{f(s['max_runtime'])} |")

    lines.extend([
        "",
        "增量时间在ALNS迭代边界检查；M4最慢的一次原子邻域评估使2秒预算实际达到2.559秒。"
        "这不影响支配性结论，但正式计时表应同时报告实际墙钟时间，并在耗时算子内部传递deadline以收紧硬上限。",
    ])

    lines.extend([
        "",
        "### 分规模结果",
        "",
        "| 客户数 | 扩展 | 平均增量改善 | 改善/持平 |",
        "|---:|---|---:|---:|",
    ])
    for customers in (100, 200):
        for model, base, name in pairs:
            s = nested_stats(nested, model, customers)
            lines.append(
                f"| {customers} | {name} | {f(s['mean'])}% | "
                f"{s['positive']}/{s['tie']} |")

    m6 = [r for r in nested if int(r["model"]) == 6]
    lines.extend([
        "",
        f"M6嵌套阶段在 **{sum(r['transshipment_count'] > 0 for r in m6)}/17** 个实例中使用中转，共 **{sum(r['transshipment_count'] for r in m6)}** 次，累计直接降本 **{sum(r['transshipment_improvement'] for r in m6):.3f}**。",
        "",
        "### 逐算例增量改善",
        "",
        "| 算例 | n | M2←M1 | M4←M3 | M6←M3 | M6中转数 |",
        "|---|---:|---:|---:|---:|---:|",
    ])
    lookup = {(r["instance"], int(r["model"])): r for r in nested}
    for instance in sorted({r["instance"] for r in nested}):
        r2, r4, r6 = (lookup[(instance, m)] for m in (2, 4, 6))
        lines.append(
            f"| {instance} | {r2['customers']} | "
            f"{f(r2['incremental_gain_pct'])}% | "
            f"{f(r4['incremental_gain_pct'])}% | "
            f"{f(r6['incremental_gain_pct'])}% | "
            f"{r6['transshipment_count']} |")

    lines.extend([
        "",
        "## 4. 对停止策略的结论",
        "",
        "单独使用时间上限仍然是跨模型公平比较的主要标准，因为不同模型一次迭代成本差异很大。但ALNS内部不应只做被动计时停止，建议采用：",
        "",
        "1. 墙钟时间作为主要上限；最大迭代/评估次数作为保险上限，并让耗时算子检查deadline。",
        "2. 一段墙钟时间无改进时执行升温、算子权重重置或多启动，而不是立即结束。",
        "3. 小规模达到已知精确最优值时提前停止。",
        "4. M6在M3搜索停滞后切换到中转，而不是固定机械地分割时间；结构收益实验则直接使用M3 incumbent。",
        "5. 最终论文采用多随机种子，报告均值、标准差、最好值以及配对统计。",
        "",
        "建议今后同时保留两张主表：等时间表用于算法效率，嵌套表用于模型机制收益。不要用其中一张替代另一张。",
        "",
        "## 5. 数据文件",
        "",
        f"- 等时间最终结果：`{equal_path.relative_to(ROOT).as_posix()}`",
        f"- 嵌套最终结果：`{final_path.relative_to(ROOT).as_posix()}`",
        f"- 嵌套M2/M4原始批次：`{nested_base_path.relative_to(ROOT).as_posix()}`",
        f"- 修正硬时间预算后的M6批次：`{nested_m6_path.relative_to(ROOT).as_posix()}`",
    ])
    output = resolve(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(output)


if __name__ == "__main__":
    main()
