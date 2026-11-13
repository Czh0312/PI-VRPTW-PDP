# 27 例六模型基准

每档 9 例。小规模 7 位顾客，中规模 20 位，大规模 50 位。按 `case_index.csv` 选择模型和算例。各模型使用同一基础算例的 `shared/` 路网及车辆。

模型 1–4 沿用原编号。模型 5 保留原模型 8 的可变批次和批内回收截点，由订单释放及顾客时间窗决定首批装车时段。模型 6 沿用同一基础算例的订单与物理作业时长，允许无批次的分次回收与清洗后再用。模型 3–6 的当前配置由订单释放时间确定每日复位期限。

文件哈希和来源保存在 [manifest.json](manifest.json)。输入验证可运行 `python -m tools.audit.audit_current_benchmark`。旧版数据和结果见[原编号归档](../../archive/datasets/pre_renumbering_20261113/README.md)。
