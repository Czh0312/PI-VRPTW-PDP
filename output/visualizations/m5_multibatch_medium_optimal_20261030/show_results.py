"""Verify and visualize historical fixed-pool, distance-optimal M5 results."""
from collections import Counter, defaultdict
import csv
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
OUT = Path(__file__).resolve().parent
STUDY = ROOT / 'output/experiments/benchmark27_20261016_cost_update_v1'
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
    seconds = int(ticks)
    return f'{seconds//3600:02d}:{seconds//60%60:02d}:{seconds%60:02d}'


manifest = read(STUDY/'manifest.json')
assert sha(STUDY/'manifest.json') == read(STUDY/'manifest.sha256.json')['sha256']
packed = read(STUDY/'packed_artifacts.json')['files']
cases = {}
for case_id in ('M01', 'M04'):
    folder = STUDY/f'runs/{case_id}_m5_exact_s42/attempt_001'
    for name, digest in read(folder/'completion.json')['hashes'].items():
        assert sha(folder/name) == digest, name
    for relative, digest in packed.items():
        if relative.startswith(f'inputs/{case_id}_m5_'):
            assert sha(STUDY/relative) == digest, relative
    result, row = read(folder/'result.json'), read(folder/'row.json')
    case_path = STUDY/row['case_path']
    assert sha(case_path) == manifest['input_hashes'][row['case_path']]
    config, fleet = load_routing_case(case_path, use_pool_config=False)
    assert result['objective_mode'] == 'distance'
    # Explicitly decode the historical objective recorded in the saved result;
    # current M5 defaults to pool/cost optimization. No input file is modified.
    config = {**config, 'objective_mode': result['objective_mode']}
    problem = load_instance(config, fleet)
    audit = audit_solution(problem, result)
    assert audit['valid'] and audit['periodic_replay']['valid'], audit.get('errors')
    assert result['info']['status'] == 'OPTIMAL' and result['info']['proven_optimal']
    assert result['info']['proof_scope'] == 'full_integer_time_multibatch_indexed'
    assert row['proven_optimal'] and row['mip_gap'] == 0
    assert abs(audit['objective'] - result['info']['objective']) < 1e-7
    assert abs(audit['objective'] - row['best_bound']) < 1e-7
    assert len(problem.operating_policy.batches) == 3
    (OUT/f'{case_id}_original_result.json').write_bytes((folder/'result.json').read_bytes())
    (OUT/f'{case_id}_independent_audit.json').write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding='utf-8')
    cases[case_id] = problem, result, row, audit

problem, result, row, audit = cases['M01']
nodes = problem.network.nodes
font_path = Path('C:/Windows/Fonts/msyh.ttc')
font_manager.fontManager.addfont(font_path)
plt.rcParams.update({'font.family': font_manager.FontProperties(fname=str(font_path)).get_name(),
    'axes.unicode_minus': False, 'font.size': 10, 'pdf.fonttype': 42, 'savefig.facecolor': 'white'})
batch_colors = dict(zip(('b1', 'b2', 'b3'), ('#197E9B', '#D58428', '#8056AD')))
vehicle_colors = ('#147D92', '#9A5B31')
all_customers = sorted({order.customer for order in problem.orders})


def sequence(trip, layer):
    base = problem.depot if layer == 'e1' else trip['satellite_id']
    return [base, *(e['site_id'] if layer == 'e1' else problem.order_map[e['order_id']].customer
                    for e in trip['events']), base]


def arrows(ax, visits, color, style='-', offset=0):
    counts = Counter()
    for a, b in zip(visits, visits[1:]):
        if a == b:
            continue
        key = tuple(sorted((a, b)))
        counts[key] += 1
        radius = .055*(1+offset+counts[key]*.35)
        ax.add_patch(FancyArrowPatch((nodes[a].x, nodes[a].y), (nodes[b].x, nodes[b].y),
            connectionstyle=f'arc3,rad={radius}', arrowstyle='-|>', mutation_scale=11,
            color=color, linewidth=1.6, linestyle=style, alpha=.84, shrinkA=6, shrinkB=6, zorder=3))


def base_map(ax, customers=(), sites=(), depot=False):
    for customer in customers:
        node = nodes[customer]
        ax.scatter(node.x, node.y, s=34, color='#334155', edgecolors='white', linewidth=.7, zorder=6)
        ax.annotate(customer, (node.x, node.y), xytext=(6, 5), textcoords='offset points',
            fontsize=8, color='#334155', zorder=7,
            bbox=dict(facecolor='white', edgecolor='none', alpha=.8, pad=.4))
    for site in sites:
        node = nodes[site]
        ax.scatter(node.x, node.y, marker='^', s=150, color='#17475C', edgecolors='white', zorder=8)
        ax.annotate(site, (node.x, node.y), xytext=(-7, -15), textcoords='offset points',
                    color='#17475C', fontweight='bold', zorder=9)
    if depot:
        node = nodes[problem.depot]
        ax.scatter(node.x, node.y, marker='s', s=130, color='#B54444', edgecolors='white', zorder=8)
        ax.annotate('D0 中心仓', (node.x, node.y), xytext=(-8, 8), textcoords='offset points',
                    fontsize=9, color='#9F3232', fontweight='bold', zorder=9)
    selected = [nodes[x] for x in [*customers, *sites, *([problem.depot] if depot else [])]]
    x0, x1 = min(n.x for n in selected), max(n.x for n in selected)
    y0, y1 = min(n.y for n in selected), max(n.y for n in selected)
    margin = max(6, .15*max(x1-x0, y1-y0))
    ax.set(xlim=(x0-margin, x1+margin), ylim=(y0-margin, y1+margin), xlabel='X 坐标', ylabel='Y 坐标')
    ax.set_aspect('equal', adjustable='box')
    ax.grid(color='#DCE4E9', alpha=.75, linewidth=.7)
    ax.set_axisbelow(True)
    for spine in ax.spines.values():
        spine.set_color('#CAD5DF')


fig, axes = plt.subplots(2, 2, figsize=(16, 12.5), facecolor='white')
fig.subplots_adjust(top=.83, bottom=.10, left=.06, right=.97, hspace=.35, wspace=.24)
fig.text(.06, .958, '模型5 · 中规模 M01 · 三批次最优路线', fontsize=22, color='#17384A', fontweight='bold')
fig.text(.06, .914, '历史固定箱池 / 距离目标版本：20顾客、3个前置仓、3个批次、32只容器', fontsize=12, color='#506575')
fig.text(.06, .878, f'总距离 {audit["objective"]:.6f}  |  GAP 0  |  原记录总耗时 {row["solve_wall_seconds"]:.2f}秒  |  重新审计通过',
         fontsize=11, color='#237456')
ax = axes[0, 0]
base_map(ax, sites=problem.satellites, depot=True)
for index, trip in enumerate(result['e1_trips']):
    arrows(ax, sequence(trip, 'e1'), batch_colors[trip['batch_id']], offset=index)
ax.set_title('一级车：按批次补货与取回空箱（3趟）', fontsize=12, loc='left', color='#17384A', pad=12)
ax.legend(handles=[Line2D([0], [0], color=c, linewidth=2, label=b) for b, c in batch_colors.items()],
          loc='upper left', frameon=False, ncol=3, fontsize=9)
for ax, site in zip((axes[0, 1], axes[1, 0], axes[1, 1]), problem.satellites):
    local = [o.customer for o in problem.orders if o.satellite == site]
    base_map(ax, customers=local, sites=[site])
    trips = [t for t in result['e2_trips'] if t['satellite_id'] == site]
    handles = []
    for trip in trips:
        color = vehicle_colors[trip['vehicle_id']]
        style = '-' if trip['trip_index'] == 0 else '--'
        arrows(ax, sequence(trip, 'e2'), color, style, offset=trip['trip_index'])
        handles.append(Line2D([0], [0], color=color, linestyle=style, linewidth=1.7,
                             label=f'V{trip["vehicle_id"]+1} 第{trip["trip_index"]+1}趟'))
    ax.set_title(f'{site} 二级路线：{len(local)}顾客、{len(trips)}趟', fontsize=12,
                 loc='left', color='#17384A', pad=12)
    ax.legend(handles=handles, fontsize=8, loc='upper left', frameon=True, framealpha=.9, ncol=2)
fig.text(.06, .042, '实线/虚线区分同车的不同趟次。箭头遵循原解；配送与回收同点可能重叠，时刻见调度图及事件表。',
         fontsize=10, color='#647789')
fig.text(.06, .018, '旧版三批次总距离最优，不代表当前费用与箱池联合优化版本的最优值；本次未重新运行优化。',
         fontsize=10, color='#647789')
fig.savefig(OUT/'M5_M01_routes.png', dpi=180, bbox_inches='tight')
plt.close(fig)

vehicle_groups = defaultdict(list)
for layer in ('e1', 'e2'):
    for trip in result[layer+'_trips']:
        key = (layer, trip.get('satellite_id', ''), trip['vehicle_id'])
        vehicle_groups[key].append(trip)
keys = sorted(vehicle_groups)
fig, ax = plt.subplots(figsize=(15, 7.7), facecolor='white')
fig.subplots_adjust(left=.15, right=.97, top=.79, bottom=.19)
fig.text(.04, .95, 'M01 三批次调度：补货、送取与同车多趟', fontsize=20, color='#17384A', fontweight='bold')
fig.text(.04, .90, '所有时刻来自保存解；条形包含运输、服务和等待，不是计算耗时。', fontsize=11, color='#506575')
for batch in problem.operating_policy.batches:
    ax.axvspan(batch.start/60, batch.end/60, color=batch_colors[batch.id], alpha=.06)
    ax.axvline(batch.start/60, color='#A1ADB9', linewidth=.8)
    ax.axvline(batch.cutoff/60, color=batch_colors[batch.id], linewidth=1.1, linestyle=':')
    ax.text((batch.start+batch.end)/120, 1.055,
            f'{batch.id}  {clock(batch.start)[:5]}–{clock(batch.end)[:5]}',
            transform=ax.get_xaxis_transform(), ha='center', fontsize=10,
            color=batch_colors[batch.id], fontweight='bold')
labels = []
for y, key in enumerate(keys):
    layer, site, vehicle = key
    labels.append(f'一级 V{vehicle+1}' if layer == 'e1' else f'{site} / V{vehicle+1}')
    for trip in vehicle_groups[key]:
        color = batch_colors[trip['batch_id']] if layer == 'e1' else '#A8BBC7'
        ax.broken_barh([(trip['departure']/60, (trip['return_complete']-trip['departure'])/60)],
                      (y-.24, .48), facecolors=color, alpha=.75, edgecolors='white', linewidth=.7)
        if layer == 'e1':
            ax.text((trip['departure']+trip['return_complete'])/120, y, trip['batch_id'],
                    ha='center', va='center', color='white', fontsize=9, fontweight='bold')
        else:
            for event in trip['events']:
                batch = problem.order_map[event['order_id']].batch
                ax.scatter(event['start']/60, y, s=27, marker='o' if event['kind']=='d' else '^',
                           color=batch_colors[batch], edgecolors='white', linewidth=.5, zorder=4)
ax.set(yticks=range(len(keys)), yticklabels=labels, xlim=(480,1260), ylim=(len(keys)-.5,-.5))
ax.set_xticks(range(480,1261,60), [f'{x//60:02d}:00' for x in range(480,1261,60)])
ax.grid(axis='x', color='#DCE4E9', linewidth=.7)
ax.set_axisbelow(True)
ax.set_xlabel('当日时间')
handles = [Line2D([0],[0], marker='o', color='none', markerfacecolor='#647789', label='配送服务开始'),
           Line2D([0],[0], marker='^', color='none', markerfacecolor='#647789', label='回收服务开始'),
           Line2D([0],[0], color='#647789', linestyle=':', label='一级取回空箱截点')]
fig.legend(handles=handles, loc='lower left', bbox_to_anchor=(.14,.075), ncol=3, frameon=False, fontsize=10)
fig.text(.04,.035,'二级事件颜色表示订单原配送批次；实际车趟可以跨越批次边界。一级车辆编号沿用原解并从1开始显示。',
         fontsize=10,color='#647789')
fig.savefig(OUT/'M5_M01_schedule.png', dpi=180, bbox_inches='tight')
plt.close(fig)

with (OUT/'M01_route_events.csv').open('w', newline='', encoding='utf-8-sig') as stream:
    writer = csv.writer(stream)
    writer.writerow(['层级','原始趟次ID','显示车辆','服务次序','顾客或仓','订单','配送批次','送取','开始时刻','箱量'])
    for layer in ('e1','e2'):
        for trip in result[layer+'_trips']:
            for i,event in enumerate(trip['events'],1):
                order = problem.order_map.get(event.get('order_id'))
                writer.writerow([layer,trip['trip_id'],trip['vehicle_id']+1,i,
                    order.customer if order else event['site_id'],event.get('order_id',''),
                    order.batch if order else trip['batch_id'],'送' if event['kind']=='d' else '收',
                    clock(event['start']),event['quantity']])

summaries = []
for case_id, (p,r,record,checked) in cases.items():
    batches = [dict(batch=b.id, start=clock(b.start), cutoff=clock(b.cutoff), end=clock(b.end),
        orders=sum(o.batch==b.id for o in p.orders), containers=p.batch_demands[b.id])
        for b in p.operating_policy.batches]
    summaries.append(dict(case=case_id, model=5, scope='historical_fixed_pool_distance',
        original_study='benchmark27_20261016_cost_update_v1', objective_mode='distance',
        customers=len({o.customer for o in p.orders}), orders=len(p.orders), batches=batches,
        pool=p.operating_policy.container_pool_size, distance=checked['objective'],
        e1_distance=checked['distances']['e1'], e2_distance=checked['distances']['e2'],
        e1_trips=len(r['e1_trips']), e2_trips=len(r['e2_trips']),
        status=r['info']['status'], gap=r['info']['mip_gap'], seed=record['seed'],
        total_seconds=record['solve_wall_seconds'], solver_runtime=record['solver_runtime'],
        independent_audit_valid=checked['valid'], periodic_valid=checked['periodic_replay']['valid'],
        source_sha256=sha(OUT/f'{case_id}_original_result.json')))
(OUT/'verification.json').write_text(json.dumps(summaries,ensure_ascii=False,indent=2),encoding='utf-8')
lines = ['# 多批次中规模：历史模型5距离最优方案','','本次只核对和绘图，没有重新运行优化、增加随机种子或改变业务数据。',
    '历史记录为固定箱池、三批次、总距离最小化。当前模型5默认费用与箱池联合优化，目标不同。',
    '审计时仅在内存中按原结果声明恢复objective_mode=distance，冻结输入和原始结果逐字节保持；'
    '已核验登记清单、输入、完成文件和结果哈希，并用当前独立回放器重新检查两周期。','',
    '| 算例 | 顾客 | 批次 | 固定箱池 | 最优总距离 | 原记录总秒数 | 状态 |',
    '| --- | ---: | ---: | ---: | ---: | ---: | --- |']
for s in summaries:
    lines.append(f'| {s["case"]} | {s["customers"]} | 3 | {s["pool"]} | {s["distance"]:.6f} | {s["total_seconds"]:.3f} | OPTIMAL / GAP 0 |')
lines += ['', '## M01批次安排','','| 批次 | 开始装载 | 一级取箱截点 | 结束 | 订单 | 需求箱量 |',
    '| --- | --- | --- | --- | ---: | ---: |']
for b in summaries[0]['batches']:
    lines.append(f'| {b["batch"]} | {b["start"]} | {b["cutoff"]} | {b["end"]} | {b["orders"]} | {b["containers"]} |')
lines += ['', '![分层分仓路线](M5_M01_routes.png)', '', '![原解调度](M5_M01_schedule.png)', '',
    'M01一级3趟、二级10趟；同车多趟串行、配送与回收事件和原始服务时刻均保留。'
    '未把原来的距离最优解释为按当前价格重新计费后的成本最优。', '',
    '当前查到的模型八20顾客M01记录仍未证明全域最优；见'
    '`output/experiments/m8_m01_compact_prefix_20261029_v2/report.md`。', '']
(OUT/'report.md').write_text('\n'.join(lines),encoding='utf-8')
print(json.dumps(summaries,ensure_ascii=False),flush=True)
