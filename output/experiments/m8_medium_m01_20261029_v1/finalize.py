"""Package the actual result and selected validation counts."""
import csv
import xml.etree.ElementTree as ET

from probe import HERE, read, save, sha


def main():
    row = read(HERE / 'summary.json')
    changes = read(HERE / 'source_changes.json')
    manifest = read(HERE / 'manifest.json')
    changed = {item['path'] for item in changes}
    relevant_unchanged = all(sha(path) == digest for path, digest in manifest['hashes'].items() if path not in changed)
    assert relevant_unchanged
    assert all('/model_3/exact/' in path.replace('\\', '/') for path in changed)
    save(HERE / 'evidence_review.json', dict(relevant_M8_sources_and_all_inputs_unchanged=True,
        all_repository_sources_unchanged=row['original_sources_and_inputs_unchanged'],
        unrelated_model3_changes=changes,
        interpretation='No master or follower was invoked; changed M3 exact files do not implement the M8 trip enumeration, necessary pool bound, loading or audit.'))
    counts = dict(tests=0, failures=0, errors=0, skipped=0)
    scopes = []
    for name, scope in [('validation_setup.xml', 'test_probe.py'), ('validation_evidence.xml', 'test_evidence.py')]:
        root = ET.parse(HERE / name).getroot()
        for suite in root.iter('testsuite'):
            for key in counts:
                counts[key] += int(suite.get(key, 0))
        scopes.append(scope)
    counts['passed'] = counts['tests']-counts['failures']-counts['errors']-counts['skipped']
    save(HERE / 'validation.json', dict(scopes=scopes, **counts, full_repository_suite=False))
    with (HERE / 'results.csv').open('w', encoding='utf-8-sig', newline='') as stream:
        fields = [key for key, value in row.items() if not isinstance(value, (dict, list))]
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction='ignore')
        writer.writeheader()
        writer.writerow(row)
    outcome = ('完成全域最优性证明' if row['proven_optimal'] else '预算内未完成全域最优性证明')
    lines = [
        '# 模型八中规模 M01 单例计时', '',
        f"2026-10-29。20 位顾客、2 个前置仓；{outcome}。实际算法墙钟 {row['total_wall_seconds']:.3f} 秒，名义总预算 600 秒。", '',
        '| 项目 | 结果 |', '|---|---|',
        f"| 状态 | {row['status']} |",
        f"| 原始批数域 | K=1～20 |",
        '| 原始时间精度 | 1 秒 |',
        f"| 已审计可行成本 U | {row['audited_upper_bound']:.9f} |",
        f"| 当前有效下界 L | {row['global_lower_bound']:.9f} |",
        f"| (U−L)/U | {100*row['gap_over_upper']:.3f}% |",
        f"| 已有/最终方案批数与箱池 | K={row['selected_K']}，N={row['selected_N']} |",
        f"| 完整车次库枚举秒 | {row['enumeration_seconds']:.3f} |",
        f"| 完整车次库生成完毕 | {row['complete_route_library']} |", '',
    ]
    if not row['complete_route_library']:
        progress = row['last_enumeration_progress']
        lines += [
            f"截止预算时已检查 {progress['prefixes']:,} 个路线前缀，发现 {progress['columns']:,} 个任务集合，正在处理前置仓 {progress['satellite']}。完整枚举尚未结束，没有进入路线主问题或完整子问题优化。", '',
            '部分路线集合不能覆盖原问题全部选择，因此没有把它用于全局下界或最优性结论。表中的 L 仅来自必要箱池下界和固定费用，运输费用全部放松；该百分比是保守上下界差距，不是一次已求解完整 MILP 的原生 MIPGap。', '',
        ]
    else:
        lines += [f"完整库包含 {row['route_columns']:,} 列，枚举 {row['enumerated_prefixes']:,} 个前缀。阶段数据见 summary.json、input_only_bound.json（若已生成）及原生日志。", '']
    lines += [
        '本轮沿用最新七单快解的路线库、同步加强主问题和完整原业务子问题方法。为允许本次中规模试跑，只在独立实验副本中把研究脚本的 9 单保护上限改为 20 单，并增加枚举截止时间与进度输出；数学约束未删减，生产默认求解器和原始数据没有修改。', '',
        '同小规模计时口径一样，复用一份已有可行方案作上界，重新审计费用和两周期回放；没有复用历史下界、缩域或车次库，没有提供路线初解。历史方案来自 K=1～4、整分钟边界的子域；本轮仅重设为原始域的日历元数据，所有业务决策不变，五类物理输入逐字节一致，并在原始 K=1～20 域重新通过审计。', '',
        '总耗时包含输入重载、审计、完整枚举尝试、已经实际执行的建模和优化、最终审计及主要写盘；不包含历史可行方案寻找、实验副本准备、解释器启动、环境预检、专项测试和报告归档。单线程、种子 42、一次试跑；不能据此推断所有中规模算例的耗时。', '',
        f"专项验证范围：{', '.join(scopes)}，{counts['passed']} passed、{counts['failures']} failed、{counts['errors']} errors、{counts['skipped']} skipped。未运行全仓库测试。项目运行器预检 READY，Python 3.11.3、Gurobi 11.0.3，真实整数优化成功。", '',
        '运行前冻结的原输入、M8 枚举及审计相关代码哈希在运行后全部一致。运行期间另外一项工作改动了模型三精确求解的 milp_solver.py 和 route_partition.py；全仓库哈希一致标志因此如实保留为 False。差异详见 source_changes.json 和 evidence_review.json；这两个文件不负责本次 M8 枚举、必要箱池下界或审计，本次也未进入主/子问题求解。', '',
        '证据：manifest.json、frozen_sources_inputs.zip、controller.log、summary.json、results.csv、physical_bound.json、audit.json、independent_final_audit.json、validation.json。运行命令为通过 tools/run.ps1 调用本目录 probe.py run；同一次计时拒绝覆盖。', '',
    ]
    (HERE / 'report.md').write_text('\n'.join(lines), encoding='utf-8')
    hashes = {p.name: sha(p) for p in HERE.iterdir() if p.is_file() and p.name != 'evidence_manifest.json'}
    save(HERE / 'evidence_manifest.json', dict(hashes=hashes))
    print(dict(status=row['status'], seconds=row['total_wall_seconds'], optimal=row['proven_optimal'], validation=counts))


if __name__ == '__main__':
    main()
