# 数据集 V2 最终结果入口

- [修订说明与验收](../../../docs/dataset_redesign_v2_20260930.md)
- [最终完整性与复算核对](verification.json)
- `accepted_design/`：最终接受清单的设计审查、逐例统计和布局图。
- `reproduction/`：同一输入、同一脚本的独立复算；数值文件逐字节一致。
- `integration_final/`：558 个公共模型初始化、64 次有限搜索检查。
- `pool_smoke/`：42 次有限箱池检查，包括低于必要下界的不可行点与 M5 精确小例。
- `pytest.xml`：344 个回归测试通过。

`candidate_*`、`final_design/`、`integration/` 与 `initialization_01.json` 为开发过程材料；不代表最终验收结果。
现行清单 SHA256 为 `253fccbf665e3822859b07c432f8023a53d68c54166c26ec94ea814ef3ba0cae`。
