"""Update the first audit after the user supplied the four priority papers."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
path = ROOT / 'docs/novelty_overlap_audit_20261004.md'
body = path.read_text(encoding='utf-8')

def section(start, end, new):
    global body
    if body.count(start) != 1 or body.count(end) != 1:
        raise ValueError(f'Unexpected section count: {start}')
    left = body.index(start)
    right = body.index(end, left)
    body = body[:left] + new.strip() + '\n\n' + body[right:]

def replace(old, new):
    global body
    if body.count(old) != 1:
        raise ValueError(f'Unexpected occurrence count: {old[:70]}')
    body = body.replace(old, new)

section('## 1. 结论与证据边界', '## 2. 先准确说清楚', '''## 1. 结论与证据边界

**当前判断：建议保留主线，但收窄创新表述并补齐有限池实验。已完成四篇高风险近邻的全文模型比较，未发现其完整复现当前 M5 的路线事件—卫星卸回截点—一级回运—清洗释放—后批供箱链。宽泛的“两级配送＋循环包装＋多周期＋ALNS”仍与已有研究明显重叠，不能据此声称首次。**

本报告已根据用户提供的四篇出版版更新。详细原文页码、公式、算法与实验对照见 [全文逐项比较报告](novelty_fulltext_comparison_20261004.md)。新报告还沿 Guo 的参考文献补读 Accorsi 等（2022）的公开清洗与运输模型；本报告文献条目由18增至19。

全文核对确认：和剑波等确有巡回、时间窗、越库协调及跨期空重箱；Guo 明确要求洗净再用并研究迟返缺箱；Yavrucu 的式(24)已经使用上一期收集空箱支撑本期装货；Soysal 也有回收、填充和跨期库存，案例将清洗时间视为可忽略。不能把它们概括为不考虑回收时序或清洗。

当前最值得研究的具体差异是：在固定订单批次、有限容器池和有限物理车辆下，路线与服务时刻改变二级卸回完成、回运轮次及实际清洗释放，从而改变后批供箱可行性。这一机制进入代码且有边界测试，但差异是否具有研究价值仍需实验和运营政策依据。

主要证据缺口现在是：

1. 正式 M5 对比已完成560/560次运行，但全部使用 N=Q，后批发箱不需要依靠及时回收复用。它是全池运输基准，不能单独验证有限池主贡献。
2. 紧池验证尚缺系统联合／顺序对照、时序简化消融，以及按独立来源家族划分的评测。
3. 通用 ALNS 操作已有广泛先例；联合库存下界、时序包络与日程恢复更值得核查独立方法增量和适用证书范围。
4. 现行目标是两层总距离，费用后处理不能称为经济成本最优；固定截点封批、日内使用时长和周期末全池归还也需要业务依据。
5. 两级卫星同步和食品 RTI 处理时序仍有需要补充全文的邻域，不把四篇比较完成说成全库原创性排除。

### 阅读深度与边界

- 已核对活动政策、订单、状态、审计、精确核心约束、ALNS、联合截止库存方法、成本模块和数据／实验文档。
- Zhou、Iassinovskaia、Charaf 的公开 PDF 在第一轮已核对核心模型。本轮核对用户四篇出版版的问题、变量、完整主模型及相关算法，并渲染公式页面；Yavrucu 的图像公式和 Accorsi 的扫描页面经过视觉读取。
- 四篇全文来源、SHA256与原文件对应关系保存在来源记录中。没有模型源码／补充材料的仿真或混合算法，只比较公开描述，不保证其内部实现完全相同。
- 第一轮运行12个现有机制测试，全部通过。本轮只读既有实验并更新报告／文献记录，未修改活动模型、数据和箱池配置，也未新增大规模优化任务。
- 本次是针对性中英文近邻与引用链审查，未完成 CNKI、Web of Science、Scopus 全库系统综述。未发现相同组合不证明全球首次，作者稿也不能无条件代替终稿。

硕士论文可以围绕明确的模型扩展、可验证的方法和条件明确的管理结论成立，不必把每个组成部分说成首次。本审查是研究证据判断，不是学位通过或录用保证。''')

replace('| L3 | 和剑波等，2026，多周期汽车循环包装与越库配送 | 多周期空重箱调度、配送协同、改进 ALNS | 期刊正式摘要，全文待取得 | 最高优先级核对；不能只与 Zhou 比 |', '| L3 | 和剑波等，2026，多周期汽车循环包装与越库配送 | 巡回、时间窗、空重库存、越库到库协调、ALNS | 用户出版版，表1–2及式(1)–(24) | 必须正面比较；无本文清洗释放与卸回归批链 |')
replace('| L4 | Guo 等，2023，RTI 共享平台 | 两层容器中心、周期补货回收、迟返造成缺箱 | 作者大学正式摘要 | “两层循环箱管理＋缺箱”已不是空白 |', '| L4 | Guo 等，2023，RTI 共享平台 | 两层 CMC、清洗再用、周期回收、提前期缺箱 | 用户出版版，第3–4节 | 客户 milk-run 排除；不能以迟返缺箱或洗后复用为新点 |')
replace('| L5 | Yavrucu 等，2025，协作闭环库存路径 | 多期、配送回收、库存、清洗 | 作者大学正式摘要 | 清洗的时间/容量处理与回收滞后必须核对全文 |', '| L5 | Yavrucu 等，2025，协作闭环库存路径 | 多期巡回、上一期收集支撑装货、清洗、需求更新 | 用户出版版，表2及式(1)–(43) | 无本文两级车交接和清洗时长；已有期级复用条件 |')
replace('| L6 | Soysal 等，2023，VMI 下 RTI 管理 | 双向路线、复用、库存、采购、清洗费用 | 出版社摘要 | 容器回收与成本分析已有完整研究链 |', '| L6 | Soysal 等，2023，VMI 下 RTI 管理 | 双向路线、填充、库存、采购与清洗费用 | 用户出版版，式(1)–(33)、附录A | 单供应商直达；案例清洗时间可忽略，无本文事件链 |')

section('### 3.3 三篇必须取得全文的高风险近邻', '### 3.4 算法和其余近邻', '''### 3.3 已核对全文的三个高风险近邻

**L3：和剑波、郭珉、陈璇（2026）。** 《多周期汽车循环包装器具调度及越库配送协同优化研究》，《供应链管理》7(8)，101–112，DOI 10.19868/j.cnki.gylgl.2026.08.009。[正式页面](https://gylg.cbpt.cnki.net/portal/journal/portal/client/paper/dfc47c2c54f849b32ced54a4e76f33ef)。出版版已确认卷期，平台显示日期的疑问不再作为文献身份障碍。

PDF 3–6／103–106确认中心—零部件供应商、中心—主机厂两组车辆巡回，式(3)–(4)为时间窗、式(6)为入库车辆到库协调，式(17)、(22)–(24)为跨期空重箱关系。短缺可外调／租购；未见清洗时长和本文二级卸回截点归批。不能把它称为单纯流量模型，也不能把其到库协调误作本文固定快照。PDF 6–8／106–108已有初始化、拆分解码、破坏修复、2-opt/Swap、自适应权重和退火；普通 ALNS 不足以形成区别。增强出版案例附录不在所给 PDF，只有复现其算法比较时才需要补。

**L4：Guo、Kong、Chan、Thadani（2023）。** IJPR 61(13)，4575–4591，DOI 10.1080/00207543.2023.2187243。[大学页面](https://scholars.hkbu.edu.hk/en/publications/integrated-inventory-control-and-scheduling-decision-framework-fo/)。

PDF 6–7／4579–4580确认两层 CMC、洗后回运和提前期造成缺箱。PDF 9–12／4582–4585的假设(ix)明确清洗后才可服务下一订单，假设(viii)排除 R-CMC 到收货方的客户 milk-run。其预测＋离散事件仿真＋调拨模型以设施调度与数量为决策，没有当前两级客户巡回和卸回截点资格公式；式(9)还已有阈值满足时全量回收。洗箱发生在区域中心，本文则在回运总仓后进行。不能声称其没有清洗、没有迟返缺箱或没有回收全取规则。

**L5：Yavrucu、Soysal、Sel、Çimen、Hamzadayı（2025，2024在线）。** Transportation Letters 17(3)，470–490，DOI 10.1080/19427867.2024.2360812。[大学页面](https://avesis.hacettepe.edu.tr/yayin/d1111c3c-bfda-41fe-ba50-8816864ec08e/modeling-a-closed-loop-inventory-routing-problem-for-returnable-transport-items-under-horizontal-logistics-collaborations-and-dynamic-demand-structure)。

PDF 6–9／474–477确认同车从车库到供应商取货、向客户配送／收空、返空到供应商再回车库；多供应商不是两级物理车交接。式(24)使用上期客户收集 C（不是供应商卸回 F）加新购／外包支撑本期装货，式(25)限制累计回收≤θ倍以前累计需求。已有期级复用，不是只收清洗费。表2及式(30)–(32)无本文清洗持续时间、客户送取窗／使用间隔和卫星归批。PDF14／482确有中途需求更新：固定前两期、改变第三／第四期后重优化；它不是本文静态批次。

上述均支持与当前 M5 有具体差异，但未证明首次性。逐篇完整字段与公式见[全文报告](novelty_fulltext_comparison_20261004.md)。''')

replace('- **L6，Soysal 等（2023）**：VMI 的闭环 RTI 路线、库存、采购与清洗费用已有研究；本文不能以“清洗纳入成本”为主创新。完整时序公式待核。[出版社](https://www.sciencedirect.com/science/article/pii/S0038012122003111)，DOI 10.1016/j.seps.2022.101504。', '- **L6，Soysal 等（2023）**：出版版式(10)–(16)确认回收、填充与空重库存的期级时序；式(12)配送受上期期末满箱库存限制。PDF5第4.1节把案例清洗时间视为可忽略，单供应商直达客户，没有本文卫星／路线释放链。R&F不是本文联合库存下界方法。不能以清洗费用或期级复用为创新。[出版社](https://www.sciencedirect.com/science/article/pii/S0038012122003111)，DOI 10.1016/j.seps.2022.101504。')
replace('这里既有运输路线研究，也有库存规划和网络运营研究；', '**L19，Accorsi等（2022）**：沿 Guo 参考文献补读的 *Managing uncertain inventories, washing, and transportation of reusable containers in food retailer supply chains*，SPC31，331–345，DOI [10.1016/j.spc.2022.02.014](https://doi.org/10.1016/j.spc.2022.02.014)。公开出版版第3–5页有清洁／脏箱库存及周级清洗／储存产能、运输流和返空提前期估计，进一步排除这些宽泛要素的首次性。不是当前客户巡回与截点模型，[全文已保存](../output/literature_audit_20261004/sources/accorsi_2022_washing.pdf)。\n\n这里既有运输路线研究，也有库存规划和网络运营研究；')

section('## 4. 核心机制的证据矩阵', '## 5. 最重要的实验诊断', '''## 4. 核心机制的证据矩阵

以下按公开主模型比较，截点特指二级卸回完成决定一级固定回运轮次。没有这条完整链，不等于没有一般清洗、提前期或卫星同步。

| 工作 | 网络与路线 | 跨期供货／复用 | 同订单送后使用再取 | 卫星卸回截点归批 | 路线实际回仓＋清洗时长支撑后批 |
| --- | --- | --- | --- | --- | --- |
| 当前 M5 | 两级巡回、E2物理车多趟 | 固定有限清洁池 | 有 | 有，全量快照 | 有 |
| Zhou作者稿 | 两级取送巡回 | 非本文供箱链 | 一次同时取送 | 非本文机制 | 非RTI清洁库存 |
| Iassinovskaia作者稿 | 生产者直达客户 | 空重库存、填充及新购 | 周期同时取送 | 无卫星层 | 期级条件，非本文事件释放 |
| Grangier公开稿 | 两级多趟、车辆同步 | 非RTI模型 | 非本文机制 | 同步非快照回运 | 非RTI模型 |
| Charaf出版版 | 两级多期补货 | 商品库存 | 纯补货 | 非本文机制 | 非RTI模型 |
| 和剑波出版版 | 两组巡回、越库到库协调 | 空重库存、前期回收、可外调箱 | 下期拆箱返空，非订单事件 | 未见本文资格映射 | 未见清洗时长／释放 |
| Guo出版版 | 两层CMC，客户milk-run排除 | 洗后复用、周期调回、提前期缺箱 | 无联合客户事件链 | 未见本文规则 | 有清洗前提／仿真，无完整路线链 |
| Yavrucu出版版 | 同车经供应商、客户、供应商 | 上期收集支撑本期装货 | 期级需求生成回收 | 无本文卫星层 | 已有期级复用，无清洗时长 |
| Soysal2023出版版 | vendor直达客户 | 回收、填充、空重库存 | 期级既有空箱回收 | 无卫星层 | 案例忽略清洗时间 |
| Accorsi2022出版版 | 周级设施运输流LP | 清洁／脏箱、产能、库存 | 无客户巡回事件 | 无本文规则 | 周级处理而非路线事件时长 |

本轮已经完成 L3–L6 主模型核对，差异比第一轮更明确；仍有 L16 两级卫星同步及 L12 食品RTI时序等待全文补充，不能把此表当作全球原创性证明。''')

section('### 5.4 正式实验的当前状态', '## 6. 模型机制已实现', '''### 5.4 正式实验的当前状态

本轮全文更新读取的最终记录：

- M1–M4：2026-10-04 18:13:18+08:00，COMPLETED，1120/1120；1077条标记有效。43次无有效解不能据此判为不可行。
- M5：2026-10-04 20:47:01+08:00，COMPLETED，560/560且全部有效；140个批次配置，140次精确300秒、420次ALNS三种子各30秒；71次精确已证最优。
- 按运行CSV与冻结manifest逐行核验，M5全部560次均为N=Q；476条路线事后所需最低池小于输入池，这不是紧池重新优化或最小池全局最优证明。

来源：[M1–M4最终报告](../output/experiments/m1_m4_comparison_20261004/full/report.md)、[M5最终报告](../output/experiments/m5_comparison_20261004/full/report.md)。两组一致性检查错误均为0，仍不代替独立全量正确性证明。

M5小规模B2/B3各30例精确全部最优，ALNS三种子最好解平均实际最优误差0.531%／0.594%；大规模最好解平均比时限内MILP可行解低12.463%／11.688%，但认证界差仍约34.409%／39.091%，不能称为近似已知最优。M2小规模最好ALNS平均实际最优误差10.210%，也不支持所有模型均接近最优。

这组完整结果有助于建立全池运输参照，核心有限池实验仍需补齐。预算不同，不能推出同预算优势；同源情景和多个种子不能算独立样本。本审查未修改或中断这些实验。第一轮17:03的历史快照保存在原验证记录中，不再作为当前进度。''')

replace('| 模型 | 面向特定日内周转政策，联合表示送后使用、两级回运、卸回截止与清洁库存 | L3–L6 全文约束对照，证明不只是既有模型换名称 |', '| 模型 | 日内送后使用、两级回运、卸回截止与清洁释放；L3–L6全文对照已指出差异 | 有限池决策变化、封批政策依据；L16／L12邻域全文补核 |')
replace('在全文排重和实验完成前，使用“建立”“刻画”“研究”“设计并验证”，', '当前四篇近邻已比完、全池实验已完成，有限池与方法增量证据仍待补齐，使用“建立”“刻画”“研究”“设计并验证”，')
replace('- **若全文近邻缺少本文完整耦合**：保留当前主线，', '- **当前四篇主模型未见本文完整耦合**：建议保留当前主线，')
replace('下一步最优先取得全文，不是继续在同一个困难例上刷 GAP。', '下一步最优先补紧池联合／顺序对照和机制消融，同时确认运营政策依据。全文优先清单转为两级卫星同步与食品RTI处理时序；不继续重复下载已审读四篇。')

section('## 11. 需要你帮忙下载的全文', '## 12. 可复核材料与检索记录', '''## 11. 已取得全文与后续最值得补充的材料

用户提供的四篇已经复制到项目并完成核心模型、算法及相关实验核对；原文件未改。来源对应与SHA256见 [记录](../output/literature_audit_20261004/fulltexts/supplied_sources.json)。Accorsi2022也已自行取得公开全文，当前四篇比较不再需要下载帮助。

如学校权限方便，建议定稿前补充以下两篇，优先第一篇；不以缺全文阻止当前有限池研究推进：

1. *Two-echelon time-dependent vehicle routing problem with simultaneous pickup and delivery and satellite synchronization*（Zhou等2024），DOI [10.1016/j.cor.2024.106600](https://www.sciencedirect.com/science/article/pii/S0305054824000728)。正式信息确认卫星缓冲、卸载／储存／重装同步，需要公式级比较本文回运截点是否只是既有同步规则的限制情形。
2. *Closed-loop inventory routing problem for perishable food with returnable transport items selection*（Zhang等2024），DOI [10.1080/00207543.2023.2275639](https://www.tandfonline.com/doi/full/10.1080/00207543.2023.2275639)。核对食品RTI处理与复用时序；若保留食品／生鲜背景，更值得补。

和剑波增强出版案例附录及Yavrucu在线算法／案例补充，只在复现其算法比较时再取。Zhou2022和Iassinovskaia2017终稿有助于最终版本核对，公开作者稿已有，优先级低于上述两篇。详细状态见 [已到位与待补清单](../reference/novelty_fulltexts_20261004/reading_list.md)，全文结论见 [逐项比较](novelty_fulltext_comparison_20261004.md)。''')

replace('### 核心本地依据', '用户提供四篇出版版及新追加Accorsi的完整页码、公式和结论见[全文报告](novelty_fulltext_comparison_20261004.md)；本轮[验证记录](../output/literature_audit_20261004/fulltext_verification.json)保存核心哈希、运行核验和来源。\n\n### 核心本地依据')
path.write_text(body, encoding='utf-8')

reading = ROOT / 'reference/novelty_fulltexts_20261004/reading_list.md'
reading.write_text('''# 创新审查：全文到位状态与后续材料

日期：2026-10-04。用途：硕士论文。四篇用户提供的出版版已到位并完成主模型比较，原文件未改；不需要重复下载。

完整判断见[全文逐项比较](../../docs/novelty_fulltext_comparison_20261004.md)，项目整体判断见[更新后的总报告](../../docs/novelty_overlap_audit_20261004.md)。

## 已收到并审读

| 文献 | 项目内原样副本 | 已核查内容 |
| --- | --- | --- |
| 和剑波等2026，《多周期汽车循环包装器具调度及越库配送协同优化研究》；DOI 10.19868/j.cnki.gylgl.2026.08.009 | [12页PDF](he_2026_multiperiod_crossdock.pdf) | 表1–2、式(1)–(24)、ALNS及案例；确认巡回／时间窗／跨期空重箱，未见本文清洗释放和固定卸回归批链 |
| Guo等2023，Integrated inventory control and scheduling decision framework…；DOI 10.1080/00207543.2023.2187243 | [18页PDF](guo_2023_rti_sharing.pdf) | 客户末段排除；清洗再用、回运提前期、库存与调拨式(2)–(21) |
| Yavrucu等2025（2024在线），Modeling a closed-loop inventory routing problem…；DOI 10.1080/19427867.2024.2360812 | [22页PDF](yavrucu_2025_closed_loop.pdf) | 表2、式(1)–(43)，包括图像公式(24)–(25)；算法与中途需求更新 |
| Soysal等2023，Managing returnable transport items in a vendor managed inventory system；DOI 10.1016/j.seps.2022.101504 | [12页PDF](soysal_2023_vmi.pdf) | 式(1)–(33)、R&F、附录A；案例明确清洗时间可忽略 |

四篇原路径、副本哈希、页数与提取文本见[来源记录](../../output/literature_audit_20261004/fulltexts/supplied_sources.json)。

## 自行追加取得

Accorsi、Cholette、Manzini、Mucci（2022），Managing uncertain inventories, washing, and transportation of reusable containers in food retailer supply chains，SPC31，331–345，DOI 10.1016/j.spc.2022.02.014。沿Guo参考文献发现，公开出版版[已保存](../../output/literature_audit_20261004/sources/accorsi_2022_washing.pdf)。周级清洁／脏箱、洗箱能力、库存和设施运输流已核查，不需要用户再取。

第一轮取得的Zhou2022、Iassinovskaia2017作者稿以及Charaf2024出版版仍在 output/literature_audit_20261004/sources/。

## 定稿前建议补充

1. **优先：Zhou等2024，Two-echelon time-dependent vehicle routing problem with simultaneous pickup and delivery and satellite synchronization.** Computers & Operations Research167，106600；[DOI 10.1016/j.cor.2024.106600](https://www.sciencedirect.com/science/article/pii/S0305054824000728)。核对卫星卸载／存储／重装同步公式，以及本文固定卸回归批的关系。仍为出版社信息级，未取得可读公开全文。
2. **其次：Zhang、Chu、Che、Li2024，Closed-loop inventory routing problem for perishable food with returnable transport items selection.** IJPR62(1–2)，501–521；[DOI 10.1080/00207543.2023.2275639](https://www.tandfonline.com/doi/full/10.1080/00207543.2023.2275639)。核对处理／清洗／再使用时序，特别是论文保留生鲜食品背景时。仍有全文覆盖缺口。

## 只在复现实验时需要

- 和剑波文章的增强出版输入／结果附录：PDF108页脚注明确不在当前PDF中。主模型比较已经足够，不需要为本轮结论单独寻找。
- Yavrucu的在线VND/WSA详细说明及案例附录：主模型已取得；完整算法复现才需要。
- Zhou2022、Iassinovskaia2017最终出版版：终稿引用核对有帮助，已有公开稿，不是当前主要瓶颈。

当前研究优先级是有限箱池主实验、联合／顺序对照和机制消融；不因不断增加下载清单而搁置这些工作。
''', encoding='utf-8')

full = ROOT / 'docs/novelty_fulltext_comparison_20261004.md'
full.write_text(full.read_text(encoding='utf-8').replace('清洗費', '清洗费'), encoding='utf-8')
print('Updated original audit, full-text report, and reading list.')
