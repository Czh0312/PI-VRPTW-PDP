# 正式主数据集：八模型 × 小中大各九例

共27个基础算例、216份可运行模型配置。每个模型有 small / medium / large 三个目录，每个目录九例。

```text
formal_benchmark27_v1/
├── model_1/ ... model_8/
│   ├── small/S01 ... S09/
│   ├── medium/M01 ... M09/
│   └── large/L01 ... L09/
│       ├── case.json        运行入口
│       ├── orders.json      相同物理订单，模型特定批次绑定
│       ├── policy.json      模型特定固定或参考日历
│       └── parameters.json  固定／优化／不适用参数说明
├── shared/                 27例的网络、车辆、费用及来源快照
├── manifest.json           八模型配对索引和输入哈希
├── case_index.csv          216项可读索引
└── selection.json          原始选例设计快照
```

| 规模 | 顾客数分布 | 每模型例数 |
| --- | --- | ---: |
| small | 7/10/12顾客各3例 | 9 |
| medium | 20顾客4例、30顾客5例 | 9 |
| large | 50顾客4例、100顾客5例 | 9 |

所有算例使用同一窗口／使用时间设计。每例跨模型保持需求、坐标、绝对送取窗、释放、使用与服务时间、车队和周期一致。批次绑定、日历与箱池是否固定由模型决定。M1/M2 不执行回收和清洗；M2/M4 的订单原分仓只是参考。

M1–M4固定单批，M3/M4固定N=Q。M5固定三批、等长日历、70%共同分界、参考订单分批及N=Q。M6优化K、共同分界、订单分批和N；M7改为独立分界；M8再放开批长。各实际批次必须有配送和回总仓箱量。M6–M8配置中的container_pool_size=Q是参考值，优化范围由pool_min/pool_max决定。

修订2将全部27例的共同参考分界从75%统一改为70%（每批182分钟配送阶段、78分钟回库阶段）；单批模型同步使用相同末次分界19:42。原M03/M5经精确求解判为不可行，根因涉及末批回库与清洗时间不足；原L09/M5也未找到可行解。修订保留需求、坐标、车队、费用、送取窗、周期终点和订单参考分批。修改不是最优分界标定，不根据模型的相对收益挑选案例。原始来源哈希和日历变更原因保存在各例provenance中。

修订2保留原始K=1…390的数学域，配置仅排除经证明不可能的K：每个非空配送批次至少需要一个订单；M6/M7的首批还必须足够长，容纳最早可能的实际回库。每例 shared/batch_count_certificate.json 给出逐订单乐观回库下界、公式、假设和保留范围。M6/M7保留3–5个候选，M8保留7–100个候选。这是可行域等价的预处理，不是人为限定K或按求解结果选数；不改变订单、车辆、费用或任何可行解。

M1–M5原生目标为距离，M6–M8为成本。共同费用文件供一致评价，不意味着改变了原生目标。费用为合成单位；单用包装经济评价需要相应的一次性包装价格，不能直接套用循环箱池费用。

27套空间网络来自14个原始来源块，中大规模含嵌套子集。来源和同源关系见manifest；种子重复不计为新算例。

在项目根目录运行：

```sh
.venv/bin/python main.py route --case data/formal_benchmark27_v1/model_1/small/S01/case.json --time 30
.venv/bin/python main.py route --case data/formal_benchmark27_v1/model_8/large/L09/case.json --time 30
.venv/bin/python -m tools.audit.audit_formal_benchmark27 --dataset data/formal_benchmark27_v1
```

默认ALNS 30秒、精确300秒、seed=42是统一入口默认值；正式多种子实验另行冻结预算和环境。生成程序拒绝覆盖非空数据集。全部运行输入在此数据集内部，配置路径相对项目根目录。来源路径仅作追溯，不在求解时加载。

输入核验及必要条件检查不等于全部算例已找到可行解或证明最优；实际状态以独立运行和审计为准。审计报告位于 `output/inspections/formal_benchmark27_v1/`。

发布版的validation目录另保存216份可行调度及独立重放记录。生成器之后运行 `python -m tools.audit.certify_formal_benchmark27` 可建立这些见证，`--verify` 可复核；求解器不会自动把见证当初始解。完整审查记录见 `output/inspections/formal_benchmark27_review_20261015/README.md`，比较与费用口径见本目录comparison_protocol.json。
