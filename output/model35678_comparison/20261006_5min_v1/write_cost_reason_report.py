"""Export verified cost attribution as Markdown and a scientific figure."""
import json
import math
from pathlib import Path

OUT=Path(__file__).resolve().parent
DEST=OUT/'cost_analysis'
data=json.loads((DEST/'analysis.json').read_text(encoding='utf-8'))
cases=data['cases']
audits=list((DEST/'references').glob('*/*/audit.json'))
assert len(audits)==36
for path in audits:
    audit=json.loads(path.read_text(encoding='utf-8'))
    assert audit['valid'] and audit['periodic_replay']['valid']
for row in cases:
    assert math.isclose(sum(row['component_savings_vs_m3'].values()),row['savings_vs_original_m3'],abs_tol=1e-6)
    assert math.isclose(sum(row['component_savings_vs_m5'].values()),row['savings_vs_original_m5'],abs_tol=1e-6)

lines=['# 大规模成本差距的原因分析','',
    '基于原注册的六例、单种子42、300秒方案分析。所有完整周期为780分钟；价格参数保持原共同哈希。原始试验方案和费用没有改写。','',
    '主要观察：相对单批次模型3，六例本轮联合模型最低费用方案的优势主要来自占用工时，而非箱池；其中五例距离费用反而高于模型3。相对固定三批次模型5，距离和工时共同降低，批次合并减少了重复供货、回收线路的费用。','',
    '当前模型3、5以距离为目标，模型6–8以完整周期成本为目标；同一个批次数不等于同一套路线、车数、任务顺序、发车时刻或回收分界。一个批次也不等于车辆只跑一趟。','',
    '## 第一组50点：M3与M7的原始差距','',
    '| 项目 | M3 | M7 | M3费用减M7费用（正数为节省） |',
    '|---|---:|---:|---:|',
    '| 箱池 | 390.00 | 390.00 | 0.00 |',
    '| 距离 | 839.55 | 873.32 | -33.77 |',
    '| 占用工时 | 864.55 | 485.98 | 378.57 |',
    '| 车辆 | 95.00 | 75.00 | 20.00 |',
    '| 趟次 | 19.00 | 18.00 | 1.00 |',
    '| 清洗、追踪、磨损 | 39.00 | 39.00 | 0.00 |',
    '| 总成本 | 2247.10 | 1881.30 | 365.80 |','',
    '工时指各车各趟的占用分钟之和，包括路线内等待，不能当成完整周期长度。M3总占用8645.53车分钟，M7为4859.80车分钟；单价同为0.1。M3一级车2台、二级车15台，M7分别为1台、13台。M3一级车固定等到19:55才回收，M7在18:26:17的优化分界后回收。','',
    '## 六例相对M3的原始费用分解','',
    '| 算例 | 本轮联合最低 | 距离节省 | 工时节省 | 车辆节省 | 趟次节省 | 箱池节省 | 总节省 |',
    '|---|---|---:|---:|---:|---:|---:|---:|']
for r in cases:
    d=r['component_savings_vs_m3']
    lines.append(f'| {r["case"]} | M{r["joint_winner"]} | {d["distance"]:.2f} | {d["work"]:.2f} | {d["vehicles"]:.2f} | {d["trips"]:.2f} | {d["pool"]:.2f} | {r["savings_vs_original_m3"]:.2f} |')
lines += ['', '## 需要统一的发车后处理','',
    '冻结源码显示，联合模型的 `JointEvaluator` 会对每个候选调用 `_delay_departures`，把到第一位客户服务前的等待移到前置仓；模型3、5的距离求解没有同样调用。相同单价并不足以消除这种方案处理差异。当前成本按占用路线时间计费，仓内等待不计入路线时间，所以只调整发车时刻也可以降低计价。它没有减少客户的使用时间，也没有改变订单时间窗。','',
    '为量化影响，对30个原方案统一执行该后处理。所有路线、客户服务时刻、回程时刻、箱池、车辆编号和分界保持原值，只有二级车出发时间推迟；30个派生方案全部独立重放两个周期通过。模型6–8费用不变，模型3六例降低99.48–213.20，模型5降低16.51–48.31。','',
    '这项处理消除了相对原M3总节省的23.90%–59.50%。相对M3的逐例降幅从原来的8.03%–16.28%变为统一发车处理后的3.42%–12.42%。因此原始降幅不能全部归因于模型结构。','',
    '| 算例 | 原M3 | M3统一发车处理 | 原M5 | M5统一发车处理 | 联合最低费用 | 相对调整后M3降低 |',
    '|---|---:|---:|---:|---:|---:|---:|']
for r in cases:
    lines.append(f'| {r["case"]} | {r["original_m3"]:.2f} | {r["m3_delayed_departures"]:.2f} | {r["original_m5"]:.2f} | {r["m5_delayed_departures"]:.2f} | {r["joint_cost"]:.2f} | {r["after_departure_normalization_reduction_percent"]:.2f}% |')
lines += ['', 'M5进一步按原时间表审计的最低箱池计价后，联合最低方案的逐例优势仍为14.41%–29.80%。这仍是方案比较，非全局最优或总体收益证明。','',
    '## 单独提前M3分界的诊断参考','',
    '为区分分界与线路变化，另对6个M3调整后方案把分界提前到其最后一趟二级空箱回前置仓时刻，一级回收事件及回库时刻平移相同时间，其余路线、客户事件、二级返回时刻和箱池不变。六例均独立双周期审计通过。这会改变M3原本固定分界的假设，因此只能列为诊断参考，不能替换原M3。','',
    '| 算例 | 已统一发车的M3 | 再提前分界的参考 | 分界单独节省 | 联合最低费用 |',
    '|---|---:|---:|---:|---:|']
for r in cases:
    lines.append(f'| {r["case"]} | {r["m3_delayed_departures"]:.2f} | {r["m3_advanced_cutoff"]:.2f} | {r["m3_cutoff_only_saving"]:.2f} | {r["joint_cost"]:.2f} |')
lines += ['',
    '第一组50点原M3与M7差365.80，其中统一发车处理153.03、保持路线仅提前分界17.74、余下方案差距195.03。余下差距涉及线路、任务排序、车辆数、路线内等待及搜索结果，不能解释成箱池节省。其他案例分界参考可能与联合方案分界不同，这些诊断步骤不能当作严格的全局因果分解。','',
    '## M5与M6–8，以及M6–8之间的解释','',
    '模型5仍固定三批，不属于同一批次结构。以第一组50点M5与M7为例，791.70的总节省包括距离658.06、占用工时81.64、车辆45和趟次7；一级趟次从6降为1，一级距离费用从823.53降为225.61。箱池费用均390，箱池并未贡献这次差距。','',
    'M6–8真正K=1时日历可行域的区别消失，成本差主要反映启发式路线和调度搜索结果，不能用本轮排名证明M7独立分界或M8不等长批次的机制收益。M8有一例K=2仅一批配送，仍保留空段导致的供货起点变化，不能完全等同于K=1。','',
    '## 结论的范围','',
    '当前没有发现成本公式使用错误箱池的证据，但发现了需要对齐的发车后处理。原结果可作为各实现输出的方案记录；要作为模型结构优势比较，应同时报告统一后处理的基准结果。原始固定分界模型与可优化分界模型应继续区别标注。','',
    '本分析不是新的ALNS试验，没有重跑或重新优化路线。派生方案均沿用原共同费用参数且通过独立双周期重放。费用为合成单位，工时收益依赖当前按占用路线时间计价的规则，并非全天固定工资的节省。只有一个随机种子，没有最优性证明。','',
    '数据：analysis.json；派生方案、审计和费用：references/；生成脚本位于上级目录。','',
    '![费用差距及统一处理的影响](cost_references.png)','']
(DEST/'report.md').write_text('\n'.join(lines),encoding='utf-8')

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
plt.rcParams.update({'font.family':'DejaVu Sans','font.size':10,'axes.spines.top':False,'axes.spines.right':False})
fig,axes=plt.subplots(2,3,figsize=(16,9))
for ax,index in zip(axes.flat,[0,2,4,1,3,5]):
    r=cases[index]
    values=[r['original_m3'],r['m3_delayed_departures'],r['m3_advanced_cutoff'],r['joint_cost']]
    ax.bar(range(4),values,color=['#446e9a','#88aaca','#a8c4b3','#dd9745'],width=.65)
    for x,value in enumerate(values):
        ax.annotate(f'{value:,.1f}',(x,value),xytext=(0,5),textcoords='offset points',ha='center',fontsize=9)
    ax.set_xticks(range(4),['M3\noriginal','M3\ndelayed','M3\nearlier cutoff',f'Best joint\nM{r["joint_winner"]}'])
    ax.set_title(r['case'].replace('zhou_','').replace('_subset50','')+f'\n{r["customers"]} customers')
    ax.set_ylabel('Synthetic cost units per cycle')
    ax.set_ylim(0,max(values)*1.18)
    ax.grid(axis='y',alpha=.18); ax.set_axisbelow(True)
fig.suptitle('Why costs differ: original results and independently audited schedule references',fontsize=15)
fig.text(.5,.025,'All use the same economic profile. References retain routes; earlier cutoff changes the fixed-M3 calendar assumption. No optimality proof.',ha='center',fontsize=10,color='#555555')
fig.tight_layout(rect=(0,.08,1,.94))
fig.savefig(DEST/'cost_references.png',dpi=180)
fig.savefig(DEST/'cost_references.svg')
plt.close(fig)
print('VERIFIED 36 derived references; report and figures exported.')
