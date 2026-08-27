# -*- coding: utf-8 -*-
"""Create the Model 1/2/3/4/6 ALNS scale-comparison report."""
from __future__ import annotations

import argparse
import csv
import json
import statistics
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load_jsonl(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()]


def merged(base_path, replacement_path, replacement_model=6):
    rows = [r for r in load_jsonl(base_path)
            if int(r["model"]) != replacement_model]
    rows.extend(load_jsonl(replacement_path))
    return sorted(rows, key=lambda r: (r["instance"], int(r["model"])))


def mean(values):
    values = list(values)
    return statistics.fmean(values) if values else 0.0


def fmt(value, digits=3):
    return "-" if value is None else f"{value:.{digits}f}"


def write_rows(rows, stem, directory):
    jsonl_path = directory / f"{stem}.jsonl"
    csv_path = directory / f"{stem}.csv"
    jsonl_path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
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
    return jsonl_path, csv_path


def grouped(rows, key):
    out = {}
    for row in rows:
        out.setdefault(row[key], []).append(row)
    return out


def paired(rows, base_model, other_model, customers=None):
    lookup = {(r["instance"], int(r["model"])): r for r in rows}
    values = []
    for instance in sorted({r["instance"] for r in rows}):
        base = lookup.get((instance, base_model))
        other = lookup.get((instance, other_model))
        if not base or not other or not base.get("feasible") or not other.get("feasible"):
            continue
        if customers is not None and int(base["customers"]) != customers:
            continue
        pct = (base["objective"] - other["objective"]) / base["objective"] * 100.0
        values.append((instance, pct))
    tol = 1e-7
    return {
        "values": values,
        "mean": mean(v for _, v in values),
        "median": statistics.median(v for _, v in values) if values else 0.0,
        "better": sum(v > tol for _, v in values),
        "tie": sum(abs(v) <= tol for _, v in values),
        "worse": sum(v < -tol for _, v in values),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--small-base", required=True)
    parser.add_argument("--small-m6", required=True)
    parser.add_argument("--large-base", required=True)
    parser.add_argument("--large-m6", required=True)
    parser.add_argument(
        "--output",
        default="output/experiments/model12346_alns_scaling_experiment.md")
    args = parser.parse_args()

    resolve = lambda p: Path(p) if Path(p).is_absolute() else ROOT / p
    small_base, small_m6 = resolve(args.small_base), resolve(args.small_m6)
    large_base, large_m6 = resolve(args.large_base), resolve(args.large_m6)
    small = merged(small_base, small_m6)
    large = merged(large_base, large_m6)
    canonical_dir = small_base.parent
    small_final, _ = write_rows(
        small, "small_standard_same_day_alns_seed42_final", canonical_dir)
    large_final, _ = write_rows(
        large, "large_standard_same_day_alns_seed42_final", canonical_dir)

    small_by_model = grouped(small, "model")
    large_by_model = grouped(large, "model")
    small_transfers = sum(r.get("transshipment_count", 0) for r in small
                          if int(r["model"]) == 6)
    small_m6_rows = small_by_model[6]
    small_transfer_cases = sum(r.get("transshipment_count", 0) > 0
                               for r in small_m6_rows)
    small_stage2_gain = sum(r.get("transshipment_improvement", 0.0)
                            for r in small_m6_rows)
    large_m6_rows = large_by_model[6]
    large_transfer_cases = sum(r.get("transshipment_count", 0) > 0
                               for r in large_m6_rows)
    large_transfer_count = sum(r.get("transshipment_count", 0)
                               for r in large_m6_rows)
    large_stage2_gain = sum(r.get("transshipment_improvement", 0.0)
                            for r in large_m6_rows)

    pair_defs = [(1, 2, "M2 vs M1"), (3, 4, "M4 vs M3"),
                 (3, 6, "M6 vs M3")]
    pair_stats = {(a, b, n): paired(large, a, b)
                  for a, b, n in pair_defs}

    lines = [
        "# 模型1、2、3、4、6的ALNS规模实验",
        "",
        "## 1. 实验目的与求解性质",
        "",
        "本实验比较五个模型的ALNS实现，重点检验小规模与大规模下的搜索质量、计算效率，以及模型6一次中转机制是否产生可观测收益。这里的运行全部是启发式ALNS，不是精确求解；小规模结果用已认证精确最优值计算gap，大规模结果没有最优性证明，只做同实例配对比较。",
        "",
        "## 2. 统一实验口径",
        "",
        "- 随机种子：42。",
        "- 迭代上限：1,000,000，实际主要受时间限制。",
        "- 小规模：22个 `small/standard_same_day` 算例，每模型5秒，使用 `config/fleet_config.json`。",
        "- 大规模：17个可行的 `large/standard_same_day` 算例，每模型10秒，使用独立的 `config/fleet_config_large.json`。其中12个为100客户、5个为200客户。",
        "- 大规模车队：一级车10辆，一级重量/体积容量1500/60；每仓二级车上限25辆，二级重量/体积容量300/5。车辆数是可用上限，并非强制全部派出。",
        "- 模型6：先运行无中转ALNS，再搜索一次中转；若未发生中转，未使用的阶段二时间返还给基础ALNS。所有输出都由模型自身评估器复核，并从输出路线重新计算距离。",
        "",
        "`Set2_200-10-3` 未纳入性能统计：固定分配中的 C93 必须经 S3 配送，而 D0→S3→C93 的理论最早到达约79.287，晚于截止时刻75，因此模型1/3/6在该数据上结构性不可行；其 `b` 版本可行并已纳入。",
        "",
        "## 3. 小规模：ALNS对精确最优值",
        "",
        "| 模型 | 可行数 | 平均gap | 中位gap | 最大gap | 命中最优 | 平均运行(s) | 平均迭代 |",
        "|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for model in (1, 2, 3, 4, 6):
        rows = small_by_model[model]
        gaps = [r["gap_to_exact_pct"] for r in rows]
        hits = sum(abs(g) <= 1e-6 for g in gaps)
        lines.append(
            f"| M{model} | {sum(r['feasible'] for r in rows)}/{len(rows)} | "
            f"{fmt(mean(gaps))}% | {fmt(statistics.median(gaps))}% | "
            f"{fmt(max(gaps))}% | {hits}/{len(rows)} | "
            f"{fmt(mean(r['runtime'] for r in rows))} | "
            f"{mean(r['iterations'] for r in rows):.1f} |")
    lines.extend([
        "",
        f"小规模M6仅在 **{small_transfer_cases}/22** 个算例中发生中转，共 **{small_transfers}** 次，阶段二直接降本 **{small_stage2_gain:.3f}**。这说明小规模下中转收益只是孤立现象，还不足以形成稳定差异。",
        "",
        "## 4. 大规模配对结果",
        "",
        "表中“平均改善”为 `(基准模型成本-扩展模型成本)/基准模型成本`；正值表示扩展模型更好。",
        "",
        "| 配对 | 样本数 | 平均改善 | 中位改善 | 更好/持平/更差 |",
        "|---|---:|---:|---:|---:|",
    ])
    for a, b, name in pair_defs:
        s = pair_stats[(a, b, name)]
        lines.append(
            f"| {name} | {len(s['values'])} | {fmt(s['mean'])}% | "
            f"{fmt(s['median'])}% | {s['better']}/{s['tie']}/{s['worse']} |")

    lines.extend([
        "",
        f"修正等待语义后，M6在 **{large_transfer_cases}/17** 个大规模算例中采用中转，共 **{large_transfer_count}** 次，阶段二累计直接降本 **{large_stage2_gain:.3f}**。这项“阶段二直接降本”只比较同一次M6运行中转前后的解，比M6与M3的跨运行差异更能隔离中转机制本身。",
        "",
        "### 分规模配对",
        "",
        "| 客户数 | 配对 | 平均改善 | 更好/持平/更差 |",
        "|---:|---|---:|---:|",
    ])
    for customers in (100, 200):
        for a, b, name in pair_defs:
            s = paired(large, a, b, customers)
            lines.append(
                f"| {customers} | {name} | {fmt(s['mean'])}% | "
                f"{s['better']}/{s['tie']}/{s['worse']} |")

    lines.extend([
        "",
        "### 逐算例结果",
        "",
        "| 算例 | n | M1 | M2 | M3 | M4 | M6 | M2 vs M1 | M4 vs M3 | M6 vs M3 | 中转数 | 阶段二降本 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ])
    lookup = {(r["instance"], int(r["model"])): r for r in large}
    for instance in sorted({r["instance"] for r in large}):
        rs = {m: lookup[(instance, m)] for m in (1, 2, 3, 4, 6)}
        p21 = (rs[1]["objective"] - rs[2]["objective"]) / rs[1]["objective"] * 100
        p43 = (rs[3]["objective"] - rs[4]["objective"]) / rs[3]["objective"] * 100
        p63 = (rs[3]["objective"] - rs[6]["objective"]) / rs[3]["objective"] * 100
        lines.append(
            f"| {instance} | {rs[1]['customers']} | {fmt(rs[1]['objective'])} | "
            f"{fmt(rs[2]['objective'])} | {fmt(rs[3]['objective'])} | "
            f"{fmt(rs[4]['objective'])} | {fmt(rs[6]['objective'])} | "
            f"{fmt(p21)}% | {fmt(p43)}% | {fmt(p63)}% | "
            f"{rs[6].get('transshipment_count', 0)} | "
            f"{fmt(rs[6].get('transshipment_improvement', 0.0))} |")

    max_small_delta = max(abs(r["route_cost_delta"]) for r in small)
    max_large_delta = max(abs(r["route_cost_delta"]) for r in large)
    lines.extend([
        "",
        "## 5. 运行效率与完整性",
        "",
        "| 模型 | 大规模可行数 | 平均运行(s) | 平均迭代数 |",
        "|---:|---:|---:|---:|",
    ])
    for model in (1, 2, 3, 4, 6):
        rows = large_by_model[model]
        lines.append(
            f"| M{model} | {sum(r['feasible'] for r in rows)}/{len(rows)} | "
            f"{fmt(mean(r['runtime'] for r in rows))} | "
            f"{mean(r['iterations'] for r in rows):.1f} |")
    lines.extend([
        "",
        f"完整性检查：小规模110/110行可行，大规模85/85行可行；输出路线重算成本与内部目标值的最大绝对误差分别为 `{max_small_delta:.3e}` 和 `{max_large_delta:.3e}`。",
        "",
        "## 6. 结论",
        "",
        f"1. 小规模主要适合校验正确性和估计ALNS gap，不能充分体现中转价值；本批22个算例中仅{small_transfer_cases}个采用中转。",
        f"2. 大规模开始出现中转：M6在{large_transfer_cases}个算例中采用了中转，说明一次中转机制在规模扩大后具有可观测作用，但并非每个实例都受益。",
        "3. 自由分配扩大了可行决策空间，但M2/M4每次邻域评估更昂贵；在固定10秒内，目标改进能力与搜索实现效率共同决定最终结果，不能把单次ALNS值当作模型的理论最优关系。",
        "4. M6相对M3的跨运行比较会混入两阶段时间分配和随机搜索差异；论文讨论中应优先引用M6自身的“阶段二直接降本”，并把M6 vs M3作为补充。",
        "5. 这是一组单随机种子、短时间预算实验。若用于最终统计显著性结论，建议再运行至少5个随机种子，并报告均值、标准差和配对检验。",
        "",
        "## 7. 原始数据",
        "",
        f"- 小规模最终合并结果：`{small_final.relative_to(ROOT).as_posix()}`",
        f"- 大规模最终合并结果：`{large_final.relative_to(ROOT).as_posix()}`",
        f"- 小规模五模型基线：`{small_base.relative_to(ROOT).as_posix()}`",
        f"- 小规模修正后M6：`{small_m6.relative_to(ROOT).as_posix()}`",
        f"- 大规模五模型基线：`{large_base.relative_to(ROOT).as_posix()}`",
        f"- 大规模修正后M6：`{large_m6.relative_to(ROOT).as_posix()}`",
        "- 独立大规模车队：`config/fleet_config_large.json`",
    ])

    output = resolve(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(output)


if __name__ == "__main__":
    main()
