# 真实案例数据区

本目录把第三方原始文件、可复现的派生数据和参数审计分开，避免把“真实观测”与
“回收情景假设”混为一谈。

## 原始文件（本地下载，不随仓库分发）

| 文件 | 来源 | 校验值 | 用途 |
|---|---|---|---|
| `raw/lade_d/delivery_hz.parquet` | Cainiao-AI/LaDe-D | SHA256 `00b6b4dca471cadee796a3fc7f07dcd919499ec58459ea67db784dcb06089e75` | 真实 AOI、订单日期、完成时间、区域和快递员接单 GPS |
| `raw/r3pack/D3.5_R3PACK_Dataset.xlsx` | Zenodo DOI 10.5281/zenodo.21392137 | MD5 `431ae25f99695d68d26747062918cff6` | 国际闭环包装参数与敏感性参考 |

LaDe-D 的 Hugging Face 数据卡标注 Apache-2.0；R3PACK Zenodo 记录当前没有显示明确
license 字段，因此本项目只保存本地原件和参数摘录，重新分发前需再次核对许可。

## 派生案例

`processed/lade_hz_0823_0829_30aoi/` 包含：

- 真实观测：区域 114/124/30，各取连续 7 天均有订单的前 10 个 AOI；配送量、
  AOI 坐标和快递员接单 GPS 来自 LaDe-D。
- 代理节点：三个卫星位置为各区域接单 GPS 中位数；中心仓使用杭州萧山机场旧货运
  中心公开坐标作为明确标注的代理点。
- 道路网络：`distance_km.csv` 与 `duration_min.csv` 是一次 OSRM/OpenStreetMap
  道路表快照，保留原始响应 `osrm_table_response.json`。
- 设计参数：最终回收率 97%，滞后概率为 1/2/3/4 天对应
  0.15/0.45/0.30/0.10；回收时间窗为下午至晚间。这些不是 LaDe 观测字段。

每个实例 CSV 都保留 `delivery_container_count` 与 `pickup_container_count`，车辆容量
按实际箱数聚合；旧数据缺少这些列时仍按每客户 1 箱运行。

## 重建命令

```bash
python scripts/build_lade_real_case.py --fetch-osrm
python scripts/audit_r3pack_dataset.py
```

构建脚本默认只读取杭州分片，不需要下载完整的 LaDe 数据集。
