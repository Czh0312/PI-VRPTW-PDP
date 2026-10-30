"""Read-only structural diagnosis against the latest frozen M3 exact runs."""
from collections import Counter
import hashlib
import itertools
import json
from pathlib import Path
import re
import sys

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[2]
STUDY = ROOT/'output/experiments/m3_network_strengthening_20261029_v1'
sys.path.insert(0,str(ROOT))
from tools.configuration.loader import load_routing_case
from models.common.cycle.instance import load_instance
from models.routing.model_3.exact.parameters import prepare_parameters
from models.routing.model_3.exact.route_bounds import site_nodes, minimum_trip_count
from models.routing.model_3.exact.preprocessing.model_bounds import route_arcs, trip_slot_limits


def read(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def native_summary(path):
    text = path.read_text(encoding='utf-8-sig')
    root = re.search(r'Root relaxation: objective ([\deE+.\-]+), (\d+) iterations, ([\d.]+) seconds',text)
    branching, points = None, []
    for line in text.splitlines():
        fields = line.split()
        fields = fields[1:] if fields and fields[0] in ('H','*') else fields
        if len(fields)<6 or not fields[0].isdigit() or not fields[1].isdigit():
            continue
        g = next((i for i,t in enumerate(fields) if t.endswith('%')),None)
        if g is None:
            continue
        try:
            point = dict(explored=int(fields[0]), unexplored=int(fields[1]),
                incumbent=float(fields[g-2]), bound=float(fields[g-1]), seconds=float(fields[-1].removesuffix('s')))
        except ValueError:
            continue
        points.append(point)
        if branching is None and point['explored']==0 and point['unexplored']>0:
            branching = point['bound']
    root_rows = [p for p in points if p['explored']==0]
    iterations = re.search(r'Explored (\d+) nodes \((\d+) simplex iterations\)',text)
    return dict(root_lp=float(root.group(1)) if root else None,
        root_seconds=float(root.group(3)) if root else None,
        root_after_cuts_bound=branching if branching is not None else root_rows[-1]['bound'] if root_rows else None,
        total_simplex_iterations=int(iterations.group(2)) if iterations else None,
        numerical_warnings=[line for line in text.splitlines() if re.search(r'Numerical trouble|Warning:.*(?:numeric|violation)',line,re.I)])


plan = read(STUDY/'plan.json')
all_rows = read(STUDY/'rows.json')
rows = [r for r in all_rows if r['version']=='after' and r['size']=='medium'
        and r['budget']==(1800 if r['case']=='M03' else 300)]
assert len(rows)==9
details, loaded = [], {}
for row in rows:
    folder = STUDY/'runs'/row['id']
    for name,digest in read(folder/'completion.json')['files'].items():
        assert sha(folder/name)==digest
    case = row['case']
    checked = 0
    for name,digest in plan['hashes'].items():
        if name.startswith('data/') and f'/medium/{case}/' in name:
            assert sha(ROOT/name)==digest
            checked += 1
    assert checked>=7
    config,fleet = load_routing_case(ROOT/row['case_path'],use_pool_config=False)
    p = load_instance(config,fleet)
    params = prepare_parameters(None,None,p,True,None)
    result = read(folder/'result.json')
    audit = read(folder/'independent_audit.json')
    assert audit['valid'] and audit['periodic_replay']['valid']
    assert len(p.orders)==len({o.customer for o in p.orders})==20
    assert len({(p.network.nodes[o.customer].x,p.network.nodes[o.customer].y) for o in p.orders})==20
    phase = next(v for v in row['native_phases'] if v['phase']=='full_model')
    native = native_summary(folder/phase['logfile'])
    provenance = read(ROOT/f'data/formal_benchmark27_uniform_7_20_50_v1/shared/medium/{case}/provenance.json')
    sites = []
    both_ways = 0
    for site in p.satellites:
        nodes = site_nodes(p,params,site)
        arcs = set(route_arcs(p,nodes,site,'e2'))
        delivery = [i for i,n in enumerate(nodes) if n['kind']=='d']
        bidirectional = sum((i,j) in arcs and (j,i) in arcs for i,j in itertools.combinations(delivery,2))
        both_ways += bidirectional
        sites.append(dict(site=site,orders=len(delivery),legal_arcs=len(arcs),
            legal_internal_arcs=sum(a>=0 and b>=0 for a,b in arcs),
            delivery_pairs_permitting_either_order=bidirectional,
            minimum_trips=minimum_trip_count(p,site),slots=trip_slot_limits(p,len(nodes))))
    waits, durations = [], []
    for trip in result['e2_trips']:
        at,clock = trip['satellite_id'],trip['departure']
        durations.append((trip['return_complete']-clock)/60)
        for event in trip['events']:
            order = p.order_map[event['order_id']]
            waits.append((event['start']-clock-p.travel(at,order.customer))/60)
            at,clock = order.customer,event['start']+order.service_time
    components = result['cost_components']
    constant = sum(components[k] for k in ('container_pool','cleaning','tracking','container_wear'))
    delta = row['objective']-row['bound']
    active = [sum(o.delivery_window[0]<=t*60<=o.delivery_window[1] for o in p.orders) for t in range(480,1261)]
    loaded[case] = (p,active)
    start = next(v for v in row['native_phases'] if v['phase']=='internal_schedule')
    details.append(dict(case=case,status=row['status'],seconds=row['total_seconds'],
        objective=row['objective'],bound=row['bound'],gap=row['gap'],absolute_gap=delta,
        constant_cost=constant,transport_cost=row['objective']-constant,
        transport_relative_gap=delta/(row['objective']-constant), cost_components=components,
        sites=sites,physical_e2_vehicles_per_site=p.fleet.e2_per_sat_count,
        volume_capacity=p.fleet.e2_volume_cap,weight_capacity=p.fleet.e2_weight_cap,
        waves=provenance['design']['period_totals'],external_periods=provenance['design']['external_periods'],
        delivery_window_widths_minutes=dict(Counter((o.delivery_window[1]-o.delivery_window[0])/60 for o in p.orders)),
        pickup_window_widths_minutes=dict(Counter((o.pickup_window[1]-o.pickup_window[0])/60 for o in p.orders)),
        peak_concurrent_delivery_windows=max(active),both_way_delivery_pairs=both_ways,
        internal_start_cost=row['initial_cost'],internal_start_available_seconds=start['ends_after_seconds'],
        total_variables=row['variables'],binary_variables=row['binary'],search_nodes=row['main_nodes'],
        e2_used_trips=len(result['e2_trips']),max_e2_trip_minutes=max(durations),max_recorded_wait_minutes=max(waits),
        main_mip_seconds=row['main_mip_seconds'],native=native,
        original_result_sha256=sha(folder/'result.json')))

details.sort(key=lambda r:r['case'])
(OUT/'analysis.json').write_text(json.dumps(details,ensure_ascii=False,indent=2),encoding='utf-8')
by_case = {r['case']:r for r in details}
m03 = by_case['M03']
assert abs(m03['constant_cost']-100.4)<1e-7
lines = ['# 当前模型3 M03：难度与算例设计复核','','本次仅复核已完成的最新优化版输入、原生主模型日志及审计记录；没有新跑MILP，没有改变算例。',
    'M03使用1800秒确认记录，其余中规模使用当前版300秒预算记录，统一单线程、种子42。', '',
    '## 当前结果与证明瓶颈','',
    f'M03在约{m03["internal_start_available_seconds"]:.2f}秒已有成本{m03["internal_start_cost"]:.6f}的内部完整调度初解。'
    f'最终成本{m03["objective"]:.6f}、下界{m03["bound"]:.6f}，差{m03["absolute_gap"]:.6f}，GAP为{100*m03["gap"]:.4f}%。'
    '因此尚未完成的是全局最优证明，不是找不到可行路线。','',
    f'固定箱池、清洗和磨损费用为{m03["constant_cost"]:.1f}；当前运输费用仅{m03["transport_cost"]:.6f}。'
    f'扣除常数后，界差相当于当前运输费用的{100*m03["transport_relative_gap"]:.3f}%。'
    '这只是帮助理解剩余证明难度，不改变目标或官方GAP，也不是对未知真实最优值的精确积分间隙。','',
    '## 与同为单仓20单的M05/M07对比','',
    '| 算例 | 配送窗/分钟 | 预约段订单数 | 窗口最高重叠数 | 可双向衔接的配送节点对 | 合法任务弧 | 车辆/仓 | 搜索节点 | 总秒数 |',
    '| --- | ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: |']
for case in ('M03','M05','M07'):
    d = by_case[case]
    lines.append(f'| {case} | {"/".join(map(str,d["delivery_window_widths_minutes"]))} | {"+".join(map(str,d["waves"]))} | '
        f'{d["peak_concurrent_delivery_windows"]} | {d["both_way_delivery_pairs"]} | {sum(s["legal_arcs"] for s in d["sites"])} | '
        f'{d["physical_e2_vehicles_per_site"]} | {d["search_nodes"]:.0f} | {d["seconds"]:.3f} |')
lines += ['', '可双向衔接仅指两种配送任务弧均通过必要窗口/旅行时间筛选，不保证能够放进同一完整可行车趟。'
    '窗口最高重叠数指同一时刻允许开始服务的订单数，不表示同车同时服务能力。', '',
    'M03和M07的单个配送窗同为75分钟，M05为58分钟。M03的任务更集中，'
    '通过双向必要时间筛选的配送节点对更多。'
    '它的车辆反而少于M05/M07、每车重量/体积容量相同，不能说M03单独因为车多或容量大而过松。'
    '单仓集中使20单共同参与路线划分；但M05/M07也单仓，所以单仓本身不是充分解释。', '',
    '## 最新主模型松弛与搜索','',
    '| 算例 | 初始根LP | 根切割后/进入分支下界 | 最终下界 | 当前可行成本 | 单纯形总迭代 |',
    '| --- | ---: | ---: | ---: | ---: | ---: |']
for case in ('M03','M05','M07'):
    d = by_case[case]
    n = d['native']
    lines.append(f'| {case} | {n["root_lp"]:.6f} | {n["root_after_cuts_bound"]:.6f} | {d["bound"]:.6f} | '
        f'{d["objective"]:.6f} | {n["total_simplex_iterations"]} |')
lines += ['', '根节点数据来自各次原生日志，其切割数量及辅助准备结果可随实际计时而不同；'
    '不把300秒运行的根LP数值当作1800秒运行的根LP。主模型原生全局下界仍偏低，是直接可见的证明瓶颈。', '',
    '## 免费等待、同质车辆与冗余趟位','',
    f'M03的工时、车辆、趟次费用均为0，已保存解最长二级趟约{m03["max_e2_trip_minutes"]:.2f}分钟，'
    f'最大相邻服务前等待约{m03["max_recorded_wait_minutes"]:.2f}分钟。'
    '等待与车辆标签可产生等成本调度，是候选退化因素；同样费用设定也用于快实例，不能单独证明其耗时贡献。',
    f'M03已有对称性规约后保留{sum(sum(s["slots"]) for s in m03["sites"])}个候选趟位，'
    f'当前解实际{m03["e2_used_trips"]}趟，必要容量下界{sum(s["minimum_trips"] for s in m03["sites"])}趟。'
    '多余趟位有缩减空间，但不能根据当前解直接把原问题限定为4趟。',
    '此前已验证的安全趟位削减短试验把31个趟位固定为0，预处理后二元变量降到4624，'
    '但180秒GAP从1.4154%变为1.4922%，尚未改善整体表现。因此趟位冗余不是已证实的唯一原因。', '',
    '## 对“算例过松”的判断','',
    '没有发现输入错误、重复顾客坐标或不可行问题。M03配送窗比M05宽，但与M07相同。'
    '直接证据是主模型下界尚未闭合；预约集中、通过必要筛选的排序候选更多是可能的结构性影响因素。'
    '这些静态指标不等于已证明所有候选可组成完整物理路线，也未单独证实各设计因素的因果贡献。',
    '上述跨实例对照不是单因素因果试验，尚不能给各因素分摊耗时。'
    '若下一步验证设计影响，应在独立诊断副本中一次只改预约集中度、窗口宽度或费用规则，原例保留；'
    '这些改变会改变业务问题，不能算作对原例的等价求解加速。',
    '优先保留原数据继续加强路线连通、容量和时间归属联系；不为消除一个难例而直接缩窗、删车或给等待加费。', '',
    '证据：analysis.json、appointment_overlap.png；原始输入/日志的完成哈希已逐例核对。', '']
(OUT/'report.md').write_text('\n'.join(lines),encoding='utf-8')

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib import font_manager
font_path = Path('C:/Windows/Fonts/msyh.ttc')
font_manager.fontManager.addfont(font_path)
plt.rcParams.update({'font.family':font_manager.FontProperties(fname=str(font_path)).get_name(),'axes.unicode_minus':False})
fig,ax = plt.subplots(figsize=(11,4.8))
for case,color in zip(('M03','M05','M07'),('#B84545','#197E9B','#5B8F50')):
    ax.step(range(480,1261),loaded[case][1],where='post',label=f'{case}  '+ '+'.join(map(str,by_case[case]['waves'])),color=color,linewidth=2)
ax.set(title='配送预约窗口的重叠：M03同时可选顾客更多',xlabel='当日时间',ylabel='允许开始配送的顾客数',ylim=(-.2,14))
ax.set_xticks(range(480,1261,60),[f'{x//60:02d}:00' for x in range(480,1261,60)])
ax.grid(alpha=.2)
ax.legend(frameon=False)
fig.text(.08,.015,'原始输入；只统计窗口包含该时刻，不表示同车可同时服务，也不构成单因素因果证明。',fontsize=9,color='#647789')
fig.tight_layout(rect=(0,.04,1,1))
fig.savefig(OUT/'appointment_overlap.png',dpi=160)
plt.close(fig)
print(json.dumps([dict(case=d['case'],waves=d['waves'],both_way_pairs=d['both_way_delivery_pairs'],
    peak=d['peak_concurrent_delivery_windows'],arcs=sum(s['legal_arcs'] for s in d['sites']),
    root=d['native']['root_lp'],root_cut=d['native']['root_after_cuts_bound'],nodes=d['search_nodes'],
    transport_gap=d['transport_relative_gap'],max_wait=d['max_recorded_wait_minutes'])
    for d in details if d['case'] in ('M03','M05','M07')],ensure_ascii=False),flush=True)
