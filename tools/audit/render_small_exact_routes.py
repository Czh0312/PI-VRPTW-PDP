"""Draw audited optimal M1/M3 routes from a frozen single-batch experiment."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import csv
import hashlib
import html
import json
import math
import os
from pathlib import Path
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
os.environ.setdefault('MPLCONFIGDIR', '/tmp/zhou-small-routes-mpl')
os.environ.setdefault('XDG_CACHE_HOME', '/tmp/zhou-small-routes-cache')

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib import font_manager
from matplotlib.lines import Line2D
from matplotlib.patches import FancyArrowPatch
import numpy as np

from tools.configuration.loader import load_routing_case
from models.routing.common.cycle import load_instance
from models.routing.common.cycle.audit import audit_solution

FONT = '/Library/Fonts/Arial Unicode.ttf'
if Path(FONT).exists():
    font_manager.fontManager.addfont(FONT)
    plt.rcParams['font.family'] = font_manager.FontProperties(fname=FONT).get_name()
plt.rcParams.update({'axes.unicode_minus': False, 'font.size': 10,
                     'svg.fonttype': 'path', 'savefig.facecolor': '#f5f7fb'})
COLORS = ['#2463a6', '#168578', '#8a4eac', '#b54d76', '#8d681f', '#4a7188']
E1_COLORS = ['#be552a', '#88711c', '#9b3e51']
FAMILIES = ['zhou_Set1_7-2-1', 'zhou_Set1_7-2-1b', 'zhou_Set1_7-2-2',
            'zhou_Set1_7-2-2b', 'zhou_Set1_7-2-3']


def route_rows(problem, result, layer):
    rows = []
    trips = sorted(result[layer+'_trips'], key=lambda r: (r.get('satellite_id', ''),
                   r['vehicle_id'], r.get('trip_index', 0)))
    vehicles = sorted({(t.get('satellite_id', problem.depot), t['vehicle_id']) for t in trips})
    for trip in trips:
        base = trip.get('satellite_id', problem.depot)
        vehicle = trip['vehicle_id']+1  # Display numbers are one-based.
        index = trip.get('trip_index', 0)+1
        palette = E1_COLORS if layer == 'e1' else COLORS
        color = palette[vehicles.index((base, trip['vehicle_id'])) % len(palette)]
        label = f'V{vehicle}' if layer == 'e1' else f'{base} · V{vehicle} · 趟{index}'
        nodes, tokens, events = [base], [base], []
        for event in trip['events']:
            node = event['site_id'] if layer == 'e1' else problem.order_map[event['order_id']].customer
            nodes.append(node)
            tokens.append(node+('送' if event['kind'] == 'd' else '收'))
            events.append({**event, 'node': node})
        nodes.append(base)
        tokens.append(base)
        distance = sum(problem.distance(a, b) for a, b in zip(nodes, nodes[1:]))
        minutes = problem.operating_policy.time_tick_seconds/60
        rows.append(dict(label=label, trip_id=trip['trip_id'], color=color, nodes=nodes,
                         tokens=tokens, events=events, distance=distance, vehicle=vehicle,
                         satellite=base, trip_index=index, departure=trip['departure']*minutes,
                         return_complete=trip['return_complete']*minutes,
                         kinds=[e['kind'] for e in events]+['r']))
    return rows


def plot_map(ax, problem, rows, layer, distance):
    positions = {key: (node.x, node.y) for key, node in problem.network.nodes.items()}
    ax.set_facecolor('white')
    ax.set_axisbelow(True)
    ax.grid(color='#e4eaf1', linewidth=.6, linestyle=':')
    for spine in ax.spines.values():
        spine.set_color('#d7dee8')
    edges = []
    for row in rows:
        for a, b, kind in zip(row['nodes'], row['nodes'][1:], row['kinds']):
            if positions[a] != positions[b]:
                edges.append((a, b, kind, row['color']))
    grouped = defaultdict(list)
    for edge in edges:
        grouped[tuple(sorted(edge[:2]))].append(edge)
    for pair, group in grouped.items():
        offsets = np.linspace(-.17, .17, len(group)) if len(group)>1 else [0]
        for (a, b, kind, color), offset in zip(group, offsets):
            rad = offset if a == pair[0] else -offset
            shrink = min(9, 2*math.dist(positions[a], positions[b]))
            ax.add_patch(FancyArrowPatch(positions[a], positions[b],
                connectionstyle=f'arc3,rad={rad}', arrowstyle='-|>', mutation_scale=12,
                shrinkA=shrink, shrinkB=shrink, color=color, linewidth=1.9, alpha=.86,
                linestyle={'d':'-', 'p':(0,(5,3)), 'r':(0,(1.2,2.2))}[kind], zorder=3))
    for key, node in problem.network.nodes.items():
        faded = layer == 'e1' and node.type == 'c'
        size, marker, color = (190, 's', '#be552a') if node.type == 'd' else (
                              (150, '^', '#364961') if node.type == 's' else (65, 'o', '#718399'))
        ax.scatter(node.x, node.y, s=size if not faded else 25, marker=marker,
                   color=color if not faded else '#d6dde7', edgecolors='white', linewidth=1.0, zorder=5)
        # Labels are offset in display space, preserving the original coordinates.
        neighbours = [(o.x, o.y) for o in problem.network.nodes.values() if o.id != key]
        nearest = min(neighbours, key=lambda pos:(pos[0]-node.x)**2+(pos[1]-node.y)**2)
        offset = (7, -13) if nearest[1] >= node.y and abs(nearest[0]-node.x)<8 else (7, 7)
        ax.annotate(key, (node.x,node.y), xytext=offset, textcoords='offset points',
                    fontsize=10 if not faded else 8, color='#263c54' if not faded else '#aab4c1',
                    zorder=6, bbox=dict(facecolor='white', edgecolor='none', alpha=.8, pad=.5))
    xs, ys = zip(*positions.values())
    span = max(max(xs)-min(xs), max(ys)-min(ys), 1)
    ax.set_xlim(min(xs)-.13*span, max(xs)+.15*span)
    ax.set_ylim(min(ys)-.13*span, max(ys)+.15*span)
    ax.set_aspect('equal', adjustable='box')
    ax.tick_params(labelsize=8, colors='#8190a3')
    ax.set_xlabel('X（基准坐标）', fontsize=9, color='#64748b')
    ax.set_ylabel('Y（基准坐标）', fontsize=9, color='#64748b')
    ax.set_title(f'{"一级运输 E1" if layer == "e1" else "二级运输 E2"}   |   距离 {distance:.3f}',
                 loc='left', fontsize=12, pad=10, color='#243951')


def route_lines(tokens, max_chars=45):
    lines, current = [], ''
    for token in tokens:
        addition = (' → ' if current else '')+token
        if len(current)+len(addition)>max_chars:
            lines.append(current+' →')
            current = token
        else:
            current += addition
    return lines+[current]


def plot_itineraries(ax, rows):
    ax.axis('off')
    total = sum(2+len(route_lines(row['tokens'])) for row in rows)
    step, y = min(.14, .93/max(total, 1)), .96
    for row in rows:
        ax.text(.015, y, f"{row['label']}    距离 {row['distance']:.3f}",
                transform=ax.transAxes, va='top', color=row['color'], fontsize=10.5)
        y -= step
        for line in route_lines(row['tokens']):
            ax.text(.015, y, line, transform=ax.transAxes, va='top', fontsize=10, color='#334155')
            y -= step
        ax.text(.015, y, f"出发 {row['departure']:.1f} → 返回卸货完成 {row['return_complete']:.1f} 分钟",
                transform=ax.transAxes, va='top', fontsize=8.7, color='#718096')
        y -= step


def save_figure(entries, path, family, comparison=False):
    fig = plt.figure(figsize=(13.0,13.3) if comparison else (15.2,10.0), facecolor='#f5f7fb')
    if comparison:
        grid = fig.add_gridspec(len(entries), 2, left=.075, right=.95, top=.86,
                               bottom=.15, hspace=.42, wspace=.23)
    else:
        grid = fig.add_gridspec(2, 2, height_ratios=[4.8,2.55], left=.065, right=.96,
                               top=.855, bottom=.078, hspace=.32, wspace=.18)
    sid = 'S'+str(FAMILIES.index(family)+1).zfill(2)
    fig.text(.065,.965,f'{sid}  |  {family.removeprefix("zhou_")}',fontsize=22,color='#172b45')
    fig.text(.065,.934,'单批次 · 7 顾客 / 2 卫星仓 · 固定分仓 · 已证明全局最优 · Gap = 0',
             fontsize=11,color='#64748b')
    for row_index, entry in enumerate(entries):
        problem, result, routes = entry['problem'],entry['result'],entry['routes']
        model = problem.model_number
        maps = [fig.add_subplot(grid[row_index if comparison else 2*row_index,col]) for col in range(2)]
        top = maps[0].get_position().y1
        title = 'M1 一次性包装 · 纯配送' if model == 1 else 'M3 循环包装 · 配送与延后回收'
        fig.text(.065,top+.036,f'{title}     总距离 {result["info"]["objective"]:.3f}',
                 fontsize=14,color='#172b45')
        for col, layer in enumerate(('e1','e2')):
            plot_map(maps[col],problem,routes[layer],layer,result['audit']['distances'][layer])
            if comparison:
                maps[col].legend(handles=[Line2D([0],[0],color=r['color'],lw=2,label=r['label'])
                                          for r in routes[layer]],loc='upper left',bbox_to_anchor=(0,-.16),
                                 ncol=min(3,len(routes[layer])),fontsize=7.8,frameon=False,
                                 columnspacing=.9,handlelength=1.5)
            else:
                plot_itineraries(fig.add_subplot(grid[2*row_index+1,col]),routes[layer])
    handles=[Line2D([0],[0],color='#415875',lw=2,linestyle=style,label=label) for style,label in
             [('-','实线：驶向配送任务'),('--','虚线：驶向回收任务'),(':','点线：返回所属仓')]]
    fig.legend(handles=handles,loc='lower left',bbox_to_anchor=(.06,.037),ncol=3,
               frameon=False,fontsize=10)
    if comparison:
        fig.text(.065,.065,'方块：中心仓；三角：卫星仓；圆点：顾客。完整访问顺序与时刻见 M1 / M3 单图及路线表。',
                 fontsize=9,color='#64748b')
    fig.text(.065,.027,'箭头表示行驶方向；颜色区分本层车辆。送/收表示到达节点后的服务，返仓时可能载有回收箱。',
             fontsize=9,color='#64748b')
    fig.text(.065,.012,'坐标和距离为基准单位，非真实道路公里。重合弧稍作弯曲；同点送后等待回收不另画虚假行驶。',
             fontsize=8.5,color='#64748b')
    # Detect text clipped off the figure, in addition to manual visual checks.
    fig.canvas.draw()
    renderer=fig.canvas.get_renderer()
    width,height=fig.bbox.width,fig.bbox.height
    for text in fig.findobj(matplotlib.text.Text):
        if text.get_visible() and text.get_text():
            box=text.get_window_extent(renderer)
            if box.x0 < -2 or box.y0 < -2 or box.x1 > width+2 or box.y1 > height+2:
                raise ValueError(f'Text outside figure: {text.get_text()}')
    fig.savefig(path.with_suffix('.png'),dpi=180)
    fig.savefig(path.with_suffix('.svg'))
    plt.close(fig)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-dir',required=True,type=Path)
    args=parser.parse_args()
    run=args.run_dir.resolve()
    manifest=json.loads((run/'manifest.json').read_text())
    if {j['family_id'] for j in manifest['jobs']} != set(FAMILIES) or len(manifest['jobs'])!=10:
        raise ValueError('Expected the five formal Zhou small cases, M1/M3 only')
    for source,data in manifest['input_snapshots'].items():
        assert hashlib.sha256((run/data['snapshot']).read_bytes()).hexdigest()==data['sha256'],source
    output=run/'route_maps'
    output.mkdir(exist_ok=True)
    grouped=defaultdict(dict)
    csv_rows=[]
    for job in manifest['jobs']:
        result=json.loads((run/job['run']/'result.json').read_text())
        config,fleet=load_routing_case(job['case_path'])
        problem=load_instance(config,fleet)
        audit=audit_solution(problem,result)
        if not result['info']['proven_optimal'] or not audit['valid'] or not audit['periodic_replay']['valid']:
            raise ValueError(f'Refusing to draw a non-optimal/invalid solution: {job["run"]}')
        routes={layer:route_rows(problem,result,layer) for layer in ('e1','e2')}
        for layer,rows in routes.items():
            assert math.isclose(sum(r['distance'] for r in rows),audit['distances'][layer],abs_tol=1e-7)
            for row in rows:
                csv_rows.append(dict(family=job['family_id'],model=job['model'],layer=layer,
                    route=row['label'],sequence=' → '.join(row['tokens']),distance=row['distance'],
                    departure_minutes=row['departure'],return_complete_minutes=row['return_complete']))
        grouped[job['family_id']][job['model']]=dict(problem=problem,result=result,routes=routes,job=job)
    summaries=[]
    sections=[]
    markdown=['# Zhou 小型集 M1 / M3 精确路线图','',
              '5 个正式七顾客实例，B=1 固定分仓；10/10 已证明最优，独立审计和两周期复放均通过。',
              '距离为基准坐标单位，不是公里。每项上限 120 秒、种子 42；下列时间是求解器报告运行时间。',
              '使用仓库当前 Zhou 派生输入，周期为 2,220 分钟（37 小时）；单批次不等于单日。',
              '返回的路线是一个全局最优解；可能存在距离相同的其他路线。M3 同点先送后收可能包含等待，图中不画虚假自环。','',
              '| 编号 | 实例 | M1 最优距离 | M3 最优距离 | 对照图 |',
              '|---|---|---:|---:|---|']
    for index,family in enumerate(FAMILIES,1):
        entries=[grouped[family][m] for m in (1,3)]
        sid=f'S{index:02d}'
        for entry in entries:
            save_figure([entry],output/f'{sid}_M{entry["problem"].model_number}',family)
        save_figure(entries,output/f'{sid}_comparison',family,True)
        values={f'M{entry["problem"].model_number}':dict(objective=entry['result']['info']['objective'],
                    solver_runtime=entry['result']['info']['runtime'],status=entry['result']['info']['status'],
                    audit_valid=True,periodic_replay=True,
                    e1_distance=entry['result']['audit']['distances']['e1'],
                    e2_distance=entry['result']['audit']['distances']['e2']) for entry in entries}
        summaries.append(dict(id=sid,family=family,**values))
        markdown.append(f'| {sid} | {family} | {values["M1"]["objective"]:.6f} | {values["M3"]["objective"]:.6f} | [{sid}]({sid}_comparison.png) |')
        sections.append(f'<section id="{sid}"><h2>{sid} · {html.escape(family)}</h2>'
            f'<p>M1：{values["M1"]["objective"]:.6f}　 M3：{values["M3"]["objective"]:.6f}　 '
            '均已证明最优，Gap = 0</p>'
            f'<p><a href="{sid}_comparison.png">下载对照 PNG</a> · '
            f'<a href="{sid}_comparison.svg">矢量 SVG</a> · '
            f'<a href="{sid}_M1.png">M1 单图</a> · <a href="{sid}_M3.png">M3 单图</a></p>'
            f'<a href="{sid}_comparison.png"><img loading="lazy" src="{sid}_comparison.png" alt="{sid} 模型1和3路线图"></a></section>')
        print(sid,family,'maps rendered and distances checked',flush=True)
    markdown+=['','## 全部车辆路线','',
               '编号 V1 对应原始结果 vehicle_id=0，趟1 对应 trip_index=0；时间单位为分钟，返回时间包含卸货。','']
    for index,family in enumerate(FAMILIES,1):
        markdown += [f'### S{index:02d} — {family}','']
        for model in (1,3):
            entry=grouped[family][model]
            markdown += [f'**M{model}：最优距离 {entry["result"]["info"]["objective"]:.6f}；求解 {entry["result"]["info"]["runtime"]:.3f} 秒。**','']
            for layer,rows in entry['routes'].items():
                for row in rows:
                    markdown.append(f'- {layer.upper()} / {row["label"]}：'+ ' → '.join(row['tokens'])+
                                    f'；距离 {row["distance"]:.6f}。')
            markdown += ['']
    (output/'routes.md').write_text('\n'.join(markdown)+'\n')
    with (output/'routes.csv').open('w',encoding='utf-8-sig',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=list(csv_rows[0]));writer.writeheader();writer.writerows(csv_rows)
    (output/'summary.json').write_text(json.dumps(summaries,ensure_ascii=False,indent=2)+'\n')
    page='''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Zhou 小型集 · M1/M3 精确路线图</title><style>
body{margin:0;background:#f5f7fb;color:#172b45;font:16px/1.7 system-ui,"PingFang SC",sans-serif}main{max-width:1200px;margin:auto;padding:36px 24px}
h1{font-size:30px;margin:0}p{color:#55677e}nav{position:sticky;top:0;background:#fff;padding:12px;border:1px solid #dee5ee;border-radius:10px;z-index:2}
a{color:#2463a6}nav a{margin-right:22px}section{scroll-margin-top:80px;margin:30px 0;background:white;padding:24px;border-radius:14px;border:1px solid #dee5ee}
img{width:100%;height:auto;display:block}h2{font-size:21px}small{color:#718096}@media print{nav{display:none}section{break-before:page;border:0}main{padding:0}}
</style><main><h1>Zhou 小型集 · M1 / M3 精确路线图</h1>
<p>5 个正式七顾客实例 · 固定分仓 · 单批次 B=1 · 10/10 全局最优，独立审计及两周期复放通过。</p>
<p>按箭头读取方向，结合图下的送/收顺序。坐标为基准单位，并非真实道路地图；返仓可能载有回收箱。
每项预算上限 120 秒。图中时间为分钟，同点送后收的等待保留在时间表中。
当前 Zhou 派生输入的周期为 2,220 分钟（37 小时），单批次不等于单日。</p><nav>'''
    page+=''.join(f'<a href="#S{i:02d}">S{i:02d}</a>' for i in range(1,6))
    page+='<a href="routes.csv">路线表 CSV</a><a href="routes.md">完整说明</a></nav>'
    page+=''.join(sections)+'</main></html>'
    (output/'index.html').write_text(page)
    (output/'verification.json').write_text(json.dumps(dict(optimal_results=10,independent_audits_passed=10,
        periodic_replays_passed=10,route_distance_sums_match=True,frozen_input_hashes_match=True,
        display_vehicle_numbering='one-based; raw result IDs remain unchanged',
        zero_length_legs='preserved in itinerary; not drawn as artificial travel',
        png_count=15,svg_count=15),ensure_ascii=False,indent=2)+'\n')
    with zipfile.ZipFile(run/'zhou_small_M1_M3_route_maps.zip','w',zipfile.ZIP_DEFLATED) as z:
        for p in sorted(output.iterdir()):
            if p.is_file():z.write(p,arcname='route_maps/'+p.name)
        for job in manifest['jobs']:
            z.write(run/job['run']/'result.json',arcname='results/'+job['run']+'.json')
    print('Gallery:',output/'index.html')


if __name__=='__main__':
    main()
