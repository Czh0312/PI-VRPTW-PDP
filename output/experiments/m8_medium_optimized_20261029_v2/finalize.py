"""Summarize completed timing attempts and the actual selected validation."""
from pathlib import Path
import csv
import xml.etree.ElementTree as ET

from study import HERE, ROOT, read, save, sha


def main():
    row = read(HERE / 'summary.json')
    baseline = read(ROOT / 'output/experiments/m8_medium_m01_20261029_v1/summary.json')
    first = read(ROOT / 'output/experiments/m8_medium_optimized_20261029_v1/summary.json')
    manifest = read(HERE / 'manifest.json')
    route_audit = read(HERE / 'independent_route_audit.json')
    raw = read(HERE / 'input_only_bound.json')
    reviewed = dict(row)
    if not raw['finite_native_bound_available']:
        reviewed['lower_bound_source'] = 'necessary_pool_bound_only'
    save(HERE / 'summary_reviewed.json', reviewed)
    save(HERE / 'metadata_review.json', dict(raw_summary_preserved=True,
        raw_lower_bound_source_label=row['lower_bound_source'],
        actual_lower_bound_source=reviewed['lower_bound_source'],
        explanation='The initial driver labels by file existence; a completed master attempt without a finite bound still leaves only the physical fallback. No numeric metric or solver status is changed.'))
    scopes = [
        (ROOT / 'output/experiments/m8_medium_optimized_20261029_v1/validation_setup.xml',
         '三份路线/分组测试、原下界测试与可变箱池子问题准备检查'),
        (HERE / 'validation_timeout_final.xml', '真实求解器超时无界处理'),
        (HERE / 'validation_evidence.xml', '本轮完整域、全部路线及最终业务证据'),
    ]
    totals = dict(tests=0, failures=0, errors=0, skipped=0)
    counts = []
    for path, description in scopes:
        root = ET.parse(path).getroot()
        item = {key: sum(int(node.get(key,0)) for node in root.iter('testsuite')) for key in totals}
        for key in totals:
            totals[key] += item[key]
        counts.append(dict(path=str(path), scope=description, **item))
    totals['passed'] = totals['tests']-totals['failures']-totals['errors']-totals['skipped']
    assert totals['failures'] == totals['errors'] == totals['skipped'] == 0
    save(HERE / 'validation.json', dict(scopes=counts, totals=totals, full_repository_suite=False))
    stages = dict(compile=row['compile_seconds'], enumeration=row['enumeration_seconds'],
        library_export=row['library_export_seconds'], master_build=row['master_build_seconds'],
        master_optimization=raw['native_runtime'], total=row['total_wall_seconds'])
    save(HERE / 'comparison.json', dict(baseline=baseline, phase_cap_diagnostic=first, optimized=row,
        optimized_stages=stages, same_original_K_and_clock=True, same_initial_audited_upper_bound=True,
        single_seed_single_run=True, strict_speedup_factor_claimed=False))
    fields = ['case','customers','status','total_wall_seconds','enumeration_seconds','master_build_seconds',
              'route_columns','audited_upper_bound','global_lower_bound','gap_over_upper','proven_optimal']
    with (HERE / 'comparison.csv').open('w', encoding='utf-8-sig', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction='ignore')
        writer.writeheader()
        writer.writerows([baseline,row])
    result = '已完成全域最优性证明' if row['proven_optimal'] else '尚未完成原问题的全域最优性证明'
    text = [
        '# 模型八 M01：路线枚举与主问题装配优化', '',
        f"2026-10-29。完整路线库已从旧版 600 秒内未完成，改善为本轮 {row['enumeration_seconds']:.3f} 秒完成两个前置仓；最终 {result}。", '',
        '| 项目 | 原版 M01 | 优化后本轮 M01 |', '|---|---:|---:|',
        '| 顾客/前置仓 | 20 / 2 | 20 / 2 |',
        '| 原始域与时钟 | K=1～20、1 秒 | K=1～20、1 秒 |',
        f"| 名义总预算 | 600 秒 | {row['budget_seconds']} 秒 |",
        f"| 实际算法墙钟 | {baseline['total_wall_seconds']:.3f} 秒 | {row['total_wall_seconds']:.3f} 秒 |",
        f"| 完整路线枚举 | 超时，尚在第一个前置仓 | {row['enumeration_seconds']:.3f} 秒完成 |",
        f"| 任务集合数 | 338899 个，库不完整 | {row['route_columns']:,} 个，库完整 |",
        f"| 状态 | {baseline['status']} | {row['status']} |",
        f"| 已审计可行成本 U | {baseline['audited_upper_bound']:.9f} | {row['audited_upper_bound']:.9f} |",
        f"| 当前有效全域下界 L | {baseline['global_lower_bound']:.9f} | {row['global_lower_bound']:.9f} |",
        f"| (U−L)/U | {100*baseline['gap_over_upper']:.3f}% | {100*row['gap_over_upper']:.3f}% |", '',
        '旧版没有完成枚举，因此不能用 600 除以新耗时，发布一个确定的完整枚举加速倍数；两次运行也没有控制整个电脑的其他负载。该表只比较本机单线程、种子 42、同输入及同已有上界的一次记录。', '',
        '## 本轮阶段耗时', '', '| 阶段 | 秒 |', '|---|---:|',
    ]
    labels = [('compile','辅助程序编译'),('enumeration','完整枚举与列转换'),('library_export','紧凑路线库写盘'),
              ('master_build','主问题装配'),('master_optimization','主问题原生优化'),('total','算法总墙钟')]
    text += [f'| {label} | {stages[key]:.3f} |' for key,label in labels]
    text += ['',
        '编译、进程启动、输入重载、原方案审计、完整枚举、紧凑导出、装配、优化和最终审计均计入本轮算法墙钟。环境预检、历史可行方案寻找、源码冻结、专项验证与报告归档另计。复用已审计可行上界，不复用历史下界、缩域或路线库，不提供 MIP 初解。', '',
        '## 已实施的改动', '',
        '1. 增加正确的分层时间/距离/取箱就绪标签支配。对九个小规模与随机边界数据逐任务集合验证最短距离。它在 M01 上仍不够快，保留为可移植实现。',
        '2. 用独立编译辅助程序执行完整参考枚举，保留原容量、时间窗、最短使用时间和所有访问顺序。没有安装包或修改 Python/系统环境。探索中的 Numba 因版本不兼容未采用。',
        '3. 把共享非空任务的路线分批/回收联系合并为组级约束。精确覆盖保证同组至多选择一列，因此保持整数可行集，并可加强分数松弛。',
        '4. 在完整模式子问题中消除已固定为零的列；可变箱池通过明确的整数值选择变量进入模式签名与条件割；缓存只保留有限个原生模型。原始路线顺序、车辆和时钟仍由完整子问题决定。',
        '5. 保留每条代表路线的完整二进制路径证据；主问题列对象省略长路径文本，用紧凑格式写盘。', '',
        '这些是实验求解流程优化；生产默认直接 MILP 入口没有切换。实现说明见 docs/model8_route_library_acceleration.md。', '',
        '## 有效性与结论边界', '',
        f"独立回放了全部 {route_audit['route_columns']:,} 条代表路线：任务唯一性、初始及全程重量/体积、送取时间窗、同趟最短使用时间、回仓和距离全部通过；验证墙钟 {route_audit['independent_wall_seconds']:.3f} 秒，未计入算法时间。",
        f"本轮主问题规模：{raw['variables']:,} 个变量、{raw['constraints']:,} 条约束（线性加指示约束）。原生状态为 {raw['native_status']}；是否返回有限原生下界：{raw['finite_native_bound_available']}。",
        '若没有有限原生界，保留的下界只来自必要箱池和固定费用，不能把它写成主问题已求得的界；也不能把阶段限时理解为不可行。当前记录不能证明中规模精确求解已经变成秒级。', '',
        '原始 summary.json 的 lower_bound_source 字段按主问题结果文件是否存在赋值，因而在“主问题限时但尚无有限界”的情况下标签不准确。保留原始文件与日志；summary_reviewed.json 和 metadata_review.json 将来源明确更正为必要箱池下界，所有数值、耗时、状态保持原样。', '',
        '## 验证与尝试记录', '',
        f"所选专项最终结果：{totals['passed']} passed、{totals['failures']} failed、{totals['errors']} errors、{totals['skipped']} skipped；没有运行全仓库测试。精确运行均通过项目运行器预检，Gurobi 11.0.3 实际整数求解成功。详细测试范围见 validation.json。",
        '探索目录 m8_medium_label_acceleration_20261029_v1 保留了两种 Python 标签试跑、编译参考试跑及全部初次测试记录。Numba 导入的跳过、首次编译/解码/语法错误，以及两次超时测试夹具未覆盖无界情况的失败均未冒充通过；修正后的验证记录另存。',
        'm8_medium_optimized_20261029_v1 是阶段上限诊断：完整枚举和装配成功，但主问题 60 秒阶段限时尚无有限下界，驱动曾将其标成 ERROR。本轮保留该记录，修正了限时处理，并把建模后全部剩余预算交给主问题重新运行。',
        f"本轮冻结输入和源码在运行后全部一致：{row['original_sources_and_inputs_unchanged']}。", '',
        '证据：summary.json、input_only_bound.json、comparison.json/csv、controller.log、native_routes/、trip_library.pkl.gz、independent_route_audit.json、independent_final_audit.json、validation.json、manifest.json 和 frozen_sources_inputs.zip。二进制与 pickle 仅用于已校验的本地实验文件，不接收不可信输入。', '',
    ]
    (HERE / 'report.md').write_text('\n'.join(text), encoding='utf-8')
    evidence = {p.relative_to(HERE).as_posix():sha(p) for p in HERE.rglob('*') if p.is_file()
                and '__pycache__' not in p.parts and p.name != 'evidence_manifest.json'}
    save(HERE / 'evidence_manifest.json', dict(hashes=evidence))
    print(dict(status=row['status'], enumeration_seconds=row['enumeration_seconds'],
               total_seconds=row['total_wall_seconds'], lower=row['global_lower_bound'],
               upper=row['audited_upper_bound'], tests=totals))


if __name__ == '__main__':
    main()
