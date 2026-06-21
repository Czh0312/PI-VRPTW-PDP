---
name: reusable-container-references-and-thesis
description: 论文核心论点（两阶段可循环容器的未来价值）的双支柱结构，以及支撑它的权威文献/政策清单
metadata:
  type: project
---

# 论文核心论点与支撑文献

## 核心论点（双支柱）
要证明"未来可回收两阶段容器有价值"，必须两条腿走路：
1. **趋势支柱**：政府/国际组织公开文件显示可循环包装是确定的政策方向（强制目标已立法）。
2. **成本支柱**：用定量成本/损益平衡证明两阶段可循环容器在经济上可行（对应 `utils/economic_cost.py` 的成本分解）。

## 趋势支柱 — 最权威锚点
- EU PPWR = Regulation (EU) 2025/40，可循环目标在 **Article 29**（运输/电商包装最低可循环比例 + 2028 复审），2025-02-11 生效，2026-08-12 适用。EUR-Lex 原文必读。
- 中国：国办转发《关于加快推进快递包装绿色转型的意见》(2020, 发改委牵头)；国家邮政局《邮件快件包装管理办法》(2021)；发改委《深入推进快递包装绿色转型行动方案》(2023)。
- UNEP 全球塑料条约（INC，谈判中，可循环为核心议题）；OECD《Global Plastics Outlook》(2022) + 政策情景到 2060。
- Ellen MacArthur Foundation《Reuse – Rethinking Packaging》（69 个可循环案例 + 6 类收益）。
- 其他司法管辖：法国 AGEC 法（2040 淘汰一次性塑料包装）；加州 SB 54（EPR）。

## 成本支柱 — 最直接对应 economic_cost.py 参数
- 趋势/经济性：ScienceDirect《Sustainability of reusable packaging – Current situation and trends》(Carrierpac 案例约 10 次循环回本)。
- 损失率 `loss_rate_per_cycle`(3%)：JCP《Optimal decisions in reducing loss rate of RTI》；行业损失 10–15%（SupplyChainBrain）。
- 损益平衡 `break_even_demand`：TCO 文献（Faes 公式；Packaging Revolution 50–70 次）。
- 运输元/公里 `e1/e2_cost_per_km`：反向物流运输是所有 LCA 的摆动项（IVL LCC 综述、密歇根参数化 LCA）——做敏感性分析时务必强调。
- 生鲜周转筐场景（北京生鲜）：PMC 同行评议 RPC vs 一次性（纸箱/木箱），RPC 更优。

## 方法论/背景支柱
- Physical Internet / π-container：Montreuil (2011) "Toward a Physical Internet", *Logistics Research* 3(2-3):71-87（奠基，引入可循环标准化 π-container）。
- 两级路径：2E-VRP 文献综述 EJOR 2022；Crainic/Perboli/Tadei 奠基（CIRRELT 2008）。

完整带链接清单已落盘：`reference/2026-06-21_可循环容器支撑文献清单.md`。
论点↔文献↔config 参数的双向追溯表：`reference/2026-06-21_论点_文献_参数_追溯表.md`（交叉对齐 `reference/cost/` 逐参数档）。
关联 [[pi_reusable_container_research_direction]]、[[cost-model-params-mainland-china]]。
