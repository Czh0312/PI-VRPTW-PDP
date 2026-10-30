"""Read-only review of the S01 historical exact witness and its certificate."""
from collections import defaultdict
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import sys

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT))
os.environ.setdefault('MPLCONFIGDIR', str(HERE/'mpl_cache'))

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib import font_manager
from matplotlib.lines import Line2D
from matplotlib.patches import FancyArrowPatch

from models.common.cycle import load_instance
from models.common.cycle.audit import audit_solution
from tools.configuration.loader import load_routing_case


def read(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')


def main():
    study = ROOT/'output/experiments/m8_all_small_rerun_20261029_v1'
    source = study/'certified_S01.json'
    case = ROOT/'data/formal_benchmark27_uniform_7_20_50_v1/model_8/small/S01/case.json'
    result, config = read(source), read(case)
    summary = read(study/'runs/S01/s42/summary.json')
    hashes = {str(source): sha(source), str(case): sha(case)}
    for key in ('data', 'routing_config', 'orders', 'operating_policy', 'economic_config'):
        path = ROOT/config[key]
        hashes[str(path)] = sha(path)
        assert sha(path) == result['info']['settings']['physical_input_hashes'][key], key
    certificate = result['external_global_optimality_certificate']
    assert certificate['global_optimality_certified']
    assert certificate['original_K'] == list(range(1, 8))
    for name, digest in certificate['evidence'].items():
        assert sha(study/'runs/S01/s42'/name) == digest, name
    original = read(study/'runs/S01/s42/best_audited_result.json')
    assert all(result[key] == original[key] for key in ('e1_trips', 'e2_trips', 'batch_boundaries',
               'batch_cutoffs', 'order_batch_assignment', 'container_pool_size'))
    problem = load_instance(*load_routing_case(case, use_pool_config=False))
    audit = audit_solution(problem, result)
    save(HERE/'audit.json', audit)
    assert audit['valid'] and audit['periodic_replay']['valid'], audit['errors']
    assert abs(audit['objective']-certificate['independently_replayed_lower_bound']) <= 1e-6
    assert abs(audit['objective']-summary['audited_upper_bound']) <= 1e-6
    p = problem.operating_policy
    tick_seconds = p.time_tick_seconds/p.time_tick_subdivision

    def clock(value):
        value = round(value*tick_seconds)
        return f'{value//3600:02}:{value//60%60:02}:{value%60:02}'

    def mins(value):
        return value*tick_seconds/60

    assigned = problem.fixed_calendar(result['cycle_batch_count'], result['batch_boundaries'])
    # service/travel durations are unchanged by assignment; quantities below use raw orders.
    orders = problem.order_map
    order_events = defaultdict(dict)
    rows, manual_errors, manual_legs = [], [], 0
    colors = ['#bc5427', '#c49224', '#2673b8', '#8553a1', '#168879', '#b24f70']
    occupied = defaultdict(list)
    for layer in ('e1', 'e2'):
        trips = sorted(result[layer+'_trips'], key=lambda t: (t['vehicle_id'], t.get('trip_index', 0), t['departure']))
        for index, trip in enumerate(trips, 1):
            base = problem.depot if layer == 'e1' else trip['satellite_id']
            goods = [orders[e['order_id']] for e in trip['events'] if e['kind'] == 'd'] if layer == 'e2' else [
                o for o in problem.orders if result['order_batch_assignment'][o.id] == trip['batch_id']
                and o.satellite in {e['site_id'] for e in trip['events'] if e['kind'] == 'd'}]
            weight = sum(o.goods_weight+o.count*problem.fleet.container_weight for o in goods)
            volume = sum(o.count*problem.fleet.container_volume for o in goods)
            max_weight, max_volume = weight, volume
            cap_weight = problem.fleet.e1_weight_cap if layer == 'e1' else problem.fleet.e2_weight_cap
            cap_volume = problem.fleet.e1_volume_cap if layer == 'e1' else problem.fleet.e2_volume_cap
            previous, current = base, trip['departure']
            nodes, tokens, events, waits = [base], [base], [], []
            batch = next(b for b in result['batches'] if b['id'] == trip.get('batch_id')) if layer == 'e1' else None
            begins = batch['start'] if layer == 'e1' else trip['departure']-p.e2_dispatch_prep_time
            vehicle_key = (layer, base if layer == 'e2' else None, trip['vehicle_id'])
            occupied[vehicle_key].append((begins, trip['return_complete'], trip['trip_id']))
            for event in trip['events']:
                node = event['site_id'] if layer == 'e1' else orders[event['order_id']].customer
                service = problem.service(node) if layer == 'e1' else orders[event['order_id']].service_time
                arrival = current+problem.travel(previous, node)
                if event['start'] < arrival:
                    manual_errors.append({'trip': trip['trip_id'], 'reason': 'travel', 'node': node})
                manual_legs += 1
                wait = event['start']-arrival
                if wait > 0:
                    waits.append(dict(node=node, earliest_arrival=clock(arrival), service_start=clock(event['start']),
                                      waiting_minutes=mins(wait)))
                quantity = event['quantity']
                if layer == 'e1' and event['kind'] == 'd':
                    task_orders = [o for o in goods if o.satellite == node]
                    moved_w = sum(o.goods_weight+o.count*problem.fleet.container_weight for o in task_orders)
                elif layer == 'e2' and event['kind'] == 'd':
                    o = orders[event['order_id']]
                    moved_w = o.goods_weight+o.count*problem.fleet.container_weight
                else:
                    moved_w = quantity*problem.fleet.container_weight
                direction = 1 if event['kind'] == 'p' else -1
                weight += direction*moved_w
                volume += direction*quantity*problem.fleet.container_volume
                max_weight, max_volume = max(max_weight, weight), max(max_volume, volume)
                if not -1e-6 <= weight <= cap_weight+1e-6 or not -1e-6 <= volume <= cap_volume+1e-6:
                    manual_errors.append({'trip': trip['trip_id'], 'reason': 'capacity', 'node': node})
                label = '送' if event['kind'] == 'd' else '收'
                nodes.append(node)
                tokens.append(f'{node}{label}{quantity}')
                events.append(dict(node=node, action=label, quantity=quantity, start=clock(event['start']),
                                   finish=clock(event['start']+service), earliest_arrival=clock(arrival),
                                   waiting_minutes=mins(wait), weight_after=weight, volume_after=volume))
                if layer == 'e2':
                    order_events[event['order_id']][event['kind']] = event['start']
                    o = orders[event['order_id']]
                    lo, hi = o.delivery_window if event['kind'] == 'd' else o.pickup_window
                    if not lo <= event['start'] <= hi:
                        manual_errors.append({'order': o.id, 'reason': 'time_window'})
                previous, current = node, event['start']+service
            unload = p.depot_return_unload_time if layer == 'e1' else p.e2_return_unload_time
            earliest_return = current+problem.travel(previous, base)+unload
            manual_legs += 1
            if trip['return_complete'] < earliest_return:
                manual_errors.append({'trip': trip['trip_id'], 'reason': 'return'})
            nodes.append(base)
            tokens.append(base)
            distance = sum(problem.distance(a, b) for a, b in zip(nodes, nodes[1:]))
            rows.append(dict(id=f'{layer.upper()}-{index}', layer=layer, vehicle=trip['vehicle_id']+1,
                trip=trip.get('trip_index', 0)+1, batch=trip.get('batch_id'), original_trip_id=trip['trip_id'],
                base=base, nodes=nodes, tokens=tokens, events=events, waits=waits, distance=distance,
                departure=clock(trip['departure']), return_complete=clock(trip['return_complete']),
                peak_weight=max_weight, capacity_weight=cap_weight, peak_volume=max_volume, capacity_volume=cap_volume,
                color=colors[len(rows)], kinds=[e['kind'] for e in trip['events']]+['r']))
    for oid, tasks in order_events.items():
        order = orders[oid]
        if tasks['p'] < tasks['d']+order.service_time+order.min_use_time:
            manual_errors.append({'order': oid, 'reason': 'customer_precedence'})
    for key, intervals in occupied.items():
        intervals.sort()
        gap = p.e1_turnaround_time if key[0] == 'e1' else p.e2_turnaround_time
        for before, after in zip(intervals, intervals[1:]):
            if after[0] < before[1]+gap:
                manual_errors.append({'vehicle': key, 'reason': 'overlap'})
    assert not manual_errors, manual_errors
    for layer in ('e1', 'e2'):
        assert math.isclose(sum(r['distance'] for r in rows if r['layer'] == layer), audit['distances'][layer], abs_tol=1e-8)
    save(HERE/'review.json', dict(case='S01', purpose='existing exact route review; no new optimization or seeds',
        objective=audit['objective'], K=result['cycle_batch_count'], N=result['container_pool_size'],
        routes=rows, input_and_solution_hashes=hashes, existing_certificate=certificate,
        original_solver_info=result['info'], manual_check=dict(valid=not manual_errors, errors=manual_errors,
            checked_travel_legs=manual_legs, checked_orders=len(order_events), checked_trips=len(rows)),
        independent_audit_valid=audit['valid'], periodic_valid=audit['periodic_replay']['valid']))
    shutil.copy2(source, HERE/'original_certified_solution.json')
    lines = ['# 模型八 S01：精确解路线检查', '',
        f'七个订单，K={result["cycle_batch_count"]}，箱池N={result["container_pool_size"]}，总费用 **{audit["objective"]:.9f}**。'
        '展示此前已有全域最优证书对应的原始路线，本次重新读取原始输入并审计，未重新运行优化、未增加种子。', '',
        'D0为中心仓，S0为卫星仓，C编号为顾客；“送/收”为服务开始，所有时刻均为当日HH:MM:SS。'
        '展示车辆编号从1开始，原JSON从0开始；表中返仓时刻包括卸货完成。', '',
        '![车辆路线图](routes.png)', '',
        '| 批次 | 开始装载 | 回收截点 | 批次结束 | 订单 | 用箱 |',
        '| --- | --- | --- | --- | --- | ---: |']
    for b in result['batches']:
        names = ', '.join(orders[o].customer for o, bid in result['order_batch_assignment'].items() if bid == b['id'])
        lines.append(f"| {b['id']} | {clock(b['start'])} | {clock(b['cutoff'])} | {clock(b['end'])} | {names} | {b['container_demand']} |")
    lines.extend(['', '## 逐趟路线', '', '| 路线 | 车辆 | 离仓 | 访问顺序 | 返仓卸货完成 | 距离 |',
                  '| --- | --- | --- | --- | --- | ---: |'])
    for row in rows:
        sequence = row['base']+' → '+' → '.join(f"{e['node']}{e['action']}{e['quantity']}（{e['start']}）" for e in row['events'])+' → '+row['base']
        lines.append(f"| {row['id']} | 车{row['vehicle']} | {row['departure']} | {sequence} | {row['return_complete']} | {row['distance']:.6f} |")
    lines.extend(['', '## 容量、时间窗与复用', '', '| 路线 | 峰值重量 / 容量 | 峰值体积 / 容量 |',
                  '| --- | ---: | ---: |'])
    for row in rows:
        lines.append(f"| {row['id']} | {row['peak_weight']:g} / {row['capacity_weight']:g} | {row['peak_volume']:g} / {row['capacity_volume']:g} |")
    lines.extend(['', '| 顾客 | 送货开始 | 配送窗 | 回收开始 | 回收窗 | 送货服务完成至回收 / 最小使用时间（分钟） |',
                  '| --- | --- | --- | --- | --- | ---: |'])
    for oid, o in sorted(orders.items()):
        tasks = order_events[oid]
        lines.append(f"| {o.customer} | {clock(tasks['d'])} | {clock(o.delivery_window[0])}–{clock(o.delivery_window[1])} "
            f"| {clock(tasks['p'])} | {clock(o.pickup_window[0])}–{clock(o.pickup_window[1])} "
            f"| {mins(tasks['p']-tasks['d']-o.service_time):.2f} / {mins(o.min_use_time):g} |")
    lines.extend(['', '箱池复用：08:00装载4箱；第一批回收4箱于11:39:10完成总仓卸货，'
        '15分钟清洗后11:54:10可用；第二批11:55装载3箱，仍余1箱。'
        '第二批3箱20:45回仓卸货完成，21:00清洗结束，全4箱回到可用状态。'
        '第一批清洗与第二批装载之间有50秒余量。', '',
        f'独立完整审计：**{audit["valid"]}**；两周期审计：**{audit["periodic_replay"]["valid"]}**。'
        f'另逐段检查{manual_legs}段行驶、7个订单时间窗/送收先后及6趟车的容量与同车占用，未发现违反。', '',
        '## 为什么路线看起来可能不合理', '',
        '一级车送达S0后可停在原地等待回收：E1-1在08:12:10完成交付，却等到11:28才开始收箱；'
        'E1-2在12:07:10交付完，20:33:50才开始收箱。原计划占用了两辆一级车，未越过车队数量或车辆时间限制。', '',
        'E2-4于12:08:10离开S0，按直达行驶最早12:17:40到C3，而其配送窗从15:14才开始，'
        '存在至少2小时56分20秒的可用等待量。这里按“到达后等到服务开始”解释；'
        '原JSON没有单独保存停车位置，等待也可以分配在服务前的途中。', '',
        '当前费用为 **0.1×E1距离 + 0.05×E2距离 + 5×箱池 + 0.01×使用箱数（清洗） + 0.01×使用箱数（磨损）**。'
        '工作时间、等待时间、车辆固定费用和趟次费用系数均为0。因此长等待和多用一辆已配置车辆不会增加目标值；'
        '满足现有模型的最优路线可以包含这些行为。若业务要求送货后立即返仓、限制等待，或按占用时间收费，需要明确加入相应规则。', '',
        '“同一顾客连续送、收”表示送货服务后等待客户使用完再回收，并非零时间完成。'
        '地图没有为同地点等待绘制虚假行驶弧。距离采用基准坐标单位，不代表真实道路公里。', '',
        '## 费用与最优性记录', '',
        f"E1距离 {audit['distances']['e1']:.9f}，E2距离 {audit['distances']['e2']:.9f}；"
        f"箱池20 + E1运费{result['cost_components']['e1_distance']:.9f} + E2运费{result['cost_components']['e2_distance']:.9f} "
        f"+ 清洗0.07 + 磨损0.07 = **{audit['objective']:.9f}**。费用单位为合成单位。", '',
        '原始路线文件 `info.status=TIME_LIMIT`、`info.proven_optimal=false` 保留了早期固定K求解的原生记录，'
        '其原生MIP GAP约54.89%。后续全域证书保存在 `external_global_optimality_certificate`，'
        f"全K=1…7的下界为 {certificate['independently_replayed_lower_bound']:.9f}，与当前重新审计的可行费用相等，"
        '才构成全局最优性证明。这两套状态代表不同阶段，不能把早期原生状态当作后续证书状态。'
        '本次核对证据文件SHA256及费用一致性，没有重新求解下界，也没有改写任何旧记录。', '',
        '此前2.615秒是使用历史可行方案后重建下界的证明时间，不是冷启动产生这条路线的耗时。', '',
        '原始方案副本：[original_certified_solution.json](original_certified_solution.json)；'
        '本次完整审计：[audit.json](audit.json)；逐段与容量检查：[review.json](review.json)。', ''])
    (HERE/'report.md').write_text('\n'.join(lines), encoding='utf-8')
    draw(problem, rows, audit['objective'])
    assert all(sha(Path(path)) == digest for path, digest in hashes.items())
    print(json.dumps(dict(case='S01', cost=audit['objective'], K=result['cycle_batch_count'], N=result['container_pool_size'],
        valid=audit['valid'], periodic_valid=audit['periodic_replay']['valid'], manual_legs=manual_legs,
        routes=[{k: r[k] for k in ('id','vehicle','departure','return_complete','tokens','peak_weight','peak_volume')} for r in rows],
        report=str(HERE/'report.md')), ensure_ascii=False))


def draw(problem, rows, objective):
    font = Path('C:/Windows/Fonts/msyh.ttc')
    if font.exists():
        font_manager.fontManager.addfont(str(font))
        plt.rcParams['font.family'] = font_manager.FontProperties(fname=str(font)).get_name()
    plt.rcParams.update({'axes.unicode_minus': False, 'font.size': 10, 'svg.fonttype': 'path'})
    fig = plt.figure(figsize=(15.4, 10.2), facecolor='#f5f7fb')
    grid = fig.add_gridspec(2, 2, left=.06, right=.97, bottom=.075, top=.87,
                          height_ratios=[4.4, 3.3], hspace=.22, wspace=.17)
    fig.text(.06,.957,'模型八 · S01 · 已有全域最优证书对应路线', fontsize=19, color='#20334d')
    fig.text(.06,.923,f'7订单 / 1卫星仓 / K=2 / 箱池N=4 / 总费用 {objective:.6f}  |  本次独立审计及两周期重放通过',
             fontsize=11, color='#596c82')
    positions = {key:(node.x,node.y) for key,node in problem.network.nodes.items()}
    for col, layer in enumerate(('e1','e2')):
        ax = fig.add_subplot(grid[0,col])
        ax.set_facecolor('white')
        ax.grid(color='#e2e8f0', linestyle=':', zorder=0)
        selected = [r for r in rows if r['layer'] == layer]
        grouped = defaultdict(list)
        for row in selected:
            for a,b,kind in zip(row['nodes'], row['nodes'][1:], row['kinds']):
                if a != b:
                    grouped[tuple(sorted((a,b)))].append((a,b,kind,row['color']))
        for pair, edges in grouped.items():
            for i,(a,b,kind,color) in enumerate(edges):
                offset = (i-(len(edges)-1)/2)*.13
                ax.add_patch(FancyArrowPatch(positions[a],positions[b],
                    connectionstyle=f'arc3,rad={offset if a == pair[0] else -offset}',
                    arrowstyle='-|>',mutation_scale=13,shrinkA=8,shrinkB=8,
                    color=color,lw=1.8,alpha=.85,
                    linestyle={'d':'-','p':'--','r':':'}[kind],zorder=3))
        for node in problem.network.nodes.values():
            faded = layer == 'e1' and node.type == 'c'
            marker,size,color = ('s',150,'#c0522e') if node.type == 'd' else (
                ('^',170,'#354965') if node.type == 's' else ('o',65,'#738aa2'))
            ax.scatter(node.x,node.y,marker=marker,s=size if not faded else 22,
                       color=color if not faded else '#d5dce5',edgecolors='white',zorder=5)
            ax.annotate(node.id,(node.x,node.y),xytext=(6,8 if node.id != 'C8' else -15),
                        textcoords='offset points',fontsize=10,color='#253951',zorder=6,
                        bbox=dict(facecolor='white',edgecolor='none',pad=.5,alpha=.8))
        ax.set_xlim(-13,43)
        ax.set_ylim(-19,21)
        ax.set_aspect('equal',adjustable='box')
        ax.set_title('一级 E1：中心仓—卫星仓' if layer == 'e1' else '二级 E2：卫星仓—顾客',loc='left',pad=12,fontsize=13)
        ax.set_xlabel('X（基准坐标）')
        ax.set_ylabel('Y（基准坐标）')
        ax.tick_params(labelsize=8)
        textax = fig.add_subplot(grid[1,col])
        textax.axis('off')
        y = .97
        step = .225 if layer == 'e2' else .35
        for row in selected:
            textax.text(0,y,f"{row['id']} · 车{row['vehicle']} · {row['batch'] or '第'+str(row['trip'])+'趟'}",
                        fontsize=11,color=row['color'],weight='bold',va='top')
            sequence = ' → '.join(row['tokens'])
            if len(sequence)>56:
                cut = sequence.rfind(' → ',0,51)
                sequence = sequence[:cut]+' →\n'+sequence[cut+3:]
            textax.text(0,y-.063,sequence,fontsize=9.2,color='#30465e',va='top',linespacing=1.7)
            textax.text(0,y-.158 if '\n' in sequence else y-.121,
                f"离仓 {row['departure']}  /  返仓卸货完成 {row['return_complete']}  /  距离 {row['distance']:.3f}",
                fontsize=8.4,color='#60748c',va='top')
            y -= step
    handles = [Line2D([0],[0],color='#415875',lw=1.8,linestyle=style,label=label) for style,label in
               [('-','驶向送货服务'),('--','驶向回收服务'),(':','返回所属仓')]]
    fig.legend(handles=handles,loc='lower left',bbox_to_anchor=(.055,.035),ncol=3,frameon=False,fontsize=9)
    fig.text(.06,.018,'方块：中心仓；三角：卫星仓；圆点：顾客。箭头为行驶方向；同点送收间的等待不画成行驶。逐项时刻见报告。',
             fontsize=8.5,color='#60748c')
    fig.savefig(HERE/'routes.png',dpi=160)
    fig.savefig(HERE/'routes.svg')
    plt.close(fig)


if __name__ == '__main__':
    main()
