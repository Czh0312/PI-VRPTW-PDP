"""Independently replay one ALNS run and render its actual routes and events."""
from collections import defaultdict
import csv
import hashlib
import json
from pathlib import Path
import sys

PROJECT = Path.cwd()
sys.path.insert(0, str(PROJECT))
from models.common.cycle import load_instance
from models.common.cycle.audit import audit_solution
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
from matplotlib.patches import FancyArrowPatch

ROOT = Path(__file__).resolve().parent


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


result = read(ROOT/'alns/result.json')
manifest = read(ROOT/'alns/manifest.json')
for filename, expected in manifest['input_sha256'].items():
    if hashlib.sha256(Path(filename).read_bytes()).hexdigest() != expected:
        raise ValueError(f'Input changed: {filename}')
for filename, expected in manifest['source_sha256'].items():
    if hashlib.sha256((PROJECT/filename).read_bytes()).hexdigest() != expected:
        raise ValueError(f'Source changed: {filename}')
problem = load_instance(manifest['config'], manifest['fleet'])
audit = audit_solution(problem, result)
if not audit['valid'] or not audit['periodic_replay']['valid']:
    raise ValueError(audit['errors'])
orders = {o.id: o for o in problem.orders}
factor = result['time_tick_seconds']/result.get('time_tick_subdivision', 1)


def clock(ticks):
    seconds = round(ticks*factor)
    return f'{seconds//3600:02d}:{seconds//60%60:02d}:{seconds%60:02d}'


trips = sorted(result['e2_trips'], key=lambda t: (t['satellite_id'], t['vehicle_id'], t['trip_index']))
route_rows, events, owners = [], [], {}
for trip in trips:
    sequence = []
    weight = sum(problem.task(e['order_id'], e['kind'])[1] for e in trip['events'] if e['kind'] == 'd')
    volume = sum(problem.task(e['order_id'], e['kind'])[2] for e in trip['events'] if e['kind'] == 'd')
    peak_weight, peak_volume = weight, volume
    for number, event in enumerate(trip['events'], 1):
        order = orders[event['order_id']]
        kind = event['kind']
        sequence.append(f'{order.customer}{"送" if kind == "d" else "取"}')
        owners[order.id, kind] = dict(satellite=trip['satellite_id'], vehicle=trip['vehicle_id'], trip=trip['trip_index'])
        _, w, v = problem.task(order.id, kind)
        weight += w if kind == 'p' else -w
        volume += v if kind == 'p' else -v
        peak_weight, peak_volume = max(peak_weight, weight), max(peak_volume, volume)
        events.append(dict(satellite=trip['satellite_id'], vehicle=trip['vehicle_id'], trip=trip['trip_index'],
            sequence=number, customer=order.customer, task='配送' if kind == 'd' else '回收',
            start=clock(event['start']), service_minutes=problem.operating_policy.minutes(order.service_time)))
    route_rows.append(dict(satellite=trip['satellite_id'], vehicle=trip['vehicle_id'], trip=trip['trip_index'],
        departure=clock(trip['departure']), return_complete=clock(trip['return_complete']),
        sequence=sequence, peak_weight=peak_weight, peak_volume=peak_volume))
cross_vehicle = [dict(customer=o.customer, delivery=owners[o.id, 'd'], pickup=owners[o.id, 'p'])
                 for o in problem.orders if owners[o.id, 'd']['vehicle'] != owners[o.id, 'p']['vehicle']]

lines = ['# M01：模型8 ALNS 实际路线', '',
    '20位顾客，最新中规模车辆配置。仅运行ALNS，预算30秒，seed=42，候选K=1～20。使用项目ALNS-only入口，未完成Gurobi许可证验证，未运行本模型的精确求解。',
    f'状态：{result["info"]["status"]}；成本：{result["info"]["objective"]:.4f}（合成算例单位）；'
    f'耗时：{result["info"]["runtime"]:.3f}秒；K={result["cycle_batch_count"]}，N={result["container_pool_size"]}。',
    f'总距离：{result["total_distance"]:.4f}（算例坐标单位）。实际一级车{audit["e1_vehicle_count"]}辆/'
    f'{audit["e1_trip_count"]}趟；二级车{audit["e2_vehicle_count"]}辆/{audit["e2_trip_count"]}趟。',
    '独立重放检查时间窗、最低使用时间、车辆衔接、载重、体积、箱量守恒、清洗和连续两个周期，均通过。',
    '这是一个种子的一次可行解，不声明最优。所有任务时间均为服务开始时间。', '',
    '## 一级路线', '']
for trip in result['e1_trips']:
    sequence = ' → '.join(f'{e["site_id"]}{"送" if e["kind"] == "d" else "取"}{e["quantity"]}箱'
                         f'({clock(e["start"])})' for e in trip['events'])
    lines.append(f'- 车{trip["vehicle_id"]}：总仓({clock(trip["departure"])}) → {sequence} → '
                 f'总仓卸完({clock(trip["return_complete"])})。')
lines += ['', '## 二级路线', '', '每条路线均从所属站出发并返回；车辆编号在每个站内单独计数。', '',
    '| 配送站 | 车辆/趟次 | 出发 | 返回卸完 | 送取顺序 | 最大载重/100 | 最大体积/5 |',
    '| --- | --- | --- | --- | --- | ---: | ---: |']
for row in route_rows:
    lines.append(f'| {row["satellite"]} | {row["vehicle"]}/{row["trip"]} | {row["departure"]} | '
        f'{row["return_complete"]} | {" → ".join(row["sequence"])} | {row["peak_weight"]:.1f} | {row["peak_volume"]:.1f} |')
lines += ['', '## 跨车辆送取', '']
if cross_vehicle:
    for row in cross_vehicle:
        lines.append(f'- {row["customer"]}：{row["delivery"]["satellite"]}车{row["delivery"]["vehicle"]}配送，'
                     f'车{row["pickup"]["vehicle"]}回收。')
else:
    lines.append('本次未出现不同车辆分别配送和回收同一顾客。')
lines += ['', '## 逐任务时间表', '', '| 站点 | 车辆 | 趟次 | 顺序 | 顾客 | 任务 | 开始时间 |',
          '| --- | ---: | ---: | ---: | --- | --- | --- |']
for event in events:
    lines.append(f'| {event["satellite"]} | {event["vehicle"]} | {event["trip"]} | {event["sequence"]} | '
                 f'{event["customer"]} | {event["task"]} | {event["start"]} |')
lines += ['', '车辆等待与工时费用仍为0，因此部分路线含较长等待。路线中的相邻送取任务不表示立即回收。',
          '图中D=配送，P=回收；空间图的实线终点为配送、虚线终点为回收，路线文字顺序和时间表为准。',
          '', '![二级路线](e2_routes.png)', '', '![任务时间轴](timeline.png)', '']
(ROOT/'routes.md').write_text('\n'.join(lines)+'\n', encoding='utf-8')
with (ROOT/'events.csv').open('w', encoding='utf-8-sig', newline='') as handle:
    writer = csv.DictWriter(handle, fieldnames=list(events[0]))
    writer.writeheader()
    writer.writerows(events)

palette = ['#1976D2', '#D55E00', '#009E73']
sites = sorted({t['satellite_id'] for t in trips})
figure, axes = plt.subplots(1, len(sites), figsize=(6*len(sites), 6), squeeze=False, constrained_layout=True)
for ax, site in zip(axes[0], sites):
    local = [o for o in problem.orders if o.satellite == site]
    station = problem.network.nodes[site]
    ax.scatter([station.x], [station.y], marker='^', s=160, color='#202020', zorder=5)
    ax.annotate(site, (station.x, station.y), xytext=(6, 8), textcoords='offset points', fontsize=11, weight='bold')
    for order in local:
        node = problem.network.nodes[order.customer]
        ax.scatter([node.x], [node.y], s=45, color='#555555', zorder=4)
        ax.annotate(order.customer, (node.x, node.y), xytext=(5, 5), textcoords='offset points', fontsize=10)
    for trip in [t for t in trips if t['satellite_id'] == site]:
        color = palette[trip['vehicle_id'] % len(palette)]
        previous = station
        for event in trip['events']:
            node = problem.network.nodes[orders[event['order_id']].customer]
            if node.id != previous.id:
                arrow = FancyArrowPatch((previous.x, previous.y), (node.x, node.y),
                    arrowstyle='-|>', mutation_scale=12, color=color,
                    linestyle='-' if event['kind'] == 'd' else '--', linewidth=1.7,
                    connectionstyle=f'arc3,rad={0.05*(trip["vehicle_id"]+1)}', alpha=.85, zorder=2)
                ax.add_patch(arrow)
            previous = node
        ax.add_patch(FancyArrowPatch((previous.x, previous.y), (station.x, station.y),
            arrowstyle='-|>', mutation_scale=12, color=color, linestyle=':', linewidth=1.5,
            connectionstyle='arc3,rad=0.04', alpha=.85))
    handles = [Line2D([0], [0], color=palette[v], lw=2, label=f'Vehicle {v}')
               for v in sorted({t['vehicle_id'] for t in trips if t['satellite_id'] == site})]
    ax.legend(handles=handles, loc='best', fontsize=9)
    ax.set_title(f'{site} | {len(local)} customers', fontsize=13)
    ax.set_xlabel('Benchmark x')
    ax.set_ylabel('Benchmark y')
    ax.set_aspect('equal', adjustable='datalim')
    ax.margins(.18)
    ax.grid(alpha=.15)
figure.suptitle('M01 | Model 8 ALNS | 20 customers | actual vehicle routes', fontsize=15)
figure.savefig(ROOT/'e2_routes.png', dpi=160)
plt.close(figure)

figure, ax = plt.subplots(figsize=(17, max(5, len(trips)*.9)), constrained_layout=True)
for i, trip in enumerate(trips):
    color = palette[trip['vehicle_id'] % len(palette)]
    start = trip['departure']*factor/3600
    finish = trip['return_complete']*factor/3600
    ax.plot([start, finish], [i, i], lw=7, alpha=.18, color=color, solid_capstyle='round')
    for j, event in enumerate(trip['events']):
        hour = event['start']*factor/3600
        label = f'{orders[event["order_id"]].customer} {event["kind"].upper()}'
        ax.scatter([hour], [i], color=color, s=65, marker='o' if event['kind'] == 'd' else 'v', zorder=3)
        ax.annotate(label, (hour, i), xytext=(0, 13 if j % 2 == 0 else -21),
                    textcoords='offset points', ha='center', va='center', fontsize=8.5)
ax.set_yticks(range(len(trips)), [f'{t["satellite_id"]} / V{t["vehicle_id"]} / T{t["trip_index"]}' for t in trips])
ax.set_ylim(-.65, len(trips)-.3)
ax.invert_yaxis()
ax.set_xlim(8, 21)
ax.set_xticks(range(8, 22), [f'{h:02d}:00' for h in range(8, 22)])
ax.grid(axis='x', alpha=.25)
ax.set_title('M01 | event start times | D: delivery, P: pickup | faint line: occupied trip', fontsize=13)
ax.legend(handles=[Line2D([0], [0], marker='o', color='#555555', ls='', label='Delivery'),
                   Line2D([0], [0], marker='v', color='#555555', ls='', label='Pickup')], loc='upper right')
figure.savefig(ROOT/'timeline.png', dpi=160)
plt.close(figure)

summary = dict(case='M01', customers=len(problem.orders), model=8, solver='alns',
    seed=42, budget_seconds=30, runtime=result['info']['runtime'], status=result['info']['status'],
    objective=result['info']['objective'], K=result['cycle_batch_count'], N=result['container_pool_size'],
    total_distance=result['total_distance'], proven_optimal=False, gurobi_checked=False,
    audit_valid=True, periodic_replay=True, e1_vehicles=audit['e1_vehicle_count'],
    e2_vehicles=audit['e2_vehicle_count'], routes=route_rows, cross_vehicle_customers=cross_vehicle)
(ROOT/'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
print(json.dumps(summary, ensure_ascii=False, indent=2))
