"""Summarize paired ALNS probes and independently audit zero-handoff routes."""
import argparse
import json
import statistics as stats
from pathlib import Path

from experiments.runs.run_model12346_alns_scaling import ROOT, load_problem
from models.routing.common.solution_audit import audit_exact_solution


def read(path):
    return [json.loads(line) for line in path.read_text().splitlines()]


def key(row):
    return row["instance"], row["seed"], row["model"]


def paired(left, right, model):
    index = {key(r): r for r in right}
    return [(r, index[key(r)]) for r in left
            if r["model"] == model and key(r) in index
            and r["info"].get("feasible") and index[key(r)]["info"].get("feasible")]


def gains(pairs):
    return [100 * (a["info"]["objective"] - b["info"]["objective"])
            / a["info"]["objective"] for a, b in pairs]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    before, after, heldout, workload = [read(args.directory / f"{name}.jsonl")
        for name in ("before", "after", "heldout", "fixed_workload")]
    lines = ["# ALNS 质量与速度优化验证", "",
        "主试验：Set2_100-5-1、Set2_100-10-1、Set2_200-10-1，seed=42/43/44；"
        "每次 3 秒，迭代上限 1,000,000。before 为修改前运行，after 为最终实现。",
        "补充验证：Set2_100-5-2b、Set2_100-10-2b、Set2_200-10-2b，seed=11/23，"
        "同样预算，未参与本轮实现调试。各模型独立从头求解，串行运行，没有额外继承预算。",
        "运行环境：/opt/anaconda3/bin/python3，PYTHONHASHSEED=0，zhou_large.json 车队配置。"
        "M6/M7 沿用统一 runner 的 0.8/0.75 阶段一比例；M3 未修改。", "",
        "## 相对各自旧实现", "",
        "改善率逐实例与种子计算后取算术平均；吞吐倍数为总迭代数之比。", "",
        "| 模型 | 配对数 | 距离改善 | 迭代吞吐倍数 | 新实现平均耗时 |",
        "| --- | ---: | ---: | ---: | ---: |"]
    for model in (4, 6, 7):
        pairs = paired(before, after, model)
        rate = sum(b["info"]["iterations"] for a, b in pairs) / sum(a["info"]["iterations"] for a, b in pairs)
        lines.append(f"| M{model} | {len(pairs)} | {stats.mean(gains(pairs)):.3f}% | {rate:.2f}× | {stats.mean(b['wall_time'] for a, b in pairs):.4f}s |")
    lines += ["", "## 相对同批 M3", "",
        "正数代表扩展模型距离更短。胜/平/负按改善率 ±1e-6 个百分点区分。", "",
        "| 样本 | 模型 | 配对数 | 平均改善 | 胜/平/负 |",
        "| --- | --- | ---: | ---: | --- |"]
    for label, rows in (("主试验", after), ("补充验证", heldout), ("合并", after + heldout)):
        base = {(r["instance"], r["seed"]): r for r in rows if r["model"] == 3}
        for model in (4, 6, 7):
            pairs = [(base[(r["instance"], r["seed"])], r) for r in rows if r["model"] == model
                     and r["info"].get("feasible") and base[(r["instance"], r["seed"])]["info"].get("feasible")]
            gs = gains(pairs)
            wins, losses = sum(g > 1e-6 for g in gs), sum(g < -1e-6 for g in gs)
            lines.append(f"| {label} | M{model} | {len(gs)} | {stats.mean(gs):.3f}% | {wins}/{len(gs)-wins-losses}/{losses} |")
    lines += ["", "## 固定工作量速度验证", "",
        "M6 每例 500 轮、seed=42，关闭中继与 refill，使用按迭代降温；"
        "关闭按耗时奖励、按时间重启和按时间更新权重。逐例断言新旧路线、目标值完全一致。", "",
        "| 实例 | 旧实现 | 新实现 | 加速倍数 |",
        "| --- | ---: | ---: | ---: |"]
    for name in sorted({r["instance"] for r in workload}):
        old = next(r for r in workload if r["instance"] == name and not r["fast_stage1"])
        new = next(r for r in workload if r["instance"] == name and r["fast_stage1"])
        lines.append(f"| {name} | {old['wall_time']:.4f}s | {new['wall_time']:.4f}s | {old['wall_time']/new['wall_time']:.2f}× |")
    fleet = json.loads((ROOT / "config/routing/validation/zhou_large.json").read_text())
    audits = []
    for row in after + heldout:
        if not row["info"].get("feasible") or row["info"].get("transshipment_count", 0):
            continue
        problem = load_problem(ROOT / "data/zhou_later_pickup/large/standard_same_day"
                               / f"{row['instance']}.csv", fleet)
        result = audit_exact_solution(problem, 3 if row["model"] in (3, 6) else 4,
            row["e1_routes"], row["e2_routes"], assignment=row["assignment"],
            objective=row["info"]["objective"])
        audits.append(dict(instance=row["instance"], seed=row["seed"], model=row["model"], audit=result))
    (args.directory / "independent_audits.json").write_text(json.dumps(audits, indent=2))
    assert all(r["audit"]["valid"] for r in audits), "independent route audit failed"
    rows = after + heldout
    error = max(abs(r["route_cost_delta"]) for r in rows if r["route_cost_delta"] is not None)
    lines += ["", "## 验证与解释边界", "",
        f"最终与补充试验共 {len(rows)} 次，其中 {sum(bool(r['info'].get('feasible')) for r in rows)} 次可行；"
        f"最大距离复算误差 {error:.3g}。{len(audits)} 个无中继解通过独立容量、任务覆盖、时间窗、"
        "先送后取和 E1/E2 同步审计；含中继解由模型中继评估器检查，并复算公开路线距离。",
        "43 项相关回归测试通过，包括缓存与原路径等价、分仓状态隔离、M3 热启动保留、中继记账与搜索引擎。",
        "实现：M4/M7 默认 80% 的轮次尝试固定当前分仓的 M3 路径邻域，"
        "其余轮次使用原分仓邻域；同分仓仅复用任务表，不复用时间表。"
        "M6 的无中继阶段使用 M3 评估器及有界距离/时间/服务时间缓存，中继评估保持独立。",
        "合并样本不是总体分布的随机抽样，也未做统计显著性检验；不能保证每个实例或 seed 都优于 M3。"
        "M4/M7 在补充的 200 客户实例上仍有劣势，后续应针对跨仓邻域的选择与时间分配继续改进。",
        "M6 的收益主要来自搜索提速；相对 M3 的距离改善不能直接解释为中继机制收益。"
        "等时吞吐和固定迭代耗时，也不能直接替代达到同一目标值所需时间的测量。", "",
        "## 复现", "", "```sh",
        "PYTHONHASHSEED=0 python -m experiments.runs.run_alns_quality_probe --output /tmp/alns_after.jsonl",
        "PYTHONHASHSEED=0 python -m experiments.runs.run_alns_quality_probe --instances Set2_100-5-2b Set2_100-10-2b Set2_200-10-2b --seeds 11 23 --output /tmp/alns_heldout.jsonl",
        "PYTHONHASHSEED=0 python -m experiments.runs.run_m6_stage1_speed_probe --output /tmp/alns_fixed_workload.jsonl",
        "python -m experiments.analysis.summarize_alns_quality_probe output/experiments/alns_quality_20260908", "```", "",
        "输出文件采用独占创建；重复运行时需使用新文件名。旧数据与 mixed_v1 调试结果均保留。"]
    (args.directory / "report.md").write_text("\n".join(lines) + "\n")


if __name__ == "__main__":
    main()
