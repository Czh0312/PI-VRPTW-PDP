# gen_table.py
# -*- coding: utf-8 -*-
"""
按算例类型分组的对比表格：
- 每个模型显示 Exact / ALNS 两列
- M3、M4 与 M2 对应求解器对比 (Gap%)
- 最优值标绿，N/A (求解失败) 标红
"""
import re
from pathlib import Path
from collections import defaultdict
from statistics import mean

OUTPUT_DIR = Path(__file__).resolve().parent / "output" / "comparison"

# ── 解析日志 ──
log_path = Path("/private/tmp/claude-501/-Users-chenzeheng-Desktop-2E-VRPTW-PDD/"
                "dd5c9619-e8a3-4db5-97e3-fffbcf391a45/tasks/bz46ghq3s.output")
with open(log_path) as f:
    lines = f.readlines()

results = []
current_scale = None
for line in lines:
    m = re.search(r'Scale: (\d+_customers)', line)
    if m:
        current_scale = m.group(1)
    m2 = re.match(
        r'\s+\[\d+/960\]\s+(\w+)\|M(\d)\|(\w+)\s+.*->\s+(\w+)\s+cost=(\S+)\s+t=(\S+)s',
        line)
    if m2 and current_scale:
        inst, model, solver, status, cost_s, _ = m2.groups()
        cost = float(cost_s) if cost_s != 'N/A' else None
        results.append((current_scale, inst, int(model), solver, cost, status))

# ── 分组 ──
def inst_type(name):
    m = re.match(r'([A-Z]+\d)\d+', name)
    return m.group(1) if m else name

grouped = defaultdict(list)
for scale, inst, model, solver, cost, status in results:
    if cost is not None and status == 'OK':
        grouped[(scale, inst_type(inst), model, solver)].append(cost)

def avg(key):
    vals = grouped.get(key, [])
    return mean(vals) if vals else None

# ── 表格配置 ──
scales_order = ['5_customers', '10_customers', '50_customers', '100_customers']
scale_labels = {'5_customers': '5', '10_customers': '10',
                '50_customers': '50', '100_customers': '100'}
types_order = ['C1', 'C2', 'R1', 'R2', 'RC1', 'RC2']
models = [1, 2, 3, 4]
solvers = ['exact', 'alns']

# 列头: 每个模型 Exact + ALNS, 然后 Gap 列
col_labels = []
for m in [1, 2, 3, 4]:
    col_labels.extend([f"M{m}\nExact", f"M{m}\nALNS"])
col_labels.append("M3 vs M2\nExact Gap%")
col_labels.append("M3 vs M2\nALNS Gap%")
col_labels.append("M4 vs M2\nExact Gap%")
col_labels.append("M4 vs M2\nALNS Gap%")
n_cols = len(col_labels)

# ── 构建每行数据 ──
rows = []  # (scale_label, type, { (model, solver): avg_cost })
for s in scales_order:
    for tp in types_order:
        row = {}
        for m in models:
            for sv in solvers:
                row[(m, sv)] = avg((s, tp, m, sv))
        if any(v is not None for v in row.values()):
            rows.append((scale_labels[s], tp, row))

# ── 绘图 ──
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

plt.rcParams.update({
    "font.family": "sans-serif",
    "font.sans-serif": ["Helvetica Neue", "Arial", "PingFang SC", "SimHei"],
    "axes.unicode_minus": False,
    "figure.facecolor": "white",
})

cell_text = []
cell_colors = []

for sl, tp, row in rows:
    text_row = []
    color_row = []

    # 8 列: M1-Exact, M1-ALNS, M2-Exact, M2-ALNS, ...
    for m in models:
        for sv in solvers:
            v = row.get((m, sv))
            if v is not None:
                text_row.append(f"{v:.1f}")
                color_row.append("white")
            else:
                text_row.append("N/A")
                color_row.append("#f8d7da")

    # 找该行 ALNS 的最优值（只标绿 ALNS 列）
    alns_vals = [row.get((m, 'alns')) for m in models]
    alns_valid = [v for v in alns_vals if v is not None]
    alns_best = min(alns_valid) if alns_valid else None
    # 标绿 ALNS 最优
    for i, m in enumerate(models):
        idx = i * 2 + 1  # ALNS 列索引
        v = row.get((m, 'alns'))
        if v is not None and alns_best is not None and abs(v - alns_best) < 0.5:
            color_row[idx] = "#d4edda"

    # Exact 列也标绿最优
    exact_vals = [row.get((m, 'exact')) for m in models]
    exact_valid = [v for v in exact_vals if v is not None]
    exact_best = min(exact_valid) if exact_valid else None
    for i, m in enumerate(models):
        idx = i * 2  # Exact 列索引
        v = row.get((m, 'exact'))
        if v is not None and exact_best is not None and abs(v - exact_best) < 0.5:
            color_row[idx] = "#d4edda"

    # Gap: M3 vs M2 (同求解器)
    for sv in solvers:
        m2v = row.get((2, sv))
        m3v = row.get((3, sv))
        if m2v and m3v and m2v > 0:
            gap = (m3v - m2v) / m2v * 100
            sign = "+" if gap >= 0 else ""
            text_row.append(f"{sign}{gap:.1f}%")
            color_row.append("#fff3cd" if gap > 0 else "#d4edda")
        else:
            text_row.append("-")
            color_row.append("#f0f0f0")

    # Gap: M4 vs M2 (同求解器)
    for sv in solvers:
        m2v = row.get((2, sv))
        m4v = row.get((4, sv))
        if m2v and m4v and m2v > 0:
            gap = (m4v - m2v) / m2v * 100
            sign = "+" if gap >= 0 else ""
            text_row.append(f"{sign}{gap:.1f}%")
            color_row.append("#fff3cd" if gap > 0 else "#d4edda")
        else:
            text_row.append("-")
            color_row.append("#f0f0f0")

    cell_text.append(text_row)
    cell_colors.append(color_row)

row_labels = [f"{sl} cust. | {tp}" for sl, tp, _ in rows]

# ── 画表格 ──
fig_height = 2.5 + len(rows) * 0.5
fig, ax = plt.subplots(figsize=(24, fig_height))
ax.axis("off")

table = ax.table(
    cellText=cell_text,
    cellColours=cell_colors,
    colLabels=col_labels,
    rowLabels=row_labels,
    loc="center",
    cellLoc="center",
)
table.auto_set_font_size(False)
table.set_fontsize(8)
table.scale(1.0, 1.7)

# 美化
for (r, c), cell in table.get_celld().items():
    cell.set_edgecolor("#CCCCCC")
    cell.set_linewidth(0.4)
    if r == 0:
        # 列头按模型分组着色
        if c < 8:
            model_idx = c // 2
            header_colors = ["#2b5797", "#c0392b", "#b5651d", "#27753a"]
            cell.set_facecolor(header_colors[model_idx])
        else:
            cell.set_facecolor("#555555")
        cell.set_text_props(fontweight="bold", fontsize=8, color="white")
    if c == -1:
        cell.set_facecolor("#F5F5F5")
        cell.set_text_props(fontweight="bold", fontsize=8)
    # 不同规模间加粗线
    if r > 0 and rows[r - 1][1] == "C1":
        cell.set_edgecolor("#888888")
        cell.set_linewidth(1.0)

ax.set_title(
    "2E-VRPTW-PDD  |  Exact & ALNS Avg. Cost by Instance Type  |  "
    "Green = Best  |  Red = Failed  |  Yellow = Gap%  |  30 inst./scale",
    fontsize=14, fontweight="bold", pad=20, color="#222"
)

plt.tight_layout()
fig_path = OUTPUT_DIR / "comparison_by_instance_type.png"
fig.savefig(fig_path, dpi=250, bbox_inches="tight", facecolor="white")
plt.close()
print(f"Saved: {fig_path}")
