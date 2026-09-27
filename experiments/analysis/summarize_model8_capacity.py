# Historical M8 experiments: retain their original two-stage semantics.
"""Re-audit stored M8 results and summarize mechanism versus benchmark evidence."""
import json
import math
from pathlib import Path
import shutil

from experiments.runs.run_model8_capacity_probe import load_problem, dump
from models.routing.variants.model_8.two_stage_v2.baseline import prepare_baseline
from models.routing.variants.model_8.two_stage_v2.state import Evaluator


def main():
    folder = Path('output/experiments/model8_capacity_20260922')
    summary = json.loads((folder / 'summary.json').read_text())
    checks = []
    for instance in summary['settings']['instances']:
        p, a, b, _ = load_problem(instance)
        base = json.loads((folder / instance / 'base.json').read_text())
        snapshot = prepare_baseline(p, a, b, base['effective_vehicle_count'],
                                    (base['e1_routes'], base['e2_routes']))
        e = Evaluator(p, a, b, snapshot)
        for path in sorted((folder / instance).glob('*.json')):
            if path.name == 'base.json':
                continue
            result = json.loads(path.read_text())
            info = result['info']
            assert info['baseline']['route_fingerprint'] == snapshot['route_fingerprint']
            assert result['e1_routes'] == snapshot['e1_routes']
            assert result['e2_routes'] == info['state']['e2_routes']
            audit = e.audit(info['state'])
            assert math.isclose(audit['objective'], info['objective'], rel_tol=1e-9, abs_tol=1e-6)
            if 'control' in path.name:
                assert not info['state']['transfers']
            checks.append({'file': str(path), 'valid': True, 'objective': audit['objective']})
    snapshot_folder = folder / 'source'
    for path in summary['source_sha256']:
        dst = snapshot_folder / path
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, dst)
    dump(folder / 'verification.json', {'status': 'PASS', 'audited_solutions': len(checks),
                                        'checks': checks})
    rows = summary['results']
    def selected(instance, prefix):
        return [r for r in rows if r['instance'] == instance and r['method'].startswith(prefix)]
    tight0 = selected('mechanism_tight', 'exact_control')[0]
    tight1 = selected('mechanism_tight', 'exact_relay')[0]
    loose0 = selected('mechanism_loose', 'exact_control')[0]
    loose1 = selected('mechanism_loose', 'exact_relay')[0]
    benefit = tight0['objective'] - tight1['objective']
    lines = ['# M8 v2 实验结论', '', '## Material Passport', '',
             '- 状态：实际运行结果；2026-09-22。',
             '- 完整实验表与证明状态：[report.md](report.md)；原始结果：[summary.json](summary.json)。',
             '- 四个案例：两个容量机制构造情景、两个未经修改的 Zhou 标准当日回收实例。',
             '- 每个启发式分支 20 秒，种子 42、7、19；原始 100/200 实例复用旧 M8 的同一基础解。',
             '- 目标单位为坐标距离，不应直接称为真实公里。', '',
             '## 结论', '',
             f'容量释放的机制已得到小规模精确验证：无交接最优 {tight0["objective"]:.6f}，'
             f'允许交接最优 {tight1["objective"]:.6f}，净节省 {benefit:.6f}（{100*benefit/tight0["objective"]:.4f}%）。'
             '两种条件问题均已证明最优，基础 M3 也已证明最优；三次启发式均达到相同距离。', '',
             f'体积容量从 3 放宽至 4 后，无交接与交接的条件最优值均为 {loose0["objective"]:.6f}；'
             f'交接增量收益为 {loose0["objective"] - loose1["objective"]:.6f}。'
             '这个对照表明构造例的收益依赖容量瓶颈，不能将其 10.8% 推广到原始数据。', '',
             '| 原始实例 | 基础距离 | 禁止交接最好距离（3种子） | 允许交接最好距离（3种子） | 最好解交接数 |',
             '|---|---:|---:|---:|---:|']
    for instance in ('Set2_100-10-1', 'Set2_200-10-1'):
        controls, relays = selected(instance, 'alns_control'), selected(instance, 'alns_relay')
        c = min(controls, key=lambda r: r['objective'])
        r = min(relays, key=lambda r: r['objective'])
        lines.append(f"| {instance} | {r['baseline']:.6f} | {c['objective']:.6f} | {r['objective']:.6f} | {r['transfers']} |")
    relays = [r for r in rows if r['instance'].startswith('Set2') and r['method'].startswith('alns_relay')]
    lines += ['', '这轮两个原始大规模实例均未发现优于基础解的传递解。200 客户禁止交接的一个种子'
              '通过普通回收调整节省 0.650282（约 0.0301%），不能归因于空箱传递。'
              '100 客户基础解已证明最优，200 客户基础解仅为最好已知可行解。', '',
              f'大实例允许交接分支在每次 20 秒内完成 {min(r["iterations"] for r in relays):,}–'
              f'{max(r["iterations"] for r in relays):,} 次搜索，进程峰值内存约 '
              f'{min(r["memory_mib"] for r in relays):.1f}–{max(r["memory_mib"] for r in relays):.1f} MiB。'
              '旧 M8 的 200 客户修复仅建模就约 30 秒，因此本次解决了该建模开销问题；'
              '算法、预算和实现不同，不据此声称同算法的严格加速比。', '',
              '## 可行性与验证', '',
              f'- 重新读取并独立复核了 {len(checks)} 个保存的第二阶段解，全部通过任务覆盖、配送承诺、'
              '重量/体积、O/R 流、一次交接、时间窗与两级同步检查，见 [verification.json](verification.json)。',
              '- 相关测试 33 项通过，覆盖新版 M8、旧版 M8 复现及 M6 精确/ALNS 回归。',
              '- 新版精确与 ALNS 的统一 CLI 均已在 7 客户标准实例跑通；精确距离 337.617968，无改善。',
              '- 禁用 Gurobi 导入时，新版第二阶段仍可找到构造例的 116.787087 解。', '',
              '## 机制图', '',
              '![构造例中的路径与容量变化](capacity_mechanism.png)', '',
              '左图：原 B 为取 P 的空箱绕行；中图：A 顺路代取后在 H 交给 B；右图：'
              '同样的任务重分配若不交接，A 会超过体积容量。图中交接数量来自保存的精确解；'
              '该例转移 1 箱或 2 箱可得到相同最短距离，启发式优先使用最少可行数量。', '',
              '## 适用范围', '',
              '新版仍限制 E1 和配送骨架。启发式单次移动至多 3 个回收任务、只新增一次两车交接，'
              '插入顺序和交接点均经过候选截断。零改善只说明这些预算和邻域内没有找到更好的最终解，'
              '不构成大实例无交接价值的最优性证明。未通过放宽容量、时间窗或新增费用制造原始实例收益。']
    diagnostic = folder / 'diagnostic.json'
    if diagnostic.exists():
        lines += ['', '## 附加时序诊断（单独 5 秒，不合并主实验）', '']
        for row in json.loads(diagnostic.read_text()):
            rejected = row['audit_rejections'].get('with_handoff: 时间窗或两级衔接不可行', 0)
            lines.append(f"- {row['instance']}：种子42，{row['iterations']}次迭代；"
                         f"{rejected}个含交接的候选因时间窗/两级衔接被拒绝。详见 [diagnostic.json](diagnostic.json)。")
        lines += ['', '这些计数只描述此次已被生成并审计的候选，不代表所有潜在方案；'
                  '说明后续应关注容量释放与时间窗口是否同时匹配，不能只依据空间靠近或剩余容量判断。']
    (folder / 'findings.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')
    print(json.dumps({'audited_solutions': len(checks), 'findings': str(folder / 'findings.md')}, ensure_ascii=False))


if __name__ == '__main__':
    main()
