"""Update review conclusions after reading Zhou2024; retain earlier evidence snapshots."""
from pathlib import Path
import json

ROOT = Path(__file__).resolve().parents[2]

def replace_once(text, old, new):
    if new in text and old not in text:
        return text
    assert text.count(old) == 1, old[:100]
    return text.replace(old, new, 1)

full = ROOT / "docs/novelty_fulltext_comparison_20261004.md"
t = full.read_text(encoding="utf-8")
t = replace_once(t, "# 硕士论文创新审查：四篇近邻全文与当前项目逐项比较", "# 硕士论文创新审查：四篇近邻、清洗与卫星同步全文比较")
t = replace_once(t, "另沿 Guo 的参考文献补读 Accorsi 等（2022）的公开出版版。", "另补读 Accorsi 等（2022）的清洗模型和新到位的 Zhou 等（2024）卫星同步出版版。")
t = replace_once(t, "涉及这五篇文献的判断以本报告为准。", "涉及六篇文献的判断以本报告和[卫星同步专项核对](C:/Users/猫的秋刀鱼/Desktop/毕业设计/docs/novelty_satellite_sync_20261004.md)为准。")
t = replace_once(t, "全文比较后，以下三点比第一轮更确定：", "全文比较后，以下四点比第一轮更确定：")
anchor = "这是相对已读模型的具体差异，不能推广成对所有文献的首次性证明。"
t = replace_once(t, anchor, anchor+"\n4. 新到位的 Zhou2024 已根据二级返仓与一级离仓时刻决定当前回运资格，且有装卸、暂存和容量。不能把这种一般同步或错过当前回运的现象当首次；固定截点单独的创新性较弱，应强调其与有限池后批清洗供箱的共同作用。")
row = "| Accorsi、Cholette、Manzini、Mucci | Sustainable Production and Consumption 2022，31，331–345；DOI 10.1016/j.spc.2022.02.014；大学公开托管的出版版 | PDF 1–5／331–335：业务边界、图1–2、式(1)–(19)；PDF 6–10／336–340：案例及清洗服务分析 |"
t = replace_once(t, row, row+"\n| Zhou、Li、Bian、Zhang | Computers & Operations Research 2024，167，106600；2024-03-06在线；DOI 10.1016/j.cor.2024.106600 | PDF 3–19：假设、表2–4、图3–4、式(7)–(57)、MA及实验；详细资格比较见专项报告 |")
t = replace_once(t, "## 8. 全文之后的核心比较矩阵", "### 7.1 新补卫星同步：Zhou2024已使二级返仓影响一级回运\n\n已视觉审读新到位21页出版版的第3–19页。表4的g资格变量、式(32)–(34)和图3–4说明，二级返仓不晚于一级离仓的当前回收货物可以进入本次回运；式(44)–(49)表示装卸和等待。该文没有当前M5同一有限池的使用—回收—清洗—后批供箱闭环。比较应同时承认时间协调的重叠和闭环反馈的差异，不以固定截点名称不同作为主要创新。[完整专项核对](C:/Users/猫的秋刀鱼/Desktop/毕业设计/docs/novelty_satellite_sync_20261004.md)含公式解释边界、政策比较、算法和实验口径。\n\n## 8. 全文之后的核心比较矩阵")
old_start = t.index("| 维度 | 当前 M5 | 和剑波2026")
old_end = t.index("\n\n表中有些差异", old_start)
matrix = t[old_start:old_end].splitlines()
new_column = ["Zhou2024", "---", "两级双向巡回，客户区域；一次完整过程", "独立取送需求、同车一次访问；非同箱使用后回收", "双层到离时刻、硬窗口、时变行驶", "按二级到仓与一级离仓资格；非固定卸回截点", "无RTI清洗供后批库存", "非同一RTI有限池复用问题", "供车充足、每车完整过程至多一路线", "车辆回原点；无全池清洁恢复", "运输＋装卸＋卫星暂存＋环境成本"]
assert len(matrix)==len(new_column)
t = t[:old_start]+"\n".join(line+" "+value+" |" for line,value in zip(matrix,new_column))+t[old_end:]
t = replace_once(t, "| 首次两级取送／自由分仓 | 删除首次性，作为基础模型 | Zhou 等已有先例 |", "| 首次两级取送／自由分仓／卫星时间同步 | 删除首次性，作为基础模型 | Zhou2022、Zhou2024等已有先例 |")
start = t.index("为了在论文定稿前进一步核查“卫星时间协调”")
end = t.index("\n\nZhou2022、Iassinovskaia2017", start)
t = t[:start]+"Zhou等2024卫星同步全文已补到并完成公式级核对，不再需要重复下载。它使回收返仓事件影响当前回运，但没有本文完整RTI清洗供后批链；详见[专项报告](C:/Users/猫的秋刀鱼/Desktop/毕业设计/docs/novelty_satellite_sync_20261004.md)。\n\n仍值得补充的是 **Zhang、Chu、Che、Li（2024），Closed-loop inventory routing problem for perishable food with returnable transport items selection.** IJPR62(1–2)，501–521，DOI [10.1080/00207543.2023.2275639](https://www.tandfonline.com/doi/full/10.1080/00207543.2023.2275639)。已有正式信息确认多类型RTI、食品闭环库存路径与利润目标，仍需核对处理和再使用时序，尤其当最终论文保留食品／生鲜背景时。"+t[end:]
t = replace_once(t, "没有在这四篇发现完整复现，是有证据的比较结论；", "在已审读六篇中未发现完整复现，是相对这些公开模型的比较结论；")
t = replace_once(t, "- 更新后的[文献到位与待补清单]", "- 新补Zhou2024的来源和本轮验证：[zhou2024_source.json](C:/Users/猫的秋刀鱼/Desktop/毕业设计/output/literature_audit_20261004/fulltexts/zhou2024_source.json)、[satellite_sync_verification.json](C:/Users/猫的秋刀鱼/Desktop/毕业设计/output/literature_audit_20261004/satellite_sync_verification.json)。此前fulltext_verification保留为当时的五篇审读快照。\n- 更新后的[文献到位与待补清单]")
full.write_text(t, encoding="utf-8")

main = ROOT / "docs/novelty_overlap_audit_20261004.md"
t = main.read_text(encoding="utf-8")
t = replace_once(t, "已完成四篇高风险近邻的全文模型比较", "已完成四篇高风险近邻、清洗与卫星同步两篇补充的全文模型比较")
t = replace_once(t, "本报告文献条目由18增至19。", "另补到Zhou2024卫星同步全文，专项比较见[新报告](novelty_satellite_sync_20261004.md)。沿其参考文献追加Marques2022的出版社摘要，文献条目现为20项。")
t = replace_once(t, "5. 两级卫星同步和食品 RTI 处理时序仍有需要补充全文的邻域，不把四篇比较完成说成全库原创性排除。", "5. Zhou2024卫星同步全文已核对：二级返仓影响一级回运已有先例，固定截点单独的创新性较弱。食品RTI处理时序仍需补核；六篇全文比较不是全库原创性排除。")
t = replace_once(t, "- 四篇全文来源、SHA256与原文件对应关系", "- 新补Zhou2024视觉审读第3–19页，包括资格式(32)–(34)、同步式(44)–(49)、有效不等式、MA与实验；原件和哈希保留。\n- 四篇全文来源、SHA256与原文件对应关系")
t = replace_once(t, "DOI 10.1016/j.cor.2024.106600；全文时序待核", "DOI 10.1016/j.cor.2024.106600；出版版第3–19页已审读，g资格、装卸与等待已有；无当前清洗供后批链")
anchor = "## 4. 核心机制的证据矩阵"
t = replace_once(t, anchor, "**L20，Marques等（2022）**：*A Branch-Cut-and-Price Approach for the Single-Trip and Multi-Trip Two-Echelon Vehicle Routing Problem with Time Windows*，Transportation Science56(6)，1598–1617，DOI [10.1287/trsc.2022.1136](https://pubsonline.informs.org/doi/10.1287/trsc.2022.1136)。沿Zhou2024引用追溯，出版社摘要明确卸装先后、卫星暂存合并、多趟和路线列／先后约束的分支割定价。本轮仅摘要级核对，不以此排除具体RTI闭环。\n\n"+anchor)
row = "| Accorsi2022出版版 | 周级设施运输流LP | 清洁／脏箱、产能、库存 | 无客户巡回事件 | 无本文规则 | 周级处理而非路线事件时长 |"
t = replace_once(t, row, row+"\n| Zhou2024出版版 | 两级双向、事件同步、时变行驶 | 一次过程＋给定旧库存，非同池后批复用 | 一次同车取送独立货物 | 按实际到离资格，非固定卸回截点 | 无RTI清洗供后批链 |")
t = replace_once(t, "本轮已经完成 L3–L6 主模型核对，差异比第一轮更明确；仍有 L16 两级卫星同步及 L12 食品RTI时序等待全文补充，不能把此表当作全球原创性证明。", "L3–L6及L16主模型均已核对。L16明确已存在二级返仓影响一级回运，本文需比较其与有限池清洗供箱反馈的组合。L12食品RTI时序仍待全文，不能把此表当作全球原创性证明。")
t = replace_once(t, "有限池决策变化、封批政策依据；L16／L12邻域全文补核", "有限池决策变化、封批政策依据；L16已核，L12仍需补全文")
t = replace_once(t, "全文优先清单转为两级卫星同步与食品RTI处理时序；不继续重复下载已审读四篇。", "卫星同步全文已补核，仍待食品RTI处理时序；不继续重复下载已审读论文。")
start=t.index("如学校权限方便，建议定稿前补充以下两篇")
end=t.index("\n\n和剑波增强出版", start)
t=t[:start]+"Zhou等2024卫星同步21页出版版已到位，第3–19页审读完成。[专项核对](novelty_satellite_sync_20261004.md)指出回运资格已有先例、固定截点单独的贡献较弱，但未见当前有限池清洗供后批链；不再需要重复下载它。\n\n仍建议补 *Closed-loop inventory routing problem for perishable food with returnable transport items selection*（Zhang等2024），DOI [10.1080/00207543.2023.2275639](https://www.tandfonline.com/doi/full/10.1080/00207543.2023.2275639)。核对食品RTI处理与复用时序；若保留食品／生鲜背景，更值得补。"+t[end:]
t=t.replace("优先级低于上述两篇。", "优先级低于剩余RTI时序核对。")
t=replace_once(t, "用户提供四篇出版版及新追加Accorsi的完整页码、公式和结论", "用户提供四篇出版版及追加Accorsi、Zhou2024的完整页码、公式和结论")
t=replace_once(t, "保存核心哈希、运行核验和来源。", "保存上一轮核心哈希、运行核验和来源；新补同步全文的[本轮验证](../output/literature_audit_20261004/satellite_sync_verification.json)另存，保留历史快照。")
main.write_text(t, encoding="utf-8")

reading=ROOT/"reference/novelty_fulltexts_20261004/reading_list.md"
t=reading.read_text(encoding="utf-8")
row="| Soysal等2023，Managing returnable transport items in a vendor managed inventory system；DOI 10.1016/j.seps.2022.101504 | [12页PDF](soysal_2023_vmi.pdf) | 式(1)–(33)、R&F、附录A；案例明确清洗时间可忽略 |"
t=replace_once(t,row,row+"\n| Zhou等2024，Two-echelon time-dependent vehicle routing problem with simultaneous pickup and delivery and satellite synchronization；DOI10.1016/j.cor.2024.106600 | [21页PDF](zhou_2024_satellite_sync.pdf) | 第3–19页，g回运资格、装卸／等待、容量、MA及实验；[专项比较](../../docs/novelty_satellite_sync_20261004.md) |")
start=t.index("1. **优先：Zhou等2024")
end=t.index("\n\n## 只在复现实验",start)
t=t[:start]+"**Zhang、Chu、Che、Li2024，Closed-loop inventory routing problem for perishable food with returnable transport items selection.** IJPR62(1–2)，501–521；[DOI10.1080/00207543.2023.2275639](https://www.tandfonline.com/doi/full/10.1080/00207543.2023.2275639)。核对处理／清洗／再使用时序，特别是论文保留生鲜食品背景时，仍有全文覆盖缺口。Zhou2024已补到，不再重复下载。"+t[end:]
t=replace_once(t,"- 和剑波文章的增强出版", "- Zhou2024在线附录A–E：拆分、初始化、对比ALNS、实例生成和管理分析，主PDF不含。主模型比较已经完成，忠实复现算法／数据才需要。\n- 和剑波文章的增强出版")
t=replace_once(t,"四篇原路径、副本哈希、页数与提取文本见[来源记录](../../output/literature_audit_20261004/fulltexts/supplied_sources.json)。", "此前四篇原路径、副本哈希、页数与提取文本见[来源记录](../../output/literature_audit_20261004/fulltexts/supplied_sources.json)；新补Zhou2024单独记录于[zhou2024_source.json](../../output/literature_audit_20261004/fulltexts/zhou2024_source.json)。")
reading.write_text(t,encoding="utf-8")
print(json.dumps(dict(updated=[str(x.relative_to(ROOT)) for x in (full,main,reading)]),ensure_ascii=False))
