"""Report a complete large ALNS comparison, with paired metrics and audits."""
from __future__ import annotations

import argparse
import hashlib
import json
import statistics as stats
from pathlib import Path

from experiments.runs.run_model12346_alns_scaling import ROOT, load_problem
from models.routing.common.solution_audit import audit_exact_solution

MODELS = (3, 4, 6, 7)


def identity(row):
    return row["instance"], row["seed"]


def valid(row):
    return (bool(row["info"].get("feasible"))
            and row["info"].get("objective") is not None
            and row.get("route_cost_delta") is not None
            and abs(row["route_cost_delta"]) < 1e-6)


def comparisons(rows, left, right):
    by_model = {
        model: {identity(r): r for r in rows if r["model"] == model and valid(r)}
        for model in (left, right)
    }
    gains = [100 * (by_model[left][key]["info"]["objective"]
                    - by_model[right][key]["info"]["objective"])
             / by_model[left][key]["info"]["objective"]
             for key in sorted(by_model[left].keys() & by_model[right].keys())]
    return dict(count=len(gains), mean=stats.mean(gains) if gains else None,
                median=stats.median(gains) if gains else None,
                wins=sum(g > 1e-6 for g in gains),
                ties=sum(abs(g) <= 1e-6 for g in gains),
                losses=sum(g < -1e-6 for g in gains))


def fmt(value, digits=3):
    return "—" if value is None else f"{value:.{digits}f}"


def size_group(name):
    customers, satellites = name.split("_", 1)[1].split("-")[:2]
    return f"{customers} 客户 / {satellites} 仓"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--partial", action="store_true")
    args = parser.parse_args()
    manifest = json.loads((args.directory / "manifest.json").read_text())
    rows = [json.loads(line) for line in (args.directory / "runs.jsonl").read_text().splitlines()]
    keys = [(r["instance"], r["seed"], r["model"]) for r in rows]
    expected = {(instance, seed, model) for instance in manifest["instances"]
                for seed in manifest["seeds"] for model in manifest["models"]}
    assert len(keys) == len(set(keys)), "duplicate runs"
    assert set(keys) <= expected, "unexpected run"
    assert all(r["time_budget"] == manifest["seconds"] for r in rows)
    if not args.partial:
        assert set(keys) == expected, f"incomplete: {len(keys)}/{len(expected)}"
        assert all(hashlib.sha256((ROOT / path).read_bytes()).hexdigest() == digest
                   for path, digest in manifest["source_sha256"].items()), "solver source changed during run"
    common = set.intersection(*[
        {identity(r) for r in rows if r["model"] == model and valid(r)}
        for model in MODELS])
    lines = ["# 全部大规模实例 ALNS 对比", "",
        f"{'进行中' if args.partial else '已完成'}：{len(rows)}/{len(expected)} 次；"
        f"{len(manifest['instances'])} 个实例，seed={manifest['seeds']}，每次 {manifest['seconds']} 秒。",
        "M3/M4/M6/M7 各自从头求解，迭代上限 1,000,000，串行执行。"
        "数据为 zhou_later_pickup/large/standard_same_day，车队为 zhou_large.json。"
        "M6/M7 阶段一比例沿用 0.8/0.75。本轮仅运行实验，未修改算法。", "",
        "## 整体结果", "",
        f"平均距离仅使用四个模型共同可行的 {len(common)} 个实例/种子配对，避免因可行率不同而失真。"
        "平均耗时与迭代数包含全部运行。", "",
        "| 模型 | 可行/运行 | 共同可行平均距离 | 平均耗时 | 平均迭代数 | 最终使用中继的运行数 |",
        "| --- | ---: | ---: | ---: | ---: | ---: |"]
    model_stats = {}
    for model in MODELS:
        subset = [r for r in rows if r["model"] == model]
        feasible = [r for r in subset if valid(r)]
        shared = [r["info"]["objective"] for r in feasible if identity(r) in common]
        model_stats[model] = dict(runs=len(subset), feasible=len(feasible),
            common_mean_objective=stats.mean(shared) if shared else None,
            mean_wall_time=stats.mean(r["wall_time"] for r in subset) if subset else None,
            mean_iterations=stats.mean(r["info"].get("iterations", 0) for r in subset) if subset else None,
            relay_runs=sum(r["info"].get("transshipment_count", 0) > 0 for r in feasible))
        s = model_stats[model]
        lines.append(f"| M{model} | {s['feasible']}/{s['runs']} | {fmt(s['common_mean_objective'])} | {fmt(s['mean_wall_time'])}s | {fmt(s['mean_iterations'], 1)} | {s['relay_runs']} |")
    lines += ["", "## 配对改善", "",
        "正数表示后者距离更短；先逐实例/种子计算改善率，再取平均。胜负使用 ±1e-6 个百分点容差。", "",
        "| 比较 | 配对数 | 平均改善 | 中位改善 | 后者胜/平/负 |",
        "| --- | ---: | ---: | ---: | --- |"]
    pair_stats = {}
    for left, right in ((3, 4), (3, 6), (3, 7), (4, 7), (6, 7)):
        s = comparisons(rows, left, right)
        pair_stats[f"{left}_{right}"] = s
        lines.append(f"| M{left} → M{right} | {s['count']} | {fmt(s['mean'])}% | {fmt(s['median'])}% | {s['wins']}/{s['ties']}/{s['losses']} |")
    lines += ["", "## 按规模分组", "",
        "| 规模 | 比较 | 配对数 | 平均改善 | 后者胜/平/负 |",
        "| --- | --- | ---: | ---: | --- |"]
    groups = {}
    for group in sorted({size_group(r["instance"]) for r in rows}):
        subset = [r for r in rows if size_group(r["instance"]) == group]
        groups[group] = {}
        for left, right in ((3, 4), (3, 6), (3, 7), (4, 7)):
            s = comparisons(subset, left, right)
            groups[group][f"{left}_{right}"] = s
            lines.append(f"| {group} | M{left} → M{right} | {s['count']} | {fmt(s['mean'])}% | {s['wins']}/{s['ties']}/{s['losses']} |")
    lines += ["", "## 逐实例结果", "",
        "改善率为该实例共同可行种子的平均值。不可配对显示 —。", "",
        "| 实例 | M3 可行数 | M4 相对 M3 | M6 相对 M3 | M7 相对 M3 | M7 相对 M4 |",
        "| --- | ---: | ---: | ---: | ---: | ---: |"]
    for name in manifest["instances"]:
        subset = [r for r in rows if r["instance"] == name]
        base = [r for r in subset if r["model"] == 3]
        gs = [comparisons(subset, left, right)["mean"] for left, right in ((3, 4), (3, 6), (3, 7), (4, 7))]
        lines.append(f"| {name} | {sum(valid(r) for r in base)}/{len(base)} | " + " | ".join(fmt(g) + "%" if g is not None else "—" for g in gs) + " |")
    invalid = [{"instance": r["instance"], "model": r["model"], "seed": r["seed"], "status": r["info"].get("status")} for r in rows if not valid(r)]
    lines += ["", "## 审计与限制", "",
        "无解仅表示本次求解未返回可行解，不据此认定数学模型不可行。"
        "限时随机搜索的平均收益不是逐例保证，也不能直接当作中继机制收益。"
        "同样耗时下的迭代数不等价于达到同样目标质量所需时间。",
        f"未返回经距离复算可行解的运行数：{len(invalid)}。"]
    audit_rows = []
    if not args.partial:
        for row in rows:
            if not valid(row) or row["info"].get("transshipment_count", 0):
                continue
            problem = load_problem(ROOT / "data/zhou_later_pickup/large/standard_same_day"
                                   / f"{row['instance']}.csv", manifest["fleet"])
            audit = audit_exact_solution(problem, 3 if row["model"] in (3, 6) else 4,
                row["e1_routes"], row["e2_routes"], assignment=row["assignment"],
                objective=row["info"]["objective"])
            audit_rows.append(dict(instance=row["instance"], seed=row["seed"], model=row["model"], audit=audit))
        (args.directory / "independent_audits.json").write_text(json.dumps(audit_rows, indent=2))
        assert all(r["audit"]["valid"] for r in audit_rows), "independent audit failed"
        error = max((abs(r["route_cost_delta"]) for r in rows if r["route_cost_delta"] is not None), default=0)
        lines += [f"{len(audit_rows)} 个无中继解通过独立容量、任务覆盖、时间窗、先送后取和 E1/E2 同步审计。"
            "含中继解使用模型评估器检查；全部公开路线均复算距离。"
            f"最大距离复算误差：{error:.3g}。"]
        conflict_file = args.directory / "fixed_assignment_conflicts.json"
        if conflict_file.exists():
            conflicts = json.loads(conflict_file.read_text())
            for instance, evidence in conflicts.items():
                for item in evidence:
                    lines.append(
                        f"另外验证到固定分仓的直接时间窗冲突：{instance} 的 {item['customer']} "
                        f"经 {item['assigned_satellite']} 最早直接到达时间为 "
                        f"{item['earliest_direct_delivery']:.4f}，超过截止时间 {item['delivery_due']:.4f}。"
                        "即使忽略服务和等待也无法按时送达；M3/M6 的固定分仓不能消除这一冲突。"
                        "原始证据保存于 fixed_assignment_conflicts.json。")
    summary = dict(completed=len(rows), expected=len(expected), common_pairs=len(common),
                   models=model_stats, comparisons=pair_stats, groups=groups, invalid_runs=invalid)
    stem = "progress" if args.partial else "report"
    (args.directory / f"{stem}.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2))
    lines += ["", "## 复现", "", "完整运行命令、Python 环境、数据与求解器源码 SHA256 存于 manifest.json。", "",
        "```sh", f"python -m experiments.analysis.summarize_alns_large_full {args.directory}", "```"]
    (args.directory / f"{stem}.md").write_text("\n".join(lines) + "\n")
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()
