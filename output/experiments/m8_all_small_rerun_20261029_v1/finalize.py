"""Publish the nine-case repeat and its independently checked proof evidence."""
from copy import deepcopy
import csv
from pathlib import Path
import statistics
import xml.etree.ElementTree as ET
import zipfile

from study import HERE, ROOT, CASES, read, save, sha, verify


def main():
    plan = read(HERE / 'plan.json')
    verify(plan)
    execution = read(HERE / 'execution.json')
    assert len(execution['rows']) == 9 and not execution['failures']
    rows = [read(HERE / 'runs' / case / 's42' / 'summary.json') for case in CASES]
    junit = ET.parse(HERE / 'validation_evidence.xml').getroot()
    suites = list(junit.iter('testsuite'))
    counts = {key: sum(int(s.get(key, 0)) for s in suites) for key in ['tests', 'failures', 'errors', 'skipped']}
    assert counts == dict(tests=31, failures=0, errors=0, skipped=0), counts
    validation = dict(scope=['output/experiments/m8_all_small_rerun_20261029_v1/test_evidence.py'],
                      passed=31, failed=0, errors=0, skipped=0, full_repository_suite_run=False,
                      junit_sha256=sha(HERE / 'validation_evidence.xml'),
                      original_slot_cut_replay=read(HERE / 'original_slot_cut_replay.json'),
                      first_test_attempt='22 passed, 9 failed solely on tuple/list JSON representation; '
                                         'comparison normalized, then the complete 31-test selection passed. '
                                         'Original first-attempt JUnit preserved. No solver result was altered.')
    save(HERE / 'validation.json', validation)
    times = [r['total_wall_seconds'] for r in rows]
    stats = dict(cases=9, trials=9, seeds=[42], proven_optimal=sum(r['proven_optimal'] for r in rows),
                 minimum_seconds=min(times), median_seconds=statistics.median(times),
                 mean_seconds=statistics.mean(times), maximum_seconds=max(times),
                 sum_case_seconds=sum(times), driver_seconds=execution['driver_seconds'],
                 total_follower_patterns=sum(r['patterns'] for r in rows),
                 slowest_case=max(rows, key=lambda r: r['total_wall_seconds'])['case'],
                 historical_feasible_plan_used=True, historical_lower_bound_used=False,
                 cold_start_end_to_end=False, repeatability_across_seeds_established=False)
    save(HERE / 'statistics.json', stats)
    with (HERE / 'results.csv').open('w', encoding='utf-8-sig', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    certificates = []
    for row in rows:
        case = row['case']
        folder = HERE / 'runs' / case / 's42'
        result = read(folder / 'best_audited_result.json')
        certificate = dict(case=case, original_K=row['original_K'], original_constraints_preserved=True,
             audited_feasible_cost=row['audited_upper_bound'], independently_replayed_lower_bound=
             read(folder / 'independent_bound_replay.json')['bound'],
             global_optimality_certified=row['proven_optimal'], absolute_tolerance=1e-6,
             method=row['proof_method'], historical_feasible_plan_used=True,
             historical_lower_bound_used=False, proof_scope='Full original cost objective and all original K.',
             proof='A fresh valid lower-bound master covers all original feasible solutions. '
                   'Where necessary, independently recertified cost-competitive K/N extrema and complete '
                   'native pattern followers refine the bound. Matching this lower bound to an independently '
                   'audited feasible original plan establishes global optimality within absolute tolerance.',
             original_plan_native_status=result.get('info', {}).get('status'),
             evidence={p.name: sha(p) for p in folder.iterdir() if p.is_file() and p.suffix in ('.json', '.jsonl')})
        certified = deepcopy(result)
        certified['external_global_optimality_certificate'] = certificate
        save(HERE / f'certified_{case}.json', certified)
        certificates.append(certificate)
    save(HERE / 'certificates.json', certificates)
    lines = ['# 模型八：全部九个七单小规模算例重跑', '',
        '2026-10-29。当前数据集中的 M8 / small / S01–S09 全部重新运行，9/9 在 1e-6 绝对容差内完成全域最优性证明。', '',
        f'每例总耗时 {min(times):.3f}–{max(times):.3f} 秒，中位数 {statistics.median(times):.3f} 秒；'
        f'九例耗时合计 {sum(times):.3f} 秒，批次驱动墙钟 {execution["driver_seconds"]:.3f} 秒。'
        '本轮没有出现四五十分钟的长尾。', '',
        '## 本轮结果', '',
        '| 算例 | 最优成本 | 全域下界 | 总耗时/秒 | 子问题组合数 | 最优证明 |',
        '|---|---:|---:|---:|---:|---|']
    for row in rows:
        lines.append(f'| {row["case"]} | {row["audited_upper_bound"]:.9f} | {row["global_lower_bound"]:.9f} | '
                     f'{row["total_wall_seconds"]:.3f} | {row["patterns"]} | {"已证明" if row["proven_optimal"] else "未闭合"} |')
    lines += ['', '## 运行与计时口径', '',
        '统一种子 42、单线程、串行运行，每例名义预算 300 秒。原始 K=1…7、业务规则、费用和整数时钟保持原样。'
        '先冻结九例和历史可行上界，再开始批次；原生产默认入口没有改动。', '',
        '**使用了已有、重新通过审计的最好可行方案作为上界；这不是完全冷启动。**'
        '没有使用历史下界、历史最优性证书、历史缩域或车次库，也没有输入历史路线初解或变量提示。'
        '当前最优值仅作为可行成本上界或事后闭合比较，不被当作下界或目标等式输入。', '',
        '表中总耗时从每例重新加载开始，包含方案审计、完整车次库枚举、主问题构建及优化、必要的四个缩域端点证明、'
        '完整子问题与最终方案审计、主要结果写盘。历史方案寻找、冻结阶段挑选上界、Python/运行器启动和环境预检、'
        '后续独立测试及报告归档没有计入。各阶段时间见 results.csv。', '',
        '原输入全 K、全车次库的第一轮下界只依赖原始数据，没有提供 U/L 或固定 K/N。'
        '七例下界直接与已审计可行成本闭合；S03、S07 重新证明成本竞争域后，分别检查 2、4 个完整组合并闭合。'
        '同趟回收批次同步及回收不早于装载批次的必要条件沿用已验证新版。', '',
        '## 独立验证', '',
        '通过项目 tools/run.ps1 的独立测试进程，所选 test_evidence.py 最终 **31 passed、0 failed、0 errors、0 skipped**。'
        '没有运行全仓库测试。预检 READY，Python 3.11.3、Gurobi 11.0.3，实际整数优化成功。', '',
        '验证覆盖全部九例的原业务、费用与两周期回放；重新枚举完整车次库并投影原可行计划；'
        '以新种子 101 重建最终下界，不设置已知最优值下界或目标等式、也不设置目标上界；'
        '独立重证 S03/S07 的 K/N 四个端点；全部六个不同子问题组合恢复原始未裁剪车次槽位重新检验。', '',
        '首轮专项测试有 9 项因 Python 路径元组与 JSON 列表的表示差异失败，另 22 项通过；'
        '修正比较的序列化表示后，完整 31 项重新通过。首轮 JUnit 单独保留，求解代码、方案和计时均未更改。', '',
        '## 结论与限制', '',
        '本轮证据支持：新版在当前全部九个七单算例的证明阶段，消除了此前已观察到的数十分钟慢例，'
        '且把重新生成下界与车次库的准备也计入后，最长约 13.6 秒。'
        '这仍是单种子、单次批次，不证明所有种子、更多来源或更大规模都同样稳定；'
        '也不能把这些时间解释成从零寻找可行解到最终证明的完整耗时。20/50 单算例未运行。', '',
        '## 证据文件与复现', '',
        '- results.csv、statistics.json：逐例成本、上下界、阶段耗时及汇总。',
        '- plan.json：运行前冻结的原始范围、输入与源码哈希、可行方案选择、种子和预算。',
        '- runs/Sxx/s42/：新车次库、输入型全域下界、必要的缩域、子问题割、原方案、审计及独立重放。',
        '- certificates.json、certified_Sxx.json：全域成本证书；原方案的原生状态保留不变，外部证书单独附加。',
        '- validation.json、validation_evidence.xml：实际专项测试范围与最终结果。',
        '- frozen_sources_inputs.zip、evidence_manifest.json：冻结输入/源码快照和结果文件 SHA256。', '',
        '新目录复现：复制 study.py 至新的 output/experiments/ 子目录，仍保留其相对项目根的层级；'
        '通过项目运行器先 prepare 再 run。prepare 拒绝覆盖已有计划，run 拒绝覆盖不完整尝试。'
        '复用本目录执行 run 时仅跳过已完成记录，不代表又做了一次新求解。', '',
        '```powershell',
        ".\\tools\\run.ps1 python -Arguments @('output/experiments/<新目录>/study.py', 'prepare')",
        ".\\tools\\run.ps1 python -Arguments @('output/experiments/<新目录>/study.py', 'run')", '```', '']
    (HERE / 'report.md').write_text('\n'.join(lines), encoding='utf-8')
    paths = set()
    for key in ['input_hashes', 'driver_hashes', 'source_hashes']:
        for raw in plan[key]:
            target = Path(raw)
            paths.add(target if target.is_absolute() else ROOT / target)
    paths.update([HERE / 'plan.json', HERE / 'test_evidence.py', Path(__file__)])
    with zipfile.ZipFile(HERE / 'frozen_sources_inputs.zip', 'w', zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(paths, key=str):
            archive.write(path, 'snapshot/' + path.relative_to(ROOT).as_posix())
    excluded = {'evidence_manifest.json'}
    files = [p for p in HERE.rglob('*') if p.is_file() and '__pycache__' not in p.parts and p.name not in excluded]
    manifest = {p.relative_to(HERE).as_posix(): sha(p) for p in sorted(files)}
    save(HERE / 'evidence_manifest.json', dict(files=manifest, file_count=len(manifest)))
    assert all(sha(HERE / name) == digest for name, digest in manifest.items())
    print(dict(statistics=stats, validation_counts=counts, evidence_files=len(manifest)), flush=True)


if __name__ == '__main__':
    main()
