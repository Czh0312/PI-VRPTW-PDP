"""Render the saved, audited M07 exact schedule without solving it again."""
from __future__ import annotations

from collections import Counter
import csv
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
OUT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib import font_manager
from matplotlib.lines import Line2D
from matplotlib.patches import FancyArrowPatch

from tools.configuration.loader import load_routing_case
from models.common.cycle.instance import load_instance
from models.common.cycle.audit import audit_solution


def read(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def clock(ticks):
    seconds = int(ticks * problem.operating_policy.time_tick_seconds)
    return f'{seconds//3600:02d}:{seconds//60%60:02d}:{seconds%60:02d}'


STUDY = ROOT / 'output/experiments/m3_network_strengthening_20261029_v1'
RUN = STUDY / 'runs/M07_after_s42_300s'
plan = read(STUDY / 'plan.json')
for name, expected in read(RUN / 'completion.json')['files'].items():
    assert sha(RUN / name) == expected, name
checked_inputs = {}
for name, expected in plan['hashes'].items():
    if name.startswith('data/') and '/medium/M07/' in name:
        assert sha(ROOT / name) == expected, name
        checked_inputs[name] = expected
assert len(checked_inputs) >= 7
result, row = read(RUN / 'result.json'), read(RUN / 'row.json')
cfg, fleet = load_routing_case(ROOT / row['case_path'], use_pool_config=False)
problem = load_instance(cfg, fleet)
audit = audit_solution(problem, result)
assert audit['valid'] and audit['periodic_replay']['valid'], audit.get('errors')
assert result['info']['status'] == 'OPTIMAL' and row['optimal'] and row['gap'] == 0
assert abs(audit['objective'] - row['objective']) < 1e-7
log = (RUN / '04_full_model.log').read_text(encoding='utf-8')
assert 'Optimal solution found (tolerance 0.00e+00)' in log
assert 'gap 0.0000%' in log

font_path = Path('C:/Windows/Fonts/msyh.ttc')
assert font_path.exists(), 'A Chinese font is required for these figures'
font_manager.fontManager.addfont(font_path)
plt.rcParams.update({
    'font.family': font_manager.FontProperties(fname=str(font_path)).get_name(),
    'axes.unicode_minus': False, 'font.size': 10, 'axes.spines.top': False,
    'axes.spines.right': False, 'pdf.fonttype': 42, 'svg.fonttype': 'path',
    'savefig.facecolor': 'white',
})
nodes = problem.network.nodes
customers = sorted({order.customer for order in problem.orders}, key=lambda s: int(s[1:]))
colors = ['#147D92', '#D87C18', '#8055AF', '#38865C']
trips = sorted(result['e2_trips'], key=lambda t: (t['satellite_id'], t['vehicle_id'], t['trip_index']))
assert len(trips) == 4 and len(customers) == 20


def physical_sequence(trip, layer):
    base = problem.depot if layer == 'e1' else trip['satellite_id']
    events = [event['site_id'] if layer == 'e1' else problem.order_map[event['order_id']].customer
              for event in trip['events']]
    return [base, *events, base]


def draw_path(ax, sequence, color, *, width=1.5, bend=.045):
    used = Counter()
    for a, b in zip(sequence, sequence[1:]):
        if a == b:
            continue
        key = tuple(sorted((a, b)))
        used[key] += 1
        radius = bend * (1 + .6 * (used[key] - 1))
        ax.add_patch(FancyArrowPatch(
            (nodes[a].x, nodes[a].y), (nodes[b].x, nodes[b].y),
            arrowstyle='-|>', connectionstyle=f'arc3,rad={radius}',
            mutation_scale=12, linewidth=width, color=color, alpha=.86,
            shrinkA=5, shrinkB=5, zorder=3,
        ))


LABEL_OFFSETS = {'C21': (-8, 9), 'C39': (8, -1), 'C25': (-7, 5),
                 'C51': (7, 5), 'C52': (7, -13), 'C40': (10, -4),
                 'C0': (-10, -8), 'C20': (8, 9), 'C11': (7, -11), 'C27': (-8, -12)}
DETAIL_OFFSETS = {'C8': (-10, 10), 'C29': (12, 10), 'C54': (10, -16),
                  'C0': (-9, -13), 'C40': (14, -3), 'C20': (10, 10),
                  'C51': (12, 17), 'C52': (12, -28), 'C39': (-6, -28),
                  'C21': (14, 16), 'C25': (-11, 16), 'C11': (14, -6), 'C27': (-8, -23)}


def background(ax, *, depot=False, muted=False, task_labels=None):
    for customer in customers:
        node = nodes[customer]
        own = not muted or customer in (task_labels or {})
        ax.scatter(node.x, node.y, s=42 if own else 20, color='#334155' if own else '#D3DCE3',
                   edgecolor='white', linewidth=.7, zorder=5)
        if own:
            dx, dy = LABEL_OFFSETS.get(customer, (6, 5))
            if task_labels is not None:
                dx, dy = DETAIL_OFFSETS.get(customer, (dx, dy))
            label = customer
            if task_labels is not None:
                label += '\n' + ' / '.join(task_labels[customer])
            ax.annotate(label, (node.x, node.y), xytext=(dx, dy), textcoords='offset points',
                        ha='right' if dx < 0 else 'left', fontsize=8.5 if task_labels is None else 8,
                        color='#1E293B', zorder=7,
                        bbox=dict(facecolor='white', alpha=.76, edgecolor='none', pad=.6))
    for site in problem.satellites:
        node = nodes[site]
        ax.scatter(node.x, node.y, marker='^', s=190, c='#17475C', edgecolor='white', linewidth=1, zorder=8)
        ax.annotate(f'{site} 前置仓', (node.x, node.y), xytext=(-9, -17), textcoords='offset points',
                    fontsize=10, fontweight='bold', color='#17475C', zorder=9, ha='center')
    if depot:
        node = nodes[problem.depot]
        ax.scatter(node.x, node.y, marker='s', s=150, color='#C84545', edgecolor='white', linewidth=1, zorder=8)
        ax.annotate(f'{problem.depot} 中心仓', (node.x, node.y), xytext=(-3, 10), textcoords='offset points',
                    ha='center', fontsize=10, fontweight='bold', color='#A33535')
    ax.set_aspect('equal', adjustable='box')
    ax.set(xlim=(-3, 74 if depot else 56), ylim=(-4, 75 if depot else 57), xlabel='X 坐标', ylabel='Y 坐标')
    ax.grid(color='#DCE3E8', linewidth=.7, alpha=.7)
    ax.set_axisbelow(True)
    for spine in ax.spines.values():
        spine.set_color('#CBD5E1')


def save_figure(fig, name):
    for suffix in ('png', 'pdf', 'svg'):
        fig.savefig(OUT / f'{name}.{suffix}', dpi=180, bbox_inches='tight')
    plt.close(fig)


summary_trips = []
with (OUT / 'M07_route_events.csv').open('w', encoding='utf-8-sig', newline='') as stream:
    writer = csv.DictWriter(stream, fieldnames=['layer', 'trip_id', 'vehicle_id', 'sequence',
        'customer_or_site', 'order_id', 'task', 'service_start', 'quantity'])
    writer.writeheader()
    for layer in ('e1', 'e2'):
        for trip in result[layer + '_trips']:
            for index, event in enumerate(trip['events'], 1):
                writer.writerow(dict(layer=layer, trip_id=trip['trip_id'], vehicle_id=trip['vehicle_id'],
                    sequence=index, customer_or_site=event.get('site_id') or problem.order_map[event['order_id']].customer,
                    order_id=event.get('order_id', ''), task='配送' if event['kind'] == 'd' else '回收',
                    service_start=clock(event['start']), quantity=event['quantity']))

for trip in trips:
    sequence = physical_sequence(trip, 'e2')
    length = sum(problem.distance(a, b) for a, b in zip(sequence, sequence[1:]))
    text = [f"{problem.order_map[event['order_id']].customer}{'送' if event['kind']=='d' else '收'}"
            for event in trip['events']]
    summary_trips.append(dict(vehicle=trip['vehicle_id']+1, source_vehicle_id=trip['vehicle_id'],
        trip_id=trip['trip_id'], sequence=sequence, task_sequence=text, distance=length,
        departure=clock(trip['departure']), return_complete=clock(trip['return_complete'])))

fig = plt.figure(figsize=(13.6, 10), facecolor='white')
fig.text(.055, .95, '模型3  /  中规模 M07：精确最优路线', fontsize=21, fontweight='bold', color='#17384A')
fig.text(.055, .909, '20位顾客 · 20单 · 40次配送/回收任务 · 1个前置仓 · 4辆二级车',
         fontsize=11.5, color='#506575')
ax = fig.add_axes([.05, .16, .61, .69])
background(ax, depot=True)
for trip in result['e1_trips']:
    draw_path(ax, physical_sequence(trip, 'e1'), '#C84545', width=2.2, bend=.04)
for trip, color in zip(trips, colors):
    draw_path(ax, physical_sequence(trip, 'e2'), color, width=1.6)
fig.text(.72, .82, '已证明全局最优', fontsize=17, fontweight='bold', color='#237456')
metrics = [('总求解耗时', f'{row["total_seconds"]:.2f} 秒'),
           ('主模型求解', f'{row["main_mip_seconds"]:.2f} 秒'),
           ('目标成本', f'{row["objective"]:.6f}'), ('最优性缺口', '0.0000%')]
for i, (label, value) in enumerate(metrics):
    y = .755 - i*.061
    fig.text(.72, y, label, fontsize=10.5, color='#647789')
    fig.text(.94, y, value, fontsize=12, ha='right', color='#183B4F', fontweight='bold')
legend = [Line2D([0], [0], color='#C84545', linewidth=2.3, label='一级车：中心仓与前置仓往返')]
for info, color in zip(summary_trips, colors):
    legend.append(Line2D([0], [0], color=color, linewidth=2,
        label=f'二级车 V{info["vehicle"]}：{len(info["task_sequence"])}项任务'))
fig.legend(handles=legend, loc='upper left', bbox_to_anchor=(.704, .51), frameon=False,
           fontsize=10.5, labelspacing=1.0)
fig.text(.72, .255, '原始计算记录\n单线程 · 随机种子42\n一秒整数时钟\n成本与连续两周期审计通过',
         fontsize=10, color='#506575', linespacing=1.8)
fig.text(.055, .085, '箭头按保存解的访问顺序绘制；配送和回收可重复访问同一顾客，详见车辆分图。',
         fontsize=10, color='#506575')
fig.text(.055, .052, '坐标连线为路线示意，距离和费用按原算例计算。本图读取已完成结果，未重新运行优化。',
         fontsize=9, color='#738392')
save_figure(fig, 'M07_route_overview')

fig, axes = plt.subplots(2, 2, figsize=(16, 14), facecolor='white')
fig.subplots_adjust(left=.055, right=.97, bottom=.17, top=.89, hspace=.50, wspace=.16)
fig.suptitle('M07 最优解：逐车路线与任务次序', x=.055, y=.97, ha='left',
             fontsize=22, fontweight='bold', color='#17384A')
fig.text(.055, .927, '送 = 配送，收 = 回收；数字为该车原解中的服务次序。V1–V4对应保存解中的车辆编号0–3。',
         fontsize=11, color='#506575')
for ax, trip, info, color in zip(axes.flat, trips, summary_trips, colors):
    tasks = {}
    for index, event in enumerate(trip['events'], 1):
        customer = problem.order_map[event['order_id']].customer
        tasks.setdefault(customer, []).append(f'{index}{"送" if event["kind"] == "d" else "收"}')
    background(ax, muted=True, task_labels=tasks)
    draw_path(ax, info['sequence'], color, width=2, bend=.06)
    ax.set_title(f'V{info["vehicle"]}  ·  {len(tasks)}位顾客  ·  {len(trip["events"])}项任务',
                 loc='left', fontsize=13, fontweight='bold', color=color, pad=13)
    parts = ['S3', *info['task_sequence'], 'S3']
    wrapped = '\n'.join(('→ ' if i else '') + ' → '.join(parts[i:i+8])
                         for i in range(0, len(parts), 8))
    ax.text(0, -.16, wrapped, transform=ax.transAxes, fontsize=8.8, color='#334155',
            va='top', linespacing=1.8)
fig.text(.055, .037, '图中直线为坐标示意。服务时刻、箱量与原始车辆编号详见 M07_route_events.csv；原解时刻未重新安排。',
         fontsize=10, color='#647789')
save_figure(fig, 'M07_route_details')

summary = dict(case='M07', orders=len(problem.orders), unique_customers=len(customers),
    tasks=sum(len(t['events']) for t in trips), e1_trips=len(result['e1_trips']), e2_trips=len(trips),
    status=result['info']['status'], objective=row['objective'], bound=row['bound'], gap=row['gap'],
    total_seconds=row['total_seconds'], main_mip_seconds=row['main_mip_seconds'],
    proof_seconds=row['proof_seconds'], seed=row['seed'], threads=1,
    independently_audited=True, periodic_replay_valid=True, source_result_sha256=sha(RUN/'result.json'),
    source_result=str((RUN/'result.json').relative_to(ROOT)), checked_input_hashes=checked_inputs,
    e2_route_details=summary_trips, distances=audit['distances'])
(OUT / 'verification.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
(OUT / 'independent_audit.json').write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps({key: value for key, value in summary.items()
                  if key not in ('checked_input_hashes', 'e2_route_details')}, ensure_ascii=False), flush=True)
