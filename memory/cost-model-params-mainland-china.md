---
name: cost-model-params-mainland-china
description: Cost-model params use mainland-China sources; reference/cost/ holds the dossier, transport switched to full-cost per-km
metadata:
  type: feedback
---

For the 2E-VRPTW economic cost model (`utils/economic_cost.py` + `config/economic_cost_config.json` + `generate_cost_analysis.py`), the user wants all base parameters sourced from **mainland China** authoritative data (国家发改委/国家邮政局/新华社/人民日报/中物联/企业官方/国标 GB), each with URL + date + quoted text — not generic or foreign values.

**Why:** Academic project; reviewers expect China-specific, citable sources. Inventing or reusing foreign numbers is unacceptable — items with no reliable public source must be flagged as "未找到可靠来源" and handled as operating assumptions + sensitivity analysis.

**How to apply:** The full source dossier lives in `reference/cost/` (start at `reference/cost/00_README.md`, then `01_transport_fuel.md`, `02_transport_full_cost.md`, `03_single_use_packaging.md`, `04_reusable_packaging.md`). When changing any parameter, update BOTH the config and the matching `reference/cost/` file.

As of 2026-06, key decisions already applied to config:
- Transport switched from fuel-only to **FULL per-km cost**: `e1_cost_yuan_per_km=6.5`, `e2_cost_yuan_per_km=2.2` (fuel-consumption fields kept as reference only). This raises break-even vs the old fuel-only calc, especially when reusable-route distance > single-use.
- Diesel 7.60 yuan/L (NDRC 2026-06-18), single-use disposal 0.3 yuan.
- `loss_rate` (3%) / `cleaning_sorting` (0.8) / `pool_size` (100) are operating assumptions (no precise public data) — always pair with the `loss_rate_sensitivity` sweep.

Related: [[reusable-container-references-and-thesis]].
