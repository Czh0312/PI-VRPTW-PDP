"""Save the final disposition, validation scope and checksums of experiment evidence."""
import json
import shutil
from pathlib import Path
from xml.etree import ElementTree
from bench import HERE, ROOT, read, save, sha

analysis = read(HERE/'analysis.json')
final = analysis['phases']['allocation_validation']
publication = read(HERE/'publication.json') if (HERE/'publication.json').exists() else None
validation = None
if (HERE/'validation.xml').exists():
    tree = ElementTree.parse(HERE/'validation.xml')
    suites = list(tree.getroot().iter('testsuite'))
    validation = {key: sum(int(s.get(key, 0)) for s in suites)
                  for key in ('tests', 'failures', 'errors', 'skipped')}
    validation['passed'] = validation['tests']-sum(validation[key] for key in ('failures', 'errors', 'skipped'))
    validation['scope'] = [
        'tests/routing/test_model8_scout_allocation.py',
        'tests/routing/test_count_budget_recycling.py',
        'tests/routing/test_solver_isolation.py::test_solver_runs_without_importing_the_other_algorithm[alns]',
    ]
    assert not any(validation[key] for key in ('failures', 'errors', 'skipped'))
    save(HERE/'validation.json', validation)
if publication:
    assert validation and publication['variant'] == 'candidate'
    assert sha(ROOT/publication['changed_algorithm_files'][0]) == publication['after_sha256']
    assert publication['all_other_model8_alns_source_files_match_baseline']

before, after = final['variants']['baseline'], final['variants']['candidate']
lines = ['# 模型八 ALNS 小规模搜索优化：单种子验证', '',
    '本轮只使用实验根种子 **42**。9 个七单算例保持原始 K=1…7、物理时钟、同一目标函数。'
    '每版每例 20 秒墙钟预算，串行执行并交替先后顺序，冷启动，不读历史方案或参考最优值。'
    '搜索内部原有的重访子种子派生规则保持不变。', '',
    '参考最优费用仅在搜索结束后的 `analyze.py` 中用于计算 gap=(ALNS费用−最优费用)/最优费用。'
    '参考来自此前经审计的全 K 证明；该证明曾复用历史可行上界，因此本文不拿其耗时与 ALNS 冷启动耗时作对比。', '',
    '## 结果与发布决定', '',
    ('已发布轻量候选，仅修改小规模实例的前期 K 时间分配。' if publication else
     '本轮候选尚未发布，保留此前默认算法。'), '',
    '| 指标 | 原版 | 轻量候选 |', '| --- | ---: | ---: |']
for label, key, percent in [
    ('平均最优差距', 'mean_gap_percent', True),
    ('中位最优差距', 'median_gap_percent', True),
    ('最差最优差距', 'worst_gap_percent', True),
    ('达到已知最优值的算例数', 'known_optimum_hits', False),
    ('差距不超过 1% 的算例数', 'within_one_percent', False),
    ('平均实际墙钟秒数', 'mean_wall_seconds', False),
    ('平均实际 CPU 秒数', 'mean_cpu_seconds', False),
]:
    if key in ('known_optimum_hits', 'within_one_percent'):
        a, b = f'{before[key]}/9', f'{after[key]}/9'
    else:
        suffix = '%' if percent else ''
        a, b = f'{before[key]:.4f}{suffix}', f'{after[key]:.4f}{suffix}'
    lines.append(f'| {label} | {a} | {b} |')
lines += ['', f"配对结果：{final['better']} 例改善、{final['tie']} 例持平、{final['worse']} 例退步。"
          '两版 18/18 个解均通过完整独立业务审计和两周期回放。ALNS 仅返回可行解，不输出全局最优证明。', '',
          '| 算例 | 已知最优费用 | 原版费用 | 候选费用 | 原版 gap | 候选 gap | 原版 K/N | 候选 K/N |',
          '| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |']
for row in final['pairs']:
    lines.append(f"| {row['case']} | {row['exact']:.6f} | {row['baseline']:.6f} | {row['candidate']:.6f} | "
                 f"{row['baseline_gap_percent']:.3f}% | {row['candidate_gap_percent']:.3f}% | "
                 f"{row['baseline_K']}/{row['baseline_N']} | {row['candidate_K']}/{row['candidate_N']} |")
lines += ['', '## 改动及其原因', '',
    '旧策略给三个最小 K 的前期权重为 4:1:1，20 秒总预算下，前三个 K 的理想初始分配为 '
    '6.67、1.67、1.67 秒。单批次没有可移动的日历边界，多批次还需要同时构造分批、路线、边界和回收截点，'
    '很短的前期时间可能让其以尚未优化的高费用参加排名。', '',
    '轻量候选对不超过 9 单的实例改用“单批次权重 1、多批次权重 2”，K=1/2/3 时理想分配为 2、4、4 秒。'
    '实际分配会回收提前返回的时间，并计入构造、复制、内部审计的开销。'
    '其他 K 仍进入探索阶段，完整 K 域保留，后续仍改进已审计的最好可行候选。'
    '更大实例、候选数不超过 3、仅构造，以及显式 recycle/single_pass 策略沿用原路径。'
    '此前已经启用的小实例单次完整审计加速保留。', '',
    '## 探索阶段与未启用的方案', '',
    '探索过按时间窗分组的非均衡初解、低箱池初解的额外路线精修、跨车辆整趟合并、'
    '整站点延迟回库与 E1 路序的联合移动。它们不依赖精确算法，但完整解码开销会占用短预算。'
    '复杂候选在部分算例有收益，另一些退步，故未写入默认算法。'
    '`sources/temporal`、`sources/joint`、`sources/reuse` 保留这些实验版本。', '',
    '| 阶段 | 候选 | 运行数 | 改善/持平/退步 | 原版平均 gap | 候选平均 gap |',
    '| --- | --- | ---: | --- | ---: | ---: |']
for phase, data in analysis['phases'].items():
    lines.append(f"| {phase} | {data['contender']} | {data['runs']} | "
                 f"{data['better']}/{data['tie']}/{data['worse']} | "
                 f"{data['variants']['baseline']['mean_gap_percent']:.4f}% | "
                 f"{data['variants'][data['contender']]['mean_gap_percent']:.4f}% |")
lines += ['', '最终性能表仅采用 `allocation_validation` 的九例配对，不按算例挑选不同阶段的较好结果。'
          '此前探索均为同一根种子，不能当作独立随机重复。`final` 是复杂候选的阶段名，并非发布版本。', '',
          '直接构造/局部精修诊断单独保存在 seed_diagnosis 和 probe 文件中，不计入上述 ALNS 性能对照。'
          '早期直接诊断未逐次冻结源码，之后原型有所修改，因此不能把它们当作当前原型的严格复现实验。'
          '`prototype_tests/` 仅是未运行、未发布的原型测试草稿，不计入通过数量。', '',
          '## 验证范围与结论边界', '']
if validation:
    lines += [f"定向回归：{validation['passed']} 通过，{validation['failures']} 失败，"
              f"{validation['errors']} 错误，{validation['skipped']} 跳过。使用项目运行器及严格禁止跳过的测试模式，"
              '预检通过 Python 接口、Gurobi 11.0.3 许可证初始化和实际整数优化。测试范围：', '']
    lines += [f'- `{path}`' for path in validation['scope']]
    lines += ['', '这是所列范围的验证，不是整个仓库全套测试。']
lines += ['', '本轮没有验证多种子稳定性，不能据此承诺每例更快达到最优值。'
          '墙钟截止使搜索路径还受实际可用 CPU 时间及迭代数量影响，即使根种子相同，重复运行也可能不同。'
          '本轮完整保留 CPU 时间、迭代数、逐 K 预算、费用和改进历史，未只报告一次最好的结果。'
          '两版实际 CPU 时间并不完全一致，当前结果不足以分离搜索策略与系统负载各自的贡献。', '',
          '## 证据', '',
          '`analysis.json` 包含所有阶段逐例结果、汇总、参考费用文件哈希；各阶段 `*_plan.json` 固定输入、'
          '源码、驱动、预算和运行顺序。`runs/` 包含原始解、独立审计与摘要，`logs/` 保存工作进程输出。'
          '`frozen_inputs/` 保存本轮输入副本，`evidence_manifest.json` 保存证据文件 SHA-256。']
(HERE/'report.md').write_text('\n'.join(lines)+'\n', encoding='utf-8')

frozen = {}
for path, digest in read(HERE/'inputs.json').items():
    original = Path(path)
    assert sha(original) == digest
    relative = original.relative_to(ROOT)
    target = HERE/'frozen_inputs'/relative
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(original, target)
    assert sha(target) == digest
    frozen[relative.as_posix()] = digest
save(HERE/'frozen_input_hashes.json', frozen)
review_sources = {}
for path in [
    'models/routing/model_8/alns/k_search/scheduler.py',
    'models/routing/model_8/alns/README.md',
    'tests/routing/test_model8_scout_allocation.py',
    'tests/routing/test_count_budget_recycling.py',
    'tests/routing/test_solver_isolation.py',
]:
    original = ROOT/path
    target = HERE/'validated_sources'/path
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(original, target)
    review_sources[path] = sha(original)
save(HERE/'validated_source_hashes.json', review_sources)
files = {p.relative_to(HERE).as_posix(): sha(p) for p in HERE.rglob('*')
         if p.is_file() and '__pycache__' not in p.parts and p.suffix != '.pyc'
         and p.name != 'evidence_manifest.json'}
save(HERE/'evidence_manifest.json', dict(files=files, root_seeds=[42],
     disposition='published_candidate' if publication else 'unchanged_default',
     source_variant='candidate' if publication else 'baseline'))
assert all(sha(HERE/path) == digest for path, digest in files.items())
print(json.dumps(dict(evidence_files_verified=len(files), inputs_verified=len(frozen),
                     publication=publication, validation=validation), ensure_ascii=False))
