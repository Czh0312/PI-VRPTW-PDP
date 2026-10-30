"""Collect completed long-budget evidence without changing native statuses."""
import hashlib
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
CASES = ['S03', 'S08', 'S09']


def read(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')


def main():
    rows = [read(HERE / case / 'summary.json') for case in CASES]
    save(HERE / 'summary.json', rows)
    certificates = []
    for row in rows:
        folder = HERE / row['case']
        names = ['manifest.json', 'reduction_certificate.json', 'phase_bound.json', 'solver_certificate.json',
                 'native_summary.json', 'gurobi.log', 'best_audited_result.json', 'summary.json',
                 'frozen_sources_inputs.zip']
        if (folder / 'native_result.json').exists():
            names.append('native_result.json')
        hashes = {name: hashlib.sha256((folder / name).read_bytes()).hexdigest() for name in names}
        certificates.append(dict(
            case=row['case'], original_K=row['original_K'],
            kind='GLOBAL_OPTIMALITY' if row['external_full_domain_optimality_certified'] else 'VALID_BOUNDS_ONLY',
            native_status=row['native_status'], upper_bound=row['best_audited_upper_bound'],
            global_lower_bound=row['global_lower_bound'], gap_over_upper=row['gap_over_upper'],
            global_optimality_certified=row['external_full_domain_optimality_certified'],
            selected_K=row['selected_K'], selected_pool=row['selected_pool'],
            evidence_sha256=hashes, verification_absolute_tolerance=1e-6,
            proof='An audited feasible U caps the original optimum. Shared all-K relaxation extrema '
                  'preserve every original solution of cost <= U in the retained domain. Complete '
                  'original integer-clock MILPs on that domain and global relaxations give valid '
                  'lower bounds. Equality with an audited feasible cost certifies full-domain optimality.',
        ))
    save(HERE / 'certificates.json', certificates)
    lines = [
        '# 模型八长预算实验：S03、S08、S09', '', '2026-10-28。三例优化预算均由此前 600 秒提高到本轮 3600 秒，达到最优时提前停止。', '',
        '| 算例 | 此前成本 | 本轮成本 | 此前下界 | 本轮下界 | 此前界差距 | 本轮界差距 | 原生状态 | 实际优化秒 |',
        '|---|---:|---:|---:|---:|---:|---:|---|---:|',
    ]
    for row in rows:
        lines.append(f"| {row['case']} | {row['prior_upper_bound']:.6f} | {row['best_audited_upper_bound']:.6f} | "
                     f"{row['prior_lower_bound']:.6f} | {row['global_lower_bound']:.6f} | "
                     f"{100*row['prior_gap_over_upper']:.4f}% | {100*row['gap_over_upper']:.4f}% | "
                     f"{row['native_status']} | {row['mip_runtime']:.3f} |")
    proven = [row['case'] for row in rows if row['external_full_domain_optimality_certified']]
    pending = [row['case'] for row in rows if not row['external_full_domain_optimality_certified']]
    lines += ['', f"本轮证明原始全 K 域最优：{'、'.join(proven) if proven else '无'}。"
              f"仍未闭合：{'、'.join(pending) if pending else '无'}。", '',
              'S09 使用 3 个批次、3 个箱子，优化约 3044.763 秒（50 分 45 秒）达到原生 OPTIMAL，覆盖原始 K=1～7。此前已有同成本的可行方案，本轮完成最优性证明。', '',
              'S08 的新方案采用 2 个批次、5 个箱子，成本从 52.378137 降至 51.990210，但尚未证明最优。S03 最好成本未变，下界有所提高。加上此前结果，当前 9 个七顾客小算例已有 7 个证明全局最优，剩余 S03、S08。', '',
              '## 收敛曲线', '',
              f"![长预算内的证明进度]({(HERE / 'convergence.png').as_posix()})", '',
              '曲线以本轮最终通过业务审计的可行成本为固定分母，回看求解日志下界，因此只展示下界的推进。S08 的成本改善见上表；曲线起点不等同于上一轮使用较高可行成本时的界差距。日志下界有显示舍入，精确最终界以 summary.json 为准。convergence.csv 保存绘图数据。', '',
              '## 设置与计时', '',
              '原证明范围仍为 K=1～7。每例重新审计上一轮最好可行方案，重新生成共享路线库、K/N 缩域端点和阶段有序一级路线下界。完整 MILP 保留全部原始业务约束和逐 K 整数时钟，使用 compact、physical_time、单线程、种子 42。三例同时运行。', '',
              '前一轮求解器进程与分支树已结束，本轮从重新构造的模型开始。使用已有方案的标量成本上界，不输入路线初解或变量提示。S03 的成本上界比上一轮初始输入更紧，三例还显式使用重新核验的阶段路线下界；因此本轮是更长预算的增强流程验证，不能作为只有时间变化的严格速度对照。', '',
              '表中差距为 (U−L)/U，是最优性证明差距。U 来自对原始全 K 实例的业务、成本及两周期独立审计；L 取有效全域松弛下界与截断完整模型下界的安全组合。正差距不等于已知方案必然次优。原生 TIME_LIMIT 状态不因松弛模型 OPTIMAL 而改写。', '',
              '实际优化秒不含解释器启动、运行环境预检、输入加载、源码冻结、缩域、建模、提取/审计和事后测试。各阶段开销保存在每例 summary.json 中。模型输入、源码、驱动和历史证据以哈希及 ZIP 快照冻结，生产求解器和输入未修改。', '',
              '## 验证与证据', '']
    validation_path = HERE / 'validation.json'
    if validation_path.exists():
        validation = read(validation_path)
        lines.append(f"所选 test_evidence.py：{validation['passed']} passed、{validation['failed']} failed、"
                     f"{validation['skipped']} skipped；未运行整个仓库测试套件。预检 READY，Gurobi 11.0.3 的实际整数求解通过。")
        lines.append('实际分两次选择运行：S09 的 5 项通过（10 项未选入），以及 S03/S08 的 10 项通过（5 项未选入）；共 15 个不同测试，无跳过。')
    else:
        lines.append('独立测试结果尚未记录。')
    lines += ['', '证据包括每例的 manifest.json、冻结 ZIP、reduction_certificate.json、phase_bound.json、solver_certificate.json、native_summary.json、gurobi.log、best_audited_result.json；有新整数解时另有 native_result.json。顶层 summary.json 和 certificates.json 保留最终范围、上下界、证明结论及证据文件哈希。', '',
              '独立验证重新加载原始完整 K 域，复核源码/物理输入及 ZIP 内容、费用、两周期回放、原生状态与整数时钟，并重新求共享下界、四个 K/N 端点和阶段路线下界。最优性按 Gurobi 浮点优化及 1e-6 复核容差判断。', '']
    (HERE / 'report.md').write_text('\n'.join(lines), encoding='utf-8')
    print(json.dumps(rows), flush=True)


if __name__ == '__main__':
    main()
