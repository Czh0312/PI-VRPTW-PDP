# 创新审查：全文到位状态与后续材料

审查批次日期：2026-10-04；最后更新2026-10-05。用途：硕士论文。此前四篇、Zhou2024和Zhang2024出版版均完成主模型比较；另审读Accorsi2022公开出版版，七篇重点全文到位。原文件未改，不需要重复下载这些文献。

完整判断见[全文逐项比较](../../docs/novelty_fulltext_comparison_20261004.md)，项目整体判断见[更新后的总报告](../../docs/novelty_overlap_audit_20261004.md)。

## 已收到并审读

| 文献 | 项目内原样副本 | 已核查内容 |
| --- | --- | --- |
| 和剑波等2026，《多周期汽车循环包装器具调度及越库配送协同优化研究》；DOI 10.19868/j.cnki.gylgl.2026.08.009 | [12页PDF](he_2026_multiperiod_crossdock.pdf) | 表1–2、式(1)–(24)、ALNS及案例；确认巡回／时间窗／跨期空重箱，未见本文清洗释放和固定卸回归批链 |
| Guo等2023，Integrated inventory control and scheduling decision framework…；DOI 10.1080/00207543.2023.2187243 | [18页PDF](guo_2023_rti_sharing.pdf) | 客户末段排除；清洗再用、回运提前期、库存与调拨式(2)–(21) |
| Yavrucu等2025（2024在线），Modeling a closed-loop inventory routing problem…；DOI 10.1080/19427867.2024.2360812 | [22页PDF](yavrucu_2025_closed_loop.pdf) | 表2、式(1)–(43)，包括图像公式(24)–(25)；算法与中途需求更新 |
| Soysal等2023，Managing returnable transport items in a vendor managed inventory system；DOI 10.1016/j.seps.2022.101504 | [12页PDF](soysal_2023_vmi.pdf) | 式(1)–(33)、R&F、附录A；案例明确清洗时间可忽略 |
| Zhou等2024，Two-echelon time-dependent vehicle routing problem with simultaneous pickup and delivery and satellite synchronization；DOI10.1016/j.cor.2024.106600 | [21页PDF](zhou_2024_satellite_sync.pdf) | 第3–19页，g回运资格、装卸／等待、容量、MA及实验；[专项比较](../../docs/novelty_satellite_sync_20261004.md) |
| Zhang等2024，Closed-loop inventory routing problem for perishable food with returnable transport items selection；DOI10.1080/00207543.2023.2275639 | [22页PDF](zhang_2024_perishable_rti_selection.pdf) | 表2、式(1)–(26)，尤其(13)–(14)跨期先后；KS、案例与170例实验；[专项比较](../../docs/novelty_perishable_rti_20261004.md) |

此前四篇原路径、副本哈希、页数与提取文本见[来源记录](../../output/literature_audit_20261004/fulltexts/supplied_sources.json)；新补Zhou2024单独记录于[zhou2024_source.json](../../output/literature_audit_20261004/fulltexts/zhou2024_source.json)。 Zhang2024原件对应关系、SHA256、页数和文本见[zhang2024_source.json](../../output/literature_audit_20261004/fulltexts/zhang2024_source.json)。

## 自行追加取得

Accorsi、Cholette、Manzini、Mucci（2022），Managing uncertain inventories, washing, and transportation of reusable containers in food retailer supply chains，SPC31，331–345，DOI 10.1016/j.spc.2022.02.014。沿Guo参考文献发现，公开出版版[已保存](../../output/literature_audit_20261004/sources/accorsi_2022_washing.pdf)。周级清洁／脏箱、洗箱能力、库存和设施运输流已核查，不需要用户再取。

第一轮取得的Zhou2022、Iassinovskaia2017作者稿以及Charaf2024出版版仍在 output/literature_audit_20261004/sources/。

## 本次优先全文已齐

此前优先补充的Zhou2024卫星同步和Zhang2024食品RTI均已收到、核对主模型及相关算法／实验，暂时不需要用户继续下载新文献。判断是针对性近邻比较，不是全库排除相同模型。Achamrah2022沿引用链仅核对出版社摘要，保留阅读深度标记，不计为已排除的全文近邻。

## 只在复现实验时需要

- Zhou2024在线附录A–E：拆分、初始化、对比ALNS、实例生成和管理分析，主PDF不含。主模型比较已经完成，忠实复现算法／数据才需要。
- 和剑波文章的增强出版输入／结果附录：PDF108页脚注明确不在当前PDF中。主模型比较已经足够，不需要为本轮结论单独寻找。
- Yavrucu的在线VND/WSA详细说明及案例附录：主模型已取得；完整算法复现才需要。
- Zhou2022、Iassinovskaia2017最终出版版：终稿引用核对有帮助，已有公开稿，不是当前主要瓶颈。

当前研究优先级是有限箱池主实验、联合／顺序对照和机制消融；不因不断增加下载清单而搁置这些工作。
