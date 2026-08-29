# -*- coding: utf-8 -*-
"""Generate tidy all-model exact and ALNS comparison reports."""
from __future__ import annotations

import json
import csv
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[2]
EXACT_INPUT = (
    ROOT / "output/results/mainline_small_exact/"
           "small_exact_models123467_final.jsonl")
ALNS_BASE_INPUT = (
    ROOT / "output/results/model12346_alns_scaling/"
           "large_standard_same_day_t10_seed42_20260829_171003.jsonl")
ALNS_M7_INPUT = (
    ROOT / "output/results/model7_exact_and_alns/"
           "model7_large_alns_t10_seed42_final.jsonl")
ALNS_OUTPUT_JSONL = (
    ROOT / "output/results/model12346_alns_scaling/"
           "large_standard_same_day_alns_seed42_all_models_final.jsonl")
ALNS_OUTPUT_CSV = ALNS_OUTPUT_JSONL.with_suffix(".csv")
SMALL_ALNS_INPUT = (
    ROOT / "output/results/model12346_alns_scaling/"
           "small_standard_same_day_alns_seed42_final.jsonl")
EXACT_REPORT = (
    ROOT / "output/experiments/four_model_small_exact_experiment_summary.md")
ALNS_REPORT = (
    ROOT / "output/experiments/model12346_alns_scaling_experiment.md")
MODELS = (1, 2, 3, 4, 6, 7)
REPAIRED = {
    "Set1_7-2-1", "Set1_7-2-2", "Set1_7-2-3b",
    "Set1_12-2-1b", "Set1_12-2-2",
}


def load(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()]


def mean(values: Iterable[float]) -> float:
    values = list(values)
    return statistics.fmean(values) if values else 0.0


def fmt(value: Any, digits: int = 3) -> str:
    if value is None:
        return "—"
    return f"{float(value):.{digits}f}"


def lookup(rows: list[dict[str, Any]]) -> dict[tuple[str, int], dict[str, Any]]:
    return {(r["instance"], int(r["model"])): r for r in rows}


def instance_order(rows: list[dict[str, Any]]) -> list[str]:
    meta = {}
    for row in rows:
        meta[row["instance"]] = int(row["customers"])
    return sorted(meta, key=lambda name: (meta[name], name))


def write_rows(rows: list[dict[str, Any]], jsonl_path: Path, csv_path: Path) -> None:
    jsonl_path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8")
    fields = []
    for row in rows:
        for field in row:
            if field not in fields:
                fields.append(field)
    with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def paired(rows: list[dict[str, Any]], base_model: int, other_model: int,
           proven_only: bool = False) -> dict[str, Any]:
    by_key = lookup(rows)
    values = []
    for instance in instance_order(rows):
        base = by_key.get((instance, base_model))
        other = by_key.get((instance, other_model))
        if not base or not other:
            continue
        if proven_only and not (
                base.get("proven_optimal") and other.get("proven_optimal")):
            continue
        if base.get("objective") is None or other.get("objective") is None:
            continue
        value = ((float(base["objective"]) - float(other["objective"]))
                 / float(base["objective"]) * 100.0)
        values.append((instance, value))
    tolerance = 1e-7
    return {
        "values": values,
        "mean": mean(value for _, value in values),
        "median": statistics.median(value for _, value in values) if values else 0.0,
        "better": sum(value > tolerance for _, value in values),
        "tie": sum(abs(value) <= tolerance for _, value in values),
        "worse": sum(value < -tolerance for _, value in values),
    }


def exact_report(rows: list[dict[str, Any]]) -> str:
    by_key = lookup(rows)
    instances = instance_order(rows)
    by_model: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_model[int(row["model"])].append(row)

    lines = [
        "# M1/M2/M3/M4/M6/M7 小规模精确求解对比",
        "",
        "## 1. 统一口径",
        "",
        "| 项目 | 设置 |",
        "| --- | --- |",
        "| 数据 | Zhou `small/standard_same_day`，22例：7客户6例、10客户8例、12客户8例 |",
        "| 车队 | E1为2辆；每个卫星站E2上限4辆 |",
        "| 目标 | E1与E2总行驶距离最小 |",
        "| M1–M4 | 600秒/模型—实例 |",
        "| M6 | 不设时间上限，证书优先精确入口 |",
        "| M7 | 120秒/实例，完整自由分配分区 + M6精确子问题 |",
        "",
        "不同模型的时间上限不同，运行时间只反映本批计算记录，不作严格速度排名。"
        "只有 `proven_optimal=True` 的结果才作为全局最优值。",
        "",
        "## 2. 模型与求解状态总览",
        "",
        "| 模型 | 卫星分配 | 回收/中继 | 已证明最优 | 限时可行 | 无incumbent | 平均时间/s | 中位时间/s |",
        "| ---: | --- | --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    definitions = {
        1: ("固定", "不回收"),
        2: ("自由", "不回收"),
        3: ("固定", "延迟回收"),
        4: ("自由", "延迟回收"),
        6: ("固定", "延迟回收 + 异步中继"),
        7: ("自由", "延迟回收 + 异步中继"),
    }
    for model in MODELS:
        model_rows = by_model[model]
        proven = sum(bool(r.get("proven_optimal")) for r in model_rows)
        incumbent = sum(
            not r.get("proven_optimal") and r.get("objective") is not None
            for r in model_rows)
        no_incumbent = len(model_rows) - proven - incumbent
        times = [float(r["runtime"]) for r in model_rows]
        assignment, mechanism = definitions[model]
        lines.append(
            f"| M{model} | {assignment} | {mechanism} | {proven}/{len(model_rows)} | "
            f"{incumbent} | {no_incumbent} | {mean(times):.3f} | "
            f"{statistics.median(times):.3f} |")

    pair_defs = (
        (1, 2, "自由分配（纯配送）"),
        (3, 4, "自由分配（延迟回收）"),
        (3, 6, "固定分配下增加中继"),
        (4, 7, "自由分配下增加中继"),
        (6, 7, "中继条件下增加自由分配"),
    )
    lines += [
        "",
        "## 3. 机制的精确配对比较",
        "",
        "改善率定义为 `(基准目标值-扩展目标值)/基准目标值`。本表只使用双方均已"
        "证明最优的实例，因此M7相关比较样本数为10。",
        "",
        "| 机制 | 配对 | 严格样本 | 平均改善 | 中位改善 | 更好/持平/更差 |",
        "| --- | --- | ---: | ---: | ---: | ---: |",
    ]
    for base, other, mechanism in pair_defs:
        stats = paired(rows, base, other, proven_only=True)
        lines.append(
            f"| {mechanism} | M{other} vs M{base} | {len(stats['values'])} | "
            f"{stats['mean']:.3f}% | {stats['median']:.3f}% | "
            f"{stats['better']}/{stats['tie']}/{stats['worse']} |")

    lines += [
        "",
        "## 4. 逐实例目标值",
        "",
        "M7的 `†` 表示限时incumbent，不是全局最优；`—` 表示120秒内无incumbent。",
        "",
        "| 实例 | n | 数据 | M1 | M2 | M3 | M4 | M6 | M7 | M7证明状态 |",
        "| --- | ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    for instance in instances:
        rs = {model: by_key[(instance, model)] for model in MODELS}
        m7 = rs[7]
        if m7.get("proven_optimal"):
            m7_value = fmt(m7["objective"])
            m7_status = "OPTIMAL"
        elif m7.get("objective") is not None:
            m7_value = f"{fmt(m7['objective'])}†"
            m7_status = "TIME_LIMIT/可行"
        else:
            m7_value = "—"
            m7_status = "TIME_LIMIT/无incumbent"
        quality = "repaired" if instance in REPAIRED else "clean"
        lines.append(
            f"| {instance} | {rs[1]['customers']} | {quality} | "
            f"{fmt(rs[1]['objective'])} | {fmt(rs[2]['objective'])} | "
            f"{fmt(rs[3]['objective'])} | {fmt(rs[4]['objective'])} | "
            f"{fmt(rs[6]['objective'])} | {m7_value} | {m7_status} |")

    lines += [
        "",
        "## 5. M7规模边界",
        "",
        "| 客户数 | 实例数 | 分区数/例 | 已证明最优 | 限时可行 | 无incumbent | 平均时间/s |",
        "| ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    m7_by_size: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in by_model[7]:
        m7_by_size[int(row["customers"])].append(row)
    for customers in sorted(m7_by_size):
        group = m7_by_size[customers]
        proven = sum(bool(r.get("proven_optimal")) for r in group)
        incumbent = sum(
            not r.get("proven_optimal") and r.get("objective") is not None
            for r in group)
        no_incumbent = len(group) - proven - incumbent
        partitions = int(group[0]["assignment_partitions_total"])
        lines.append(
            f"| {customers} | {len(group)} | {partitions} | {proven} | "
            f"{incumbent} | {no_incumbent} | "
            f"{mean(float(r['runtime']) for r in group):.3f} |")

    lines += [
        "",
        "## 6. 结论",
        "",
        "1. M1/M2/M3/M4/M6共110行全部证明最优；M7为10个最优、7个限时"
        "incumbent、5个限时无incumbent，证明状态已与目标值明确分开。",
        "2. M2相对M1、M4相对M3的改善反映自由分配价值；M6相对M3的22个"
        "严格样本均持平，说明这些小实例中固定分配下中继没有降低最优里程。",
        "3. 10个已闭合的M7实例均与M4持平并不劣于M6；这只适用于严格样本，"
        "不能外推到12个TIME_LIMIT实例。",
        "4. M7分区数从128增长到4096，解释了为何联合精确模型只用于小规模校验，"
        "大规模应使用对应ALNS。",
        "",
        "## 7. 机器可读结果",
        "",
        "- `output/results/mainline_small_exact/small_exact_models123467_final.jsonl`",
        "- `output/results/mainline_small_exact/small_exact_models123467_final.csv`",
        "- M7详细报告：`output/experiments/model7_exact_and_large_alns_experiment.md`",
    ]
    return "\n".join(lines) + "\n"


def alns_report(rows: list[dict[str, Any]], small_rows: list[dict[str, Any]]) -> str:
    by_key = lookup(rows)
    instances = instance_order(rows)
    by_model: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_model[int(row["model"])].append(row)

    lines = [
        "# M1/M2/M3/M4/M6/M7 大规模ALNS对比",
        "",
        "## 1. 统一口径",
        "",
        "| 项目 | 设置 |",
        "| --- | --- |",
        "| 数据 | Zhou `large/standard_same_day` 的17个公共可行实例 |",
        "| 规模 | 100客户12例、200客户5例 |",
        "| 时间 | 每模型—实例10秒 |",
        "| 随机种子 | 42 |",
        "| 迭代上限 | 1,000,000，实际由时间限制主导 |",
        "| 车队 | E1上限10辆；每仓E2上限25辆 |",
        "",
        "`Set2_200-10-3` 不进入公共配对：M1/M3/M6在固定分配下结构性不可行；"
        "M7虽可改派，但本次10秒也未找到可行解。以下均为启发式结果，不是最优性证明。",
        "",
        "## 2. 求解与机制总览",
        "",
        "| 模型 | 可行 | 平均时间/s | 平均迭代 | 采用中继的实例 | 中继总数 | 同次阶段二降本 |",
        "| ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for model in MODELS:
        model_rows = by_model[model]
        relay_cases = sum(int(r.get("transshipment_count") or 0) > 0
                          for r in model_rows)
        relay_count = sum(int(r.get("transshipment_count") or 0)
                          for r in model_rows)
        stage2 = sum(float(r.get("transshipment_improvement") or 0.0)
                     for r in model_rows)
        relay_cases_text = str(relay_cases) if model in (6, 7) else "—"
        relay_count_text = str(relay_count) if model in (6, 7) else "—"
        stage2_text = f"{stage2:.3f}" if model in (6, 7) else "—"
        lines.append(
            f"| M{model} | {sum(bool(r.get('feasible')) for r in model_rows)}/"
            f"{len(model_rows)} | {mean(float(r['runtime']) for r in model_rows):.3f} | "
            f"{mean(float(r['iterations']) for r in model_rows):.1f} | "
            f"{relay_cases_text} | {relay_count_text} | {stage2_text} |")

    pair_defs = (
        (1, 2, "自由分配（纯配送）"),
        (3, 4, "自由分配（延迟回收）"),
        (3, 6, "固定分配下增加中继"),
        (4, 7, "自由分配下增加中继"),
        (6, 7, "M7联合机制 vs M6"),
    )
    lines += [
        "",
        "## 3. 配对改善",
        "",
        "改善率定义为 `(基准目标值-扩展目标值)/基准目标值`，正值表示扩展模型"
        "本次结果更好。跨模型ALNS差异同时受搜索速度影响，不代表理论可行域支配关系。",
        "",
        "| 机制 | 配对 | 样本 | 平均改善 | 中位改善 | 更好/持平/更差 |",
        "| --- | --- | ---: | ---: | ---: | ---: |",
    ]
    pair_stats = {}
    for base, other, mechanism in pair_defs:
        stats = paired(rows, base, other)
        pair_stats[(base, other)] = dict(stats["values"])
        lines.append(
            f"| {mechanism} | M{other} vs M{base} | {len(stats['values'])} | "
            f"{stats['mean']:.3f}% | {stats['median']:.3f}% | "
            f"{stats['better']}/{stats['tie']}/{stats['worse']} |")

    lines += [
        "",
        "## 4. 逐实例目标值",
        "",
        "| 实例 | n | M1 | M2 | M3 | M4 | M6 | M7 |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for instance in instances:
        rs = {model: by_key[(instance, model)] for model in MODELS}
        lines.append(
            f"| {instance} | {rs[1]['customers']} | "
            f"{fmt(rs[1]['objective'])} | {fmt(rs[2]['objective'])} | "
            f"{fmt(rs[3]['objective'])} | {fmt(rs[4]['objective'])} | "
            f"{fmt(rs[6]['objective'])} | {fmt(rs[7]['objective'])} |")

    lines += [
        "",
        "## 5. 逐实例机制改善",
        "",
        "| 实例 | M2 vs M1 | M4 vs M3 | M6 vs M3 | M7 vs M4 | M7 vs M6 |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for instance in instances:
        values = [pair_stats[(base, other)][instance]
                  for base, other, _ in pair_defs]
        lines.append(
            f"| {instance} | " + " | ".join(f"{value:.3f}%" for value in values) + " |")

    lines += [
        "",
        "## 6. M7内部两阶段结果",
        "",
        "这里比较同一次M7运行中自由分配阶段与中继后处理阶段，可直接隔离该次运行"
        "的中继贡献。",
        "",
        "| 实例 | 第一阶段 | M7最终值 | 中继降本 | 中继数 | 改派客户 |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for instance in instances:
        row = by_key[(instance, 7)]
        lines.append(
            f"| {instance} | {fmt(row.get('stage1_objective'))} | "
            f"{fmt(row.get('objective'))} | "
            f"{fmt(row.get('transshipment_improvement'))} | "
            f"{int(row.get('transshipment_count') or 0)} | "
            f"{int(row.get('moved_customers') or 0)} |")

    # Keep the historical small ALNS benchmark, but move it to a compact
    # appendix because M7 small-scale evidence is the exact experiment above.
    small_by_model: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in small_rows:
        small_by_model[int(row["model"])].append(row)
    lines += [
        "",
        "## 7. 小规模ALNS历史校验（M1/M2/M3/M4/M6）",
        "",
        "该旧批次为5秒/例、seed=42；M7的小规模证据采用精确实验，因此不与此表"
        "混成六模型ALNS排名。",
        "",
        "| 模型 | 可行 | 平均gap | 中位gap | 最大gap | 命中精确最优 |",
        "| ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for model in (1, 2, 3, 4, 6):
        model_rows = small_by_model[model]
        gaps = [float(r["gap_to_exact_pct"]) for r in model_rows]
        lines.append(
            f"| M{model} | {sum(bool(r.get('feasible')) for r in model_rows)}/"
            f"{len(model_rows)} | {mean(gaps):.3f}% | "
            f"{statistics.median(gaps):.3f}% | {max(gaps):.3f}% | "
            f"{sum(abs(gap) <= 1e-6 for gap in gaps)}/{len(gaps)} |")

    m7_rows = by_model[7]
    lines += [
        "",
        "## 8. 结论",
        "",
        "1. 17个公共实例的六模型结果全部可行，路线重算误差保持在浮点容差内。",
        "2. M6在15例采用59次中继，同次阶段二累计降本194.141；M7在17例采用"
        f"{sum(int(r.get('transshipment_count') or 0) for r in m7_rows)}次中继，同次"
        f"阶段二累计降本{sum(float(r.get('transshipment_improvement') or 0.0) for r in m7_rows):.3f}。",
        "3. M7在固定10秒下平均仅完成约3.5次主搜索迭代，明显少于M4；因此M7相对"
        "M4的跨运行负改善主要说明当前实现需要更长预算或更强联合邻域，不否定精确模型"
        "的理论包含关系。",
        "4. 论文讨论中，自由分配价值使用M2 vs M1和M4 vs M3；中继价值优先引用"
        "M6/M7各自的同次阶段二降本；跨模型M7 vs M4/M6作为限时算法表现补充。",
        "",
        "## 9. 机器可读结果",
        "",
        "- `output/results/model12346_alns_scaling/large_standard_same_day_alns_seed42_all_models_final.jsonl`",
        "- `output/results/model12346_alns_scaling/large_standard_same_day_alns_seed42_all_models_final.csv`",
        "- M1/M2/M3/M4/M6来源：`output/results/model12346_alns_scaling/large_standard_same_day_t10_seed42_20260829_171003.jsonl`",
        "- M7完整18例：`output/results/model7_exact_and_alns/model7_large_alns_t10_seed42_final.jsonl`",
    ]
    return "\n".join(lines) + "\n"


def main() -> None:
    exact_rows = load(EXACT_INPUT)
    alns_base_rows = load(ALNS_BASE_INPUT)
    common_instances = {row["instance"] for row in alns_base_rows}
    alns_m7_rows = [
        row for row in load(ALNS_M7_INPUT)
        if row["instance"] in common_instances and row.get("feasible")]
    alns_rows = sorted(
        [*alns_base_rows, *alns_m7_rows],
        key=lambda row: (int(row["customers"]), row["instance"], int(row["model"])))
    small_alns_rows = load(SMALL_ALNS_INPUT)
    if len(exact_rows) != 132 or len(alns_rows) != 102:
        raise RuntimeError(
            f"unexpected inputs: exact={len(exact_rows)}, alns={len(alns_rows)}")
    write_rows(alns_rows, ALNS_OUTPUT_JSONL, ALNS_OUTPUT_CSV)
    EXACT_REPORT.write_text(exact_report(exact_rows), encoding="utf-8")
    ALNS_REPORT.write_text(alns_report(alns_rows, small_alns_rows), encoding="utf-8")
    print(EXACT_REPORT)
    print(ALNS_REPORT)


if __name__ == "__main__":
    main()
