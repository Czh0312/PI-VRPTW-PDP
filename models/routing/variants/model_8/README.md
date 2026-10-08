# 模型8：不等长批次、逐批独立分界

周期总长H固定，同时优化批次数K、各批长度和各批分界；各批长度之和为H。订单分批、箱池、两级路线、车辆和实际服务时刻联合优化同一成本。顾客预约和货物释放时刻保持绝对时间。

- [精确求解](exact/README.md)：每个K的完整联合MILP，共享总预算并汇总全局证明。
- [ALNS](alns/README.md)：批次边界、独立截点、订单分批和路线的自适应搜索。
- [完整定义、输入、运行和审计](../../../../docs/model7_model8_calendar_design.md)。

```sh
.venv/bin/python main.py route --case config/experiments/examples/model8_demo.json --solver exact --exact-time 30
.venv/bin/python main.py route --case config/experiments/examples/model8_demo.json --solver alns --time 3 --iterations 3000
```

新增编号不恢复已归档的历史模型。费用示例为合成单位；机制验证不能替代大规模算法实验。

