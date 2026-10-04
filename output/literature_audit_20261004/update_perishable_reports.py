"""Close the final priority full-text gap without changing research code or old snapshots."""
from pathlib import Path
import json

ROOT = Path(__file__).resolve().parents[2]
ABS = ROOT.as_posix()

def replace_once(text, old, new):
    assert text.count(old) == 1, (old, text.count(old))
    return text.replace(old, new, 1)

paths = {
    "full": ROOT / "docs/novelty_fulltext_comparison_20261004.md",
    "main": ROOT / "docs/novelty_overlap_audit_20261004.md",
    "reading": ROOT / "reference/novelty_fulltexts_20261004/reading_list.md",
    "satellite": ROOT / "docs/novelty_satellite_sync_20261004.md",
}
texts = {k: p.read_text(encoding="utf-8") for k, p in paths.items()}

t = texts["full"]
t = replace_once(t, "# 硕士论文创新审查：四篇近邻、清洗与卫星同步全文比较", "# 硕士论文创新审查：七篇重点全文逐项比较")
t = replace_once(t, "审查日期：2026-10-04。", "审查日期：2026-10-04；食品RTI全文补核与本报告更新完成于2026-10-05。")
t = replace_once(t, "另补读 Accorsi 等（2022）的清洗模型和新到位的 Zhou 等（2024）卫星同步出版版。", "另补读 Accorsi 等（2022）的清洗模型、Zhou 等（2024）卫星同步及Zhang等（2024）食品RTI选择出版版。")
t = replace_once(t, "涉及六篇文献的判断以本报告和[卫星同步专项核对](C:/Users/猫的秋刀鱼/Desktop/毕业设计/docs/novelty_satellite_sync_20261004.md)为准。", "涉及七篇重点文献的判断以本报告、[卫星同步专项核对](C:/Users/猫的秋刀鱼/Desktop/毕业设计/docs/novelty_satellite_sync_20261004.md)和[食品RTI专项核对](C:/Users/猫的秋刀鱼/Desktop/毕业设计/docs/novelty_perishable_rti_20261004.md)为准。")
t = t.replace("已审读六篇公开模型", "已审读七篇重点公开模型")
t = replace_once(t, "以下四点比第一轮更确定", "以下五点比第一轮更确定")
anchor = "4. 新到位的 Zhou2024 已根据二级返仓与一级离仓时刻决定当前回运资格，且有装卸、暂存和容量。不能把这种一般同步或错过当前回运的现象当首次；固定截点单独的创新性较弱，应强调其与有限池后批清洗供箱的共同作用。"
t = replace_once(t, anchor, anchor + "\n5. Zhang2024式(13)–(14)禁止当期返空用于当期填充、当期消费空箱用于当期回收。先后与跨期复用已有明确建模；其返空可直接再用，无本文路线实际回仓清洗时长和卫星归批链。食品保质期、RTI类型与利润联合优化也已存在，当前项目没有这些功能。")
anchor = "| Zhou、Li、Bian、Zhang | Computers & Operations Research 2024，167，106600；2024-03-06在线；DOI 10.1016/j.cor.2024.106600 | PDF 3–19：假设、表2–4、图3–4、式(7)–(57)、MA及实验；详细资格比较见专项报告 |"
t = replace_once(t, anchor, anchor + "\n| Zhang、Chu、Che、Li | IJPR2024，62(1–2)，501–521；2023-11-02在线；DOI10.1080/00207543.2023.2275639 | PDF5–9／504–508：表2、假设及式(1)–(26)；PDF10–12／509–511：KS；PDF12–19／511–518：案例、实验和局限 |")
anchor = "## 8. 全文之后的核心比较矩阵"
section = f"""### 7.2 新补食品RTI：Zhang2024已有明确跨期再用顺序

22页出版版已核对完整主模型及算法、案例与实验，PDF5–19的关键公式和表格经页面图像核查。式(8)把当前回收加入期末库存，但式(13)仅允许上期期末空箱与本期采购支撑本期填充；式(14)仅允许回收上期消费产生的空箱。因此“回收后支持未来供货”“先使用后回收”已有明确约束，不能被本项目重新命名为创新。

假设称返空可直接再进入供应链，没有显式清洗时长；这不等于允许当期立即复用。它采用单供应商直接服务零售商、初始零库存并可采购、每车每期至多一路线，无当前卫星固定截点和实际清洗释放链。食品年龄、多类型RTI、损失与利润目标比当前项目范围更广，不能以当前食品背景替代这些决策。[专项报告]({ABS}/docs/novelty_perishable_rti_20261004.md)含公式、KS证书边界、170例实验口径和最终贡献调整。

"""
t = replace_once(t, anchor, section + anchor)
start = t.index("| 维度 | 当前 M5 |")
end = t.index("\n\n表中有些差异", start)
extra = [
    "Zhang2024", "---",
    "供应商直达零售商，单套车辆、期级巡回",
    "本期消费空箱留待后期回收，式(14)",
    "无路线到离时钟，按期控制库存与路线",
    "无卫星层或本文截点",
    "返空直接可再用，但式(13)禁止当期返空用于当期填充；无清洗时长",
    "初始零库存、可采购；节点储存容量非固定池",
    "每车每期至多一路线",
    "期末库存延续，无全池清洁恢复",
    "食品与多类型容器、采购、库存及路径联合利润",
]
lines = t[start:end].splitlines()
assert len(lines) == len(extra), len(lines)
t = t[:start] + "\n".join(line + " " + value + " |" for line, value in zip(lines, extra)) + t[end:]
anchor = "| 首次考虑清洗、清洁库存、迟返缺箱 | 删除宽泛主张 | Guo 的清洗前提／提前期，Accorsi 的状态／能力等 |"
t = replace_once(t, anchor, anchor + "\n| 首次食品循环箱、类型保鲜选择或先使用后跨期回收 | 删除；食品质量／类型优化也不在当前实现范围 | Zhang2024主模型及式(13)–(14)，需准确界定应用背景 |")
start = t.index("## 14. 是否还需要下载文献")
end = t.index("\n## 15. 可复核记录", start)
t = t[:start] + f"""## 14. 是否还需要下载文献

**此前优先需要的两篇Zhou2024与Zhang2024均已到位，七篇重点全文的核心模型比较已经完成，暂时不需要用户继续下载新论文。** 详见[卫星同步]({ABS}/docs/novelty_satellite_sync_20261004.md)及[食品RTI]({ABS}/docs/novelty_perishable_rti_20261004.md)专项核对。后者已确认跨期复用顺序，无需再次获取它来作这个判断。

Zhou2022、Iassinovskaia2017已有公开作者稿，Charaf2024已有出版版，之前判断继续保留。原论文代码、随机输入、增强出版案例或在线算法附录只在忠实复现相应算法／实验时才是必要材料；当前优先级是紧池实验、联合／顺序对照、机制消融和政策依据。

本轮未完成CNKI、Web of Science、Scopus全库检索和全部被引全文追踪，不能作“世界上没有相同模型”的断言。七篇重点全文中未发现完整复现，是相对这些公开模型的比较结论；是否形成硕士论文贡献，仍看业务合理性、模型说明、方法实质和有限池证据。沿Zhang引用链新增的虚拟共享RTI文献仅标记出版社摘要，未计入已排除的全文近邻。
""" + t[end:]
anchor = "- 更新后的[文献到位与待补清单](C:/Users/猫的秋刀鱼/Desktop/毕业设计/reference/novelty_fulltexts_20261004/reading_list.md)。"
t = replace_once(t, anchor, f"- 本次Zhang2024的[来源记录]({ABS}/output/literature_audit_20261004/fulltexts/zhang2024_source.json)和[新增验证快照]({ABS}/output/literature_audit_20261004/perishable_rti_verification.json)另存；此前快照记录各自时点，不覆盖。\n" + anchor)
texts["full"] = t

t = texts["main"]
t = replace_once(t, "审查日期：2026-10-04。", "审查日期：2026-10-04；食品RTI全文补核与本报告更新完成于2026-10-05。")
t = replace_once(t, "已完成四篇高风险近邻、清洗与卫星同步两篇补充的全文模型比较", "已完成四篇高风险近邻、清洗、卫星同步及食品RTI共七篇重点全文模型比较")
t = replace_once(t, "沿其参考文献追加Marques2022的出版社摘要，文献条目现为20项。", "另完成Zhang2024食品RTI全文的[专项核对](novelty_perishable_rti_20261004.md)。引用链追加Marques2022和Achamrah2022的出版社摘要，文献条目现为21项；摘要条目不计为已排除的全文近邻。")
anchor = "不能把它们概括为不考虑回收时序或清洗。"
t = replace_once(t, anchor, anchor + " Zhang2024式(13)–(14)进一步确认当期返空不能支持当期填充，当期消费空箱不能当期回收；直接再用假设不等于没有跨期先后。")
t = replace_once(t, "食品RTI处理时序仍需补核；六篇全文比较不是全库原创性排除。", "Zhang2024处理时序也已核对，期级先后已有明确约束；七篇重点全文比较不是全库原创性排除。")
anchor = "- 新补Zhou2024视觉审读第3–19页，包括资格式(32)–(34)、同步式(44)–(49)、有效不等式、MA与实验；原件和哈希保留。"
t = replace_once(t, anchor, anchor + "\n- 新补Zhang2024完整主模型表2与式(1)–(26)、KS、案例和170例实验已核对，PDF5–19公式及表格视觉审读；来源与字节哈希另存。")
t = replace_once(t, "| L12 | Zhang 等，2024，食品 RTI 选择与闭环库存路径 | 食品、容器选择、库存路径、利润目标 | 出版社正式摘要 | 加食品背景或经济分析已有近邻 |", "| L12 | Zhang 等，2024，食品 RTI 选择与闭环库存路径 | 食品年龄、类型选择、库存路线、跨期先后、利润 | 用户22页出版版，表2及式(1)–(26) | 已有期级复用；直接再用、单级，无本文卸回归批与清洗释放链 |")
t = replace_once(t, "食品、多种 RTI、闭环库存路径及利润目标已有研究；本文没有做食品保鲜或容器类型选择，不应借食品背景扩大贡献。", "22页出版版表2及式(1)–(26)已核对。式(13)禁止本期返空用于本期填充，式(14)禁止本期消费空箱立即回收，已有跨期先后；返空可直接再用，无本文清洗时长、两级车交接与卫星归批链。食品年龄、多种RTI、采购、损失和利润已有实质模型，当前没有这些决策；[详细比较](novelty_perishable_rti_20261004.md)另存。")
anchor = "## 4. 核心机制的证据矩阵"
t = replace_once(t, anchor, "**L21，Achamrah等（2022）**：Sustainability14(10)，5805，DOI[10.3390/su14105805](https://www.mdpi.com/2071-1050/14/10/5805)。沿Zhang引用链核对出版社摘要，多供应商、多类型RTI库存路径与虚拟共享已有研究。未审读主模型，不能把其“two-level supply chain”自动认作卫星交接的两级车队，也不据此排除本文具体事件链。\n\n" + anchor)
anchor = "| Zhou2024出版版 | 两级双向、事件同步、时变行驶 | 一次过程＋给定旧库存，非同池后批复用 | 一次同车取送独立货物 | 按实际到离资格，非固定卸回截点 | 无RTI清洗供后批链 |"
t = replace_once(t, anchor, anchor + "\n| Zhang2024出版版 | 单供应商直达零售商的期级巡回 | 回收支持后期填充、可采购、多类型 | 当期消费空箱留待后期回收 | 无卫星层 | 返空直接再用；式(13)有期级限制，无实际清洗时长 |")
t = replace_once(t, "L3–L6及L16主模型均已核对。L16明确已存在二级返仓影响一级回运，本文需比较其与有限池清洗供箱反馈的组合。L12食品RTI时序仍待全文，不能把此表当作全球原创性证明。", "L3–L6、L16和L12主模型均已核对。L16已有二级返仓影响一级回运，L12已有明确跨期填充与回收先后；本文需比较具体资格与实际清洗释放反馈的组合。此表不是全球原创性证明。")
t = replace_once(t, "有限池决策变化、封批政策依据；L16已核，L12仍需补全文", "有限池决策变化、封批政策依据；L16与L12均已全文补核")
t = replace_once(t, "**当前四篇主模型未见本文完整耦合**", "**七篇重点全文主模型未见本文完整耦合**")
t = replace_once(t, "卫星同步全文已补核，仍待食品RTI处理时序；不继续重复下载已审读论文。", "卫星同步与食品RTI全文均已补核，当前优先材料缺口关闭；暂时不需要用户继续下载新文献。")
t = replace_once(t, "仍建议补 *Closed-loop inventory routing problem for perishable food with returnable transport items selection*（Zhang等2024），DOI [10.1080/00207543.2023.2275639](https://www.tandfonline.com/doi/full/10.1080/00207543.2023.2275639)。核对食品RTI处理与复用时序；若保留食品／生鲜背景，更值得补。", "Zhang等2024食品RTI选择22页出版版也已到位，完整主模型与实验口径核对完成。[专项报告](novelty_perishable_rti_20261004.md)及[来源记录](../output/literature_audit_20261004/fulltexts/zhang2024_source.json)保留证据。此前两个优先全文缺口均已关闭，不需要重复下载。")
t = replace_once(t, "优先级低于剩余RTI时序核对。", "并非当前研究推进的瓶颈。")
t = replace_once(t, "用户提供四篇出版版及追加Accorsi、Zhou2024的完整页码、公式和结论", "此前四篇出版版及追加Accorsi、Zhou2024、Zhang2024的完整页码、公式和结论")
anchor = "另存，保留历史快照。"
t = replace_once(t, anchor, "另存，保留历史快照。Zhang全文的[本次验证](../output/literature_audit_20261004/perishable_rti_verification.json)追加记录来源、核心哈希、560行池设置和报告链接，不覆盖旧记录。")
t = replace_once(t, "检索日为 2026-10-04，", "检索起始日为2026-10-04，最后食品RTI补核与引用链记录更新于2026-10-05，")
texts["main"] = t

t = texts["reading"]
t = replace_once(t, "日期：2026-10-04。", "审查批次日期：2026-10-04；最后更新2026-10-05。")
t = replace_once(t, "此前四篇和新到位Zhou2024出版版均完成主模型比较；另审读Accorsi2022公开出版版。", "此前四篇、Zhou2024和Zhang2024出版版均完成主模型比较；另审读Accorsi2022公开出版版，七篇重点全文到位。")
anchor = "此前四篇原路径、副本哈希、页数与提取文本见[来源记录]"
start = t.index(anchor)
table_end = t.rfind("\n\n", 0, start)
newrow = "| Zhang等2024，Closed-loop inventory routing problem for perishable food with returnable transport items selection；DOI10.1080/00207543.2023.2275639 | [22页PDF](zhang_2024_perishable_rti_selection.pdf) | 表2、式(1)–(26)，尤其(13)–(14)跨期先后；KS、案例与170例实验；[专项比较](../../docs/novelty_perishable_rti_20261004.md) |"
t = t[:table_end] + "\n" + newrow + t[table_end:]
anchor = "新补Zhou2024单独记录于[zhou2024_source.json](../../output/literature_audit_20261004/fulltexts/zhou2024_source.json)。"
t = replace_once(t, anchor, anchor + " Zhang2024原件对应关系、SHA256、页数和文本见[zhang2024_source.json](../../output/literature_audit_20261004/fulltexts/zhang2024_source.json)。")
start = t.index("## 定稿前建议补充")
end = t.index("\n## 只在复现实验", start)
t = t[:start] + "## 本次优先全文已齐\n\n此前优先补充的Zhou2024卫星同步和Zhang2024食品RTI均已收到、核对主模型及相关算法／实验，暂时不需要用户继续下载新文献。判断是针对性近邻比较，不是全库排除相同模型。Achamrah2022沿引用链仅核对出版社摘要，保留阅读深度标记，不计为已排除的全文近邻。\n" + t[end:]
texts["reading"] = t

t = texts["satellite"]
t = replace_once(t, "本次L16的全文缺口已关闭，不再需要用户重复下载它。此前建议的Zhang等2024食品与RTI选择论文（DOI [10.1080/00207543.2023.2275639](https://www.tandfonline.com/doi/full/10.1080/00207543.2023.2275639)）仍未取得全文；若保留食品背景，下一篇优先核对它的处理／再使用时序。没有完成全库被引追踪，不声称排除了所有相同研究。", "本次L16的全文缺口已关闭。2026-10-05状态更新：此前建议的Zhang等2024食品RTI全文也已到位并核对，[专项比较](novelty_perishable_rti_20261004.md)确认跨期先后已有、没有当前完整卸回归批与实际清洗释放链。两个优先全文缺口均关闭，暂时不需要用户继续下载文献。没有完成全库被引追踪，不声称排除了所有相同研究。")
texts["satellite"] = t

# Check every expected replacement before writing the four reports.
for key, path in paths.items():
    path.write_text(texts[key], encoding="utf-8")
print(json.dumps({"updated": [str(p.relative_to(ROOT)) for p in paths.values()]}, ensure_ascii=False))
