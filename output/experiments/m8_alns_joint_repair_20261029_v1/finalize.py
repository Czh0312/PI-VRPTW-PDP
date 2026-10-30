"""Archive the tested conditional rescue and all unsuccessful trial evidence."""
import shutil
from xml.etree import ElementTree
from bench import HERE, ROOT, read, save, sha

publication = read(HERE/'publication.json')
for path, digest in {**publication['algorithm_files'], **publication['test_files']}.items():
    assert sha(ROOT/path) == digest
replay = read(HERE/'replay_summary.json')
functional = read(HERE/'functional_checks.json')
assert all(row['valid'] and row['periodic_valid'] for row in functional['rows'])
tree = ElementTree.parse(HERE/'validation.xml')
suites = list(tree.getroot().iter('testsuite'))
validation = {key: sum(int(s.get(key, 0)) for s in suites)
              for key in ('tests', 'failures', 'errors', 'skipped')}
validation['passed'] = validation['tests']-sum(validation[key] for key in ('failures', 'errors', 'skipped'))
assert not any(validation[key] for key in ('failures', 'errors', 'skipped'))
validation['scope'] = [
    'tests/routing/test_model8_repair_precedence.py',
    'tests/routing/test_model8_cycle_rescue.py',
    'tests/routing/test_model8_evaluation_pool.py',
    'tests/routing/test_count_budget_recycling.py',
    'tests/routing/test_model8_scout_allocation.py',
    'tests/routing/test_model8_fast_retiming.py',
    'tests/routing/test_solver_isolation.py::test_solver_runs_without_importing_the_other_algorithm[alns]',
]
save(HERE/'validation.json', validation)

total = replay['pairs']
a, b = replay['baseline_valid'], replay['rescue_valid']
lines = ['# 模型八 ALNS：条件式时间循环补救', '',
    '**本轮没有验证出能稳定命中最优值的新搜索策略。** 已启用的改动只补救原修复中出现的时间循环；'
    '复杂初解、路线束、直接替换修复评分等方案没有设为默认。', '',
    '整个实验沿用根种子42，没有增加独立实验种子。新发布机制的核心证据是固定移除任务回放、定向回归和短时功能检查；'
    '不能把这些结果说成整个ALNS的同预算质量、运行时间或多种子稳定性已经提高。', '',
    '## 已发布的具体改动', '',
    '原插入评分可能把一个订单的回收放到同一辆车的更早趟次，而该订单的配送在更晚趟次。'
    '完整物理解码因此产生 `TIMING_CYCLE`，提案无法成为可行解。', '',
    '对不超过9单的实例，`destroy_routes` 先按原方法修复，并通过同一个 `JointEvaluator` 评价。'
    '原提案可行时直接返回；只有其因时间循环失败且预算未耗尽时，才对同一组移除订单再修复一次。'
    '补救使用所属批次供货时间估计最早配送，屏蔽同车回收早于配送的趟次，并纠正新增回收趟次的插入位置。'
    '其他车辆上的合法并行安排仍允许。', '',
    '补救方案必须通过完整业务和两周期审计才采用；补救失败或超时则返回原提案。'
    '两次修复共用原截止时间，评价通过缓存复用，仍使用原费用函数、K域和物理时间域。'
    '超过9单不增加这次补救。没有导入精确求解实现，也没有给正式冷启动搜索提供参考最优方案。', '',
    '结果和跨K访问记录的 `timing_repair_attempts`、`timing_repair_successes` 汇总补救次数，包含内部热启动。', '',
    '## 固定工作量回放：发布依据', '',
    '从九个已保存的ALNS方案各取七个订单，枚举所有两订单移除组合，得到9×21=189组固定任务。'
    '原版和补救版使用相同日历、分批、路线、移除订单与费用口径。这是确定性的候选修复回放，没有新增实验随机种子，'
    '没有使用精确方案或最优费用。', '',
    '| 指标 | 原版 | 条件式补救 |', '| --- | ---: | ---: |',
    f'| 可行修复 | {a}/{total} | {b}/{total} |',
    f'| 可行率 | {100*a/total:.2f}% | {100*b/total:.2f}% |',
    f'| 失败修复 | {total-a} | {total-b} |', '',
    f"原来可行的{replay['preserved_feasible_proposals']}组全部保留，提案SHA-256和费用逐一一致；"
    f"从时间循环中救回{replay['recovered_from_timing_cycles']}组，损失原有可行提案{replay['lost_feasible_proposals']}组。"
    f"共尝试{replay['attempts']}次补救，成功{replay['successes']}次。", '',
    '“保留原可行提案”仅指同一组固定修复输入。运行完整ALNS时，补救后的新提案会影响后续接受、算子权重和随机路径，'
    '而补救也消耗时间，因此该回放不是整个求解过程费用不退步的保证。CPU耗时已保存在回放摘要中，单次结果不作加速结论。', '',
    '## 回归和真实算例功能检查', '',
    f"定向回归：{validation['passed']}通过、{validation['failures']}失败、{validation['errors']}错误、{validation['skipped']}跳过。"
    '通过项目运行器完成，预检验证了Python接口、Gurobi 11.0.3许可证初始化和实际整数优化。选取范围：', '',
]
lines += [f'- `{path}`' for path in validation['scope']]
lines += ['', '以上不是整个仓库全套测试。新增检查覆盖配送/回收先后、所属批次供货时间、跨卫星索引、跨车辆合法并行、'
          '补救成功/失败及超时、原可行提案保留。', '',
          '另外用已发布源码对S02、S03、S05各做5秒、根种子42的真实入口功能检查，三例均通过独立业务和两周期审计，'
          '补救计数与跨K访问汇总一致。它们没有配对原版，不用于比较求解质量或命中最优值。', '',
          '| 算例 | 补救尝试 | 成功 | 审计 |', '| --- | ---: | ---: | --- |']
for row in functional['rows']:
    lines.append(f"| {row['case']} | {row['timing_repair_attempts']} | {row['timing_repair_successes']} | 业务及两周期通过 |")
lines += ['', '## 未启用的候选及完整对照', '',
    '探索了非均衡/费用感知分批、保留路线的局部修复、配套回收等待、有限路线束，以及直接替换插入评分。'
    '所有计时对照都只用根种子42，每例每版20秒，串行并交替先后，保留完整K=1…7。'
    '已知最优费用只在搜索结束后离线计算gap，未用于搜索或停止。', '',
    '| 阶段 | 候选 | 算例数 | 改善/持平/退步 | 原版平均gap | 候选平均gap |',
    '| --- | --- | ---: | --- | ---: | ---: |']
analysis = read(HERE/'analysis.json')
for phase, data in analysis['phases'].items():
    v = data['contender']
    lines.append(f"| {phase} | {v} | {len(data['pairs'])} | {data['better']}/{data['tie']}/{data['worse']} | "
                 f"{data['variants']['baseline']['mean_gap_percent']:.4f}% | {data['variants'][v]['mean_gap_percent']:.4f}% |")
direct = analysis['phases']['final']
lines += ['',
    f"直接替换前置关系评分的九例对照虽将平均gap从{direct['variants']['baseline']['mean_gap_percent']:.4f}%降到"
    f"{direct['variants']['precedence']['mean_gap_percent']:.4f}%，但有6例退步，最差gap从"
    f"{direct['variants']['baseline']['worst_gap_percent']:.4f}%变为{direct['variants']['precedence']['worst_gap_percent']:.4f}%，因此没有启用。"
    '这些数字属于 `precedence` 候选，不属于已发布的 `rescue` 机制。', '',
    '复杂候选 `candidate_v4` 曾在S03得到47.818014，距已知最优约0.12%，但S05退步。'
    '不能按算例挑不同候选的最好结果，不能据一次接近最优就声称整体有效。', '',
    '早期原型检查曾有1项失败、8项通过，暴露了保留修复仍可能产生时间循环；修正后9项通过。'
    '早期复杂精修原型还发现局部评分覆盖总费用变量的问题，已在后续原型修正。'
    '这些失败版本、检查XML和全部原始运行结果保留；它们未发布为默认算法。'
    '最终生产范围的75项回归独立记录，不能将原型检查重复累计进去。', '',
    '## 结论边界与证据', '',
    '本轮落地的是一个可验证的无效修复补救，不是“已经稳定达到精确最优值”。'
    '已发布变体尚未完成整批同预算冷启动质量对照，也没有多种子稳定性结论。'
    '先前精确验证还曾复用经审计的历史可行上界，不能直接将其计时与ALNS冷启动相除得出速度倍数。', '',
    '`publication.json`固定实际发布源码；`replay_plan.json`固定同批候选回放的源码和输入；'
    '`replay_baseline.json`、`replay_rescue.json`及摘要保留逐任务对照；`validation.xml`与`validation.json`记录定向检查。'
    '`functional_checks/`保存真实入口功能输出。各阶段 `*_plan.json`、`runs/`和`logs/`保留被否决候选的计时证据。'
    '`frozen_inputs/`保存物理输入副本，`evidence_manifest.json`记录文件SHA-256。']
(HERE/'report.md').write_text('\n'.join(lines)+'\n', encoding='utf-8')

for path, digest in read(HERE/'inputs.json').items():
    original = ROOT/path
    assert sha(original) == digest
    target = HERE/'frozen_inputs'/original.relative_to(ROOT)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(original, target)
    assert sha(target) == digest
for path in [*publication['test_files'], 'models/routing/model_8/alns/README.md']:
    target = HERE/'validated_sources'/path
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(ROOT/path, target)
files = {p.relative_to(HERE).as_posix(): sha(p) for p in HERE.rglob('*')
         if p.is_file() and '__pycache__' not in p.parts and p.suffix != '.pyc' and p.name != 'evidence_manifest.json'}
save(HERE/'evidence_manifest.json', dict(files=files, root_seeds=[42], published_variant='rescue'))
assert all(sha(HERE/path) == digest for path, digest in files.items())
print(dict(verified_evidence_files=len(files), validation=validation,
           published_variant='rescue', fixed_work_valid_before=a, fixed_work_valid_after=b))
