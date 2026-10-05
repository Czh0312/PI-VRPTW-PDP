"""Close a rejected pilot, preserving all raw records and cancelled work."""
from pathlib import Path
import json
import re
from collections import Counter
from experiment import OUT,sha,write,now,verify

ROOT=OUT.parents[2]
verify()
assert not (OUT/'running.lock').exists()
assert not (OUT/'final_manifest.json').exists()
comparison=json.loads((OUT/'pilot_comparison.json').read_text())
assert not comparison['eligible'] and comparison['valid_pairs']==8
checks={}
for phase,n in [('verification',16),('unaffected',12),('pilot',16)]:
    rows=json.loads((OUT/(phase+'_fresh_decode.json')).read_text())
    checks[phase+'_fresh']=len(rows)==n and all(r['valid'] and r['equal'] for r in rows)
    runs=json.loads((OUT/phase/'summary.json').read_text())
    checks[phase+'_complete']=len(runs)==n and all(r['returncode']==0 and r['valid'] and r['periodic'] for r in runs)
latest=json.loads((OUT/'latest_fresh_audit.json').read_text())
checks['latest44']=latest['passed'] and len(latest['checks'])==44
for rel,h in json.loads((OUT/'latest_source_manifest.json').read_text()).items():
    checks['latest/'+rel]=sha(OUT/'latest_source'/rel)==h
checks['targeted_tests']='258 passed' in (OUT/'targeted_tests.log').read_text()
checks['new_tests']='32 passed' in (OUT/'candidate_tests.log').read_text()
baseline=json.loads((OUT/'before_source_manifest.json').read_text())
protected=['models/routing/solvers/alns/single_batch.py','models/routing/solvers/alns/single_batch_descent.py',
           'models/routing/solvers/alns/single_batch_scoring.py','tests/routing/test_single_batch_late_pickup.py']
for rel in protected:checks['owned/'+rel]=sha(ROOT/rel)==baseline[rel]
checks['no_production_helper']=not (ROOT/'models/routing/solvers/alns/single_batch_soft_window.py').exists()
drift={rel:dict(frozen=h,current=sha(ROOT/rel) if(ROOT/rel).exists() else None)
       for rel,h in baseline.items() if not(ROOT/rel).exists() or sha(ROOT/rel)!=h}
write(OUT/'production_preservation.json',dict(owned_unchanged={p:checks['owned/'+p] for p in protected},external_drift=drift))
assert all(checks.values()),[k for k,v in checks.items() if not v]

mechanism=[];total=Counter()
for index in range(8):
    raw=json.loads((OUT/'pilot'/('%03d_after_source.json'%index)).read_text());info=raw['result']['info']
    fields={k:info[k] for k in ('soft_window_eligible_scores','soft_window_accepted_infeasible',
            'soft_window_entered_from_feasible','soft_window_returns_to_feasible','soft_window_infeasible_iterations')}
    total.update(fields)
    mechanism.append(dict(index=index,job=raw['job'],iterations=info['iterations'],
          infeasible_iteration_share=fields['soft_window_infeasible_iterations']/info['iterations'],**fields))
write(OUT/'mechanism_summary.json',dict(total=dict(total),runs=mechanism,
    note='Eligible scores include initialization/cache hits. Accepted infeasible counts include moves within an infeasible excursion. Entered-from-feasible and recovery count transitions; infeasible iterations include noops. Not independent samples or a causal attribution of quality.'))
summary={r['stratum']:r for r in comparison['summary']}
failed=[name for name,row in summary.items() if not row['eligible']]
write(OUT/'decision.json',dict(time=now(),status='REJECTED_PILOT_QUALITY_GATE',adopted=False,
    failed_strata=failed,parameters_retuned=False,confirmation_status='CANCELLED_NOT_RUN',
    unchanged_gate=True,production_changed=False))
planned=json.loads((OUT/'confirmation_manifest.json').read_text())
assert not (OUT/'confirmation').exists()
write(OUT/'confirmation_cancelled.json',dict(time=now(),reason='Pilot failed registered quality gate; no subset adoption or retuning.',planned_pairs=len(planned['jobs']),planned_runs=2*len(planned['jobs']),actual_runs=0))

report=ROOT/'docs/alns_single_batch_soft_window_20261005.md'
assert not report.exists()
base='../output/experiments/alns_single_batch_soft_window_20261005/'
table='\n'.join('| %s | %+.4f%% | %d/%d | %+.4f%% | %s |' %
    (label,summary[name]['mean'],summary[name]['wins'],summary[name]['pairs'],summary[name]['worst'],
     '通过' if summary[name]['eligible'] else '未通过')
    for name,label in [('short','短30秒，50/100合计'),('short50','短30秒，50顾客'),
                       ('short100','短30秒，100顾客'),('long','长60秒，100顾客')])
text=f'''# 2026-10-05：M3 有界时间窗罚分替换试验

## Material Passport

- Origin Skill：academic-research-suite / experiment-agent。
- Origin Mode：Continuous run + descriptive validation；用户要求继续优化。
- Verification Status：VERIFIED，限源文件封存、正确性/复现、新鲜解码及独立审计。候选未通过登记质量门槛。

**结论：该规则确实允许搜索进入轻微不可行状态并恢复可行，但本轮质量门槛未全部通过，整体拒绝。没有修改生产求解器，没有按规模或预算挑选子集采用。60/120秒确认阶段按原规则取消，没有运行或调参重试。**

## 1. 动机与唯一候选

[上一轮同状态诊断](alns_single_batch_same_state_diagnostic_20261005.md)中，67,052次不可行提案从可行状态出发时，原百万级罚分使其退火接受概率在浮点计算中为零。追加非负罚分不能打开这类路径，因此本轮测试替换一类罚分，而非追加罚分。

仅固定M3启用：`model_number==3`且不存在自由分仓搜索。对于唯一解码违反类型为 `E2_TIME_WINDOW`、完整距离有限、每处晚到均不超过300秒的中间解，将搜索分数改为：

`完整一层＋二层距离 + 10 × 总晚到分钟数`。

晚到量覆盖二层出发超过周期结束、客户服务开始超过原解码截止值，以及回仓超过周期结束减周转时间。配送截止仍取原窗口末端与供货批次截止减服务时间的较小值。输入时间单位先换算为秒，再转为分钟。

系数10（距离单位/分钟）及300秒上限是预登记的单一工程选择，没有证明最优，也没有扫参。没有改变业务时间窗、容器库存、供货/回收或车辆规则；容量、时序环、覆盖、库存、未回收等其他违反及组合继续使用原罚分。零程度、非有限或不一致的程度保留原分数。

包装器只改变搜索分数，原解码结果、违反列表及缓存原值不修改。最好解更新仍要求无解码违反且完整独立审计通过，输出仍是保存的最好可行解，而不是当前中间解。原下降候选、随机重构、lazy pickup prefix和晚期回收接受控制流程没有改写；新的评分仍可能间接改变该接受规则的比较结果。

## 2. 封存与正确性

冻结当前410个源文件和三组输入，再登记规则、种子、预算和门槛。只在候选快照修改 `single_batch.py`，新增 `single_batch_soft_window.py`及其测试；独立建立关闭选项快照。生产文件未编辑。输入只迁移绝对文件路径，业务数据和配置不变。

- **258项定向检查通过，其中包含32项新增检查**；32不是额外加在258之外。
- 新检查覆盖秒/分钟换算、300秒边界、其他违反隔离、非有限值、缓存不变性、真实不可行中间解的接受和恢复，以及搜索结束时当前解不可行也不能替换已审计的最好解。
- 两组100顾客、M1–M4，关闭选项固定500迭代 **8对/16次**，线路、分仓、目标及登记统计逐项复现。
- 启用候选时，M1/M2/M4固定500迭代 **6对/12次**仍逐项复现，策略标记关闭。
- 上述28个保存解均无缓存新鲜解码，完整独立审计、周期重放有效且目标一致。三个输入预检查通过。

证据：[预登记]({base}protocol.json)、[候选改动范围]({base}candidate_scope_verification.json)、[258项检查]({base}targeted_tests.log)、[32项新增检查]({base}candidate_tests.log)、[关闭选项复现]({base}disabled_equivalence.json)、[非目标模型复现]({base}unaffected_equivalence.json)。

## 3. 同墙钟质量结果与决定

M3短30秒：50/100顾客各三种子561、562、563；长60秒：另一100顾客来源、种子561、562。共8对16次，交替版本顺序、全部串行。两版使用相同冻结输入和公共源码；候选评分开销计入求解预算。计时期间没有并行运行本窗口测试或剖析。

距离变化按各对 `(候选/基线−1)×100%`，负值表示降距。各行相互包含，不是独立试验组。

| 分层 | 平均距离变化 | 改善/对数 | 最大单次变化 | 登记门槛 |
|---|---:|---:|---:|---|
{table}

登记要求短/长/短50/短100各层均值均≤0%，任一对退步≤5%，短至少3/6改善、长至少1/2改善；短长两档均须实际接受过有界不可行提案并恢复可行，所有解必须新鲜完整审计通过。未通过分层：{', '.join(failed)}。特别是100顾客短预算种子563退步约5.39%，已越过单次5%上限。不能由总体平均或某个好分层覆盖这一失败。

全部16个质量解的原始独立审计有效；计时结束后再无缓存解码，独立审计和周期重放全部有效、完整距离一致。质量未通过与解合法是不同结论。预登记的60/120秒、种子571–573确认阶段8对16次全部取消，实际运行0次。未启用参数网格或重新挑选种子。

证据：[全部8对CSV]({base}pilot_pairs.csv)、[分层汇总]({base}pilot_comparison.json)、[16解新鲜审计]({base}pilot_fresh_decode.json)、[拒绝决定]({base}decision.json)、[取消确认记录]({base}confirmation_cancelled.json)。

## 4. 机制观察及其边界

8次候选质量运行合计：{total['soft_window_eligible_scores']:,}次符合替换规则的评分调用；{total['soft_window_accepted_infeasible']:,}次接受有界不可行提案；{total['soft_window_entered_from_feasible']:,}次从可行进入有界不可行；{total['soft_window_returns_to_feasible']:,}次从不可行恢复到可行。评分调用包括初始化和缓存命中，接受次数也包括不可行区间内部的移动；这些计数不是独立样本或独立解码次数。

这证明本次规则打开了原来难以通过的搜索路径，并不证明目标改善由这些路径造成，也不保证路径带来总体收益。尤其不能把50顾客或某一长预算的好结果称为稳定提升。本轮没有测定最佳罚分系数、最佳上限或最佳不可行持续长度。

证据：[逐运行机制计数及不可行迭代占比]({base}mechanism_summary.json)。

## 5. 公共代码复核与收尾

生产 `single_batch.py`、`single_batch_descent.py`、`single_batch_scoring.py`和`test_single_batch_late_pickup.py`均保持本轮开始时的字节；新包装器和测试只存在实验快照，没有生产回滚或覆盖他人改动。

正式计时前已有5个冻结文件发生外部变化，涉及其他模型/精确求解；均有记录，计时两版仍使用同一个冻结上下文。收尾另存当时公共源码快照，并对本轮 **全部44个保存解**无缓存重解码：独立审计和周期重放全部有效，完整距离一致。这是最新公共上下文中的保存解复核，不是新的求解质量对照或候选集成性能测试。因为没有生产采用，没有再运行整仓回归。

机器有其他聊天工作，记录负载和CPU但未控制外部负载；不能据此把每对变化归因于罚分。沙箱内无法全局列出进程，只管理本窗口已知进程，没有终止其他聊天任务。所有本窗口运行已结束，原始日志、输入、参数、源码、好结果和坏结果全部保留；无待运行作业、无新增或重启自动化。

若继续，需对新的恢复策略或新参数重新登记，先验证其作用，再做同预算完整对照；不要直接采用本轮参数或只挑好的分层。当前没有真实最优性GAP或稳定提升结论。

统计解释检查 **11/11**：保留规模/预算分层；不以均值保证每例；公开既有嵌套几何来源；不筛掉差解；保留完整分母；不重挑种子；保留全部输出和取消记录；公开负结果；采用门槛不变；限制外部负载下的因果解释；不由距离变化推断最优性或真实GAP。

证据：[计时前外部变化]({base}external_source_drift_before_quality.json)、[生产保护记录]({base}production_preservation.json)、[最新公共上下文44解审计]({base}latest_fresh_audit.json)、[收尾检查点]({base}checkpoint.json)。
'''
report.write_text(text)
write(OUT/'checkpoint.json',dict(status='completed_rejected_soft_window_pilot',completed=now(),
      active_process=None,pending_runs=[],quality_pairs=8,quality_runs=16,total_saved_solutions=44,
      fresh_audited=44,latest_context_audited=44,adopted=False,production_changed=False,
      confirmation='CANCELLED_NOT_RUN',automation_created_or_restarted=False,
      report=str(report.relative_to(ROOT))))
(OUT/'progress.md').write_text(f'''# 本轮完成

- M3有界时间窗罚分替换：固定系数10距离单位/分钟、单处上限300秒，候选仅存在快照。
- 258项检查通过（含32项新增）；关闭选项8对、非目标模型6对固定工作量逐值复现。
- 30/60秒8对16次完整完成，未通过分层：{', '.join(failed)}，整体拒绝。
- 60/120秒确认按登记取消，0次执行；没有调参、改门槛或子集采用。
- 44个保存解在实验上下文及最新公共代码快照均新鲜完整审计有效、目标一致。
- 所有已知本窗口进程结束；无待运行任务，无自动化重启；保护生产文件、旧档案及其他聊天修改。
- 下一次须读报告后创建新的登记目录，不覆盖本轮证据或直接重启已拒绝参数。
''')
for target in re.findall(r'\]\(([^)]+)\)',text):
    if '://' not in target:checks['link/'+target]=(report.parent/target).resolve().exists()
assert all(checks.values())
write(OUT/'final_verification.json',dict(passed=True,time=now(),checks=checks,
     note='Evidence/hash checks are not a software-test count.'))
excluded={'before_source','after_source','disabled_source','latest_source','__pycache__','.pytest_cache'}
files=[p for p in OUT.rglob('*') if p.is_file() and not any(x in excluded for x in p.relative_to(OUT).parts)]
hashes={str(p.relative_to(ROOT)):sha(p) for p in sorted(files)}
hashes[str(report.relative_to(ROOT))]=sha(report)
write(OUT/'final_manifest.json',hashes)
print('Closed rejected pilot:',len(checks),'evidence checks,',len(hashes),'artifact hashes')
print(json.dumps(dict(summary=comparison['summary'],mechanism=dict(total)),ensure_ascii=False,indent=2))
