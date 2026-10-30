# 可行性见证

witnesses.zip 含全部216个模型配置的显式调度。各份均经独立重放，检查车辆、时间窗、箱量守恒、清洗、末期归库及重复周期。这证明输入可行，不证明最优，也不表示ALNS已找到这些解。M6–M8采用可行的K=1、N=Q见证，不能用于证明循环收益。主实验不会自动读取这些解作为初始解。

复核：`.venv/bin/python -m tools.audit.certify_formal_benchmark27 --verify`。
