# Model 2：自由分仓纯配送

## 定位

Model 2 在 Model 1 的纯配送两级网络上增加顾客—前置仓分配决策。它不包含回收任务，因此适合单独测量“自由分仓”对纯配送路线的影响。

| 模型 | 顾客分仓 | 配送 | 延后回收 |
| --- | --- | --- | --- |
| M1 | 固定 | 是 | 否 |
| M2 | 自由 | 是 | 否 |
| M3 | 固定 | 是 | 是 |
| M4 | 自由 | 是 | 是 |

M1 的任何固定分仓可行解都属于 M2 的可行域，因此在相同参数和完整求解口径下，M2 的最优值不应劣于 M1。

## 完整数学模型

`models/routing/variants/model_2/model.py` 保存当前完整自由分仓弧流 MIP。核心决策包括：

- 每个顾客恰好分配到一个前置仓；
- 只启用至少服务一个顾客的前置仓；
- E1 将各仓所需货物从中心仓送到前置仓；
- E2 车辆从所属前置仓出发完成顾客配送并返回；
- 分配、访问、容量、时间窗和 E1/E2 衔接保持一致。

模型只有配送任务，不建立顾客 pickup、前置仓回收或回收载荷约束。

## 精确求解

`models/routing/variants/model_2/exact_solver.py` 是当前严格入口。只有完整自由分仓 MIP 返回 `OPTIMAL` 时才设置 `proven_optimal=True`。达到时间限制的可行解只作为 incumbent。

历史时间扩展、固定 E1 分解、受限路线池和分支定价实现已移至：

```text
archive/models/routing/variants/model_2/
```

受限路线池上的 `OPTIMAL` 不能证明完整 Model 2 全局最优。

## ALNS

`models/routing/alns/variants/model_2.py` 实现自由分仓纯配送 ALNS：

- 顾客所在的 E2 路线桶隐式表示其前置仓分配；
- 删除算子以单个配送顾客为单位；
- 修复算子跨前置仓比较插入位置；
- 每次分配变化后重新汇总 E1 配送任务；
- 输出路线、分配和成本供实验汇总。

## 当前使用方式

单案例可在统一入口覆盖模型编号：

```powershell
python main.py route --case config/cases/zhou_small_demo.json `
  --model 2 --solver alns --time 30
```

批量精确或 ALNS 对比使用 `experiments/runs/` 下的当前脚本。数据应来自 `data/zhou_later_pickup/`，历史 C101/R101 结果不再作为当前验证依据。
