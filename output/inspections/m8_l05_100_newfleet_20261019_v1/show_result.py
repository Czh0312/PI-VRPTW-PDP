"""Audit saved L05 candidates and show actual batch stock and vehicle routes."""
from collections import Counter
import hashlib
import json
from pathlib import Path
import sys

PROJECT = Path.cwd()
sys.path.insert(0, str(PROJECT))
from models.common.cycle import load_instance
from models.common.cycle.audit import audit_solution

ROOT = Path(__file__).resolve().parent
def read(path):
    return json.loads(path.read_text(encoding='utf-8'))

cold_summary = read(ROOT / 'summary.json')
supplementary = (ROOT / 'warm/summary.json').exists()
candidate_root = ROOT / 'warm' if supplementary else ROOT
summary = read(candidate_root / 'summary.json')
manifest = read(candidate_root / 'alns/manifest.json')
for filename, expected in manifest['source_sha256'].items():
    if hashlib.sha256((PROJECT / filename).read_bytes()).hexdigest() != expected:
        raise ValueError(f'Source changed during experiment: {filename}')
for filename, expected in manifest['input_sha256'].items():
    if hashlib.sha256(Path(filename).read_bytes()).hexdigest() != expected:
        raise ValueError(f'Input changed during experiment: {filename}')
problem = load_instance(manifest['config'], manifest['fleet'])
orders = {order.id: order for order in problem.orders}
status_counts = Counter(row['status'] for row in cold_summary['counts'])
lines = ['# L05：100 顾客，模型 8 ALNS', '',
    '使用当前交错取送时间窗、一客一箱和更新后的大规模车辆配置。'
    '一级车5辆、载重800、体积40；每站二级车5辆、载重120、体积6。',
    '首轮完整 K=1～100 域、300秒预算、seed=42、空初解，保持原来的单周期成本目标和自适应预算分配。'
    '仅记录原搜索返回的候选；未强制多批次、未修改模型或时间窗。',
    f'首轮结果：{cold_summary["status"]}；有效成本={cold_summary["cost"]}。'
    '未找到可行解不表示已经证明不可行。',
    ('补充运行：用数据集M5可行性见证投影为M8的3批安排，并将箱池降为独立重放所需的84个；'
     '以该安排作初解，再运行完整K=1～100域ALNS 120秒、seed=42。'
     '这是有初解的诊断，不能记作冷启动成功；仍未强制K=3。'
     if supplementary else '本报告没有补充的有初解运行。'),
    f'最终状态：{summary["status"]}；K={summary["K"]}；N={summary["N"]}；'
    f'成本={summary["cost"]}；求解耗时={summary["runtime"]:.3f}秒。',
    '仅运行ALNS，Gurobi未检查、未做精确求解，也未证明最优。各K分配的时间不同。', '',
    '## 已保存并独立验证的候选', '',
    '| K | 箱池 N | 初始购箱费 | 两级运输费 | 其他运营费 | 总成本 | 一级/二级趟数 |',
    '| ---: | ---: | ---: | ---: | ---: | ---: | --- |']
verified = []
candidates = []
for item in sorted(summary['candidates'], key=lambda row: row['K']):
    result = read(candidate_root / f'best_K{item["K"]}.json')
    audit = audit_solution(problem, result)
    if not audit['valid'] or not audit['periodic_replay']['valid']:
        raise ValueError(audit['errors'])
    costs = result['cost_components']
    transport = costs['e1_distance'] + costs['e2_distance']
    other = sum(value for key, value in costs.items()
                if key not in ('container_pool', 'e1_distance', 'e2_distance'))
    row = dict(K=item['K'], N=result['container_pool_size'], cost=result['info']['objective'],
        acquisition=costs['container_pool'], transport=transport, other=other,
        e1_trips=audit['e1_trip_count'], e2_trips=audit['e2_trip_count'],
        audit_valid=True, periodic_replay=True, clean_stock=audit['clean_stock'],
        releases=audit['depot_releases'])
    verified.append(row)
    candidates.append((result, audit))
    lines.append(f'| {row["K"]} | {row["N"]} | {row["acquisition"]:.4f} | '
        f'{row["transport"]:.4f} | {row["other"]:.4f} | {row["cost"]:.4f} | '
        f'{row["e1_trips"]}/{row["e2_trips"]} |')

def clock(value, result):
    seconds = round(value * result['time_tick_seconds'] / result.get('time_tick_subdivision', 1))
    return f'{seconds // 3600:02d}:{seconds // 60 % 60:02d}:{seconds % 60:02d}'

if supplementary:
    lines += ['', '## 初解与已有参考路线', '',
        '以下各方案均在更新后的车辆参数下独立重放；1批参考是构造见证，不能称为ALNS求得或最优路线。', '',
        '| 来源 | K | 箱池 N | 初始购箱费 | 每周期运输及其他运营费 | 单周期含购箱总成本 |',
        '| --- | ---: | ---: | ---: | ---: | ---: |']
    for label, path in (
        ('已有1批构造见证', ROOT / 'verified_reference_K1.json'),
        ('补充ALNS使用的3批初解', ROOT / 'warm/initial_K3.json'),
        ('补充ALNS最终结果', ROOT / 'warm/alns/result.json')):
        reference = read(path)
        checked_reference = audit_solution(problem, reference)
        assert checked_reference['valid'] and checked_reference['periodic_replay']['valid']
        total = reference['info']['objective']
        acquisition = reference['cost_components']['container_pool']
        lines.append(f'| {label} | {reference["cycle_batch_count"]} | '
            f'{reference["container_pool_size"]} | {acquisition:.4f} | {total-acquisition:.4f} | {total:.4f} |')
    lines += ['', f'ALNS是否改善所提供的初解：{summary["alns_improved_initial"]}。',
        '若比较多个重复运营周期，购箱只计一次，各周期运营费分别累加；上表总成本只对应一个周期。']

lines += ['', '候选检查覆盖时间窗、先送后取及最低使用时间、车辆衔接、载重体积、箱量守恒、'
    '清洗和连续两个周期的闭环。两个周期重放只检查物理可行性，不是两周期经济评价。',
    f'各 K 的返回状态统计：{dict(status_counts)}。未找到可行解不表示已证明不可行。', '']
for result, audit in candidates:
    count = result['cycle_batch_count']
    lines += [f'## K={count}：{"最终选定方案" if count == summary["K"] else "未胜出的可行候选"}', '',
        '| 批次 | 装载开始 | 配送需求 | 装载前干净箱 | 装载后干净箱 |',
        '| --- | --- | ---: | ---: | ---: |']
    for stock in audit['clean_stock']:
        lines.append(f'| {stock["batch_id"]} | {clock(stock["time"], result)} | '
            f'{stock["demand"]} | {stock["available"]} | {stock["after"]} |')
    lines += ['', '清洗完成可再次发出的箱量：' + '；'.join(
        f'{clock(release["clean_ready"], result)}，{release["quantity"]}箱'
        for release in sorted(audit['depot_releases'], key=lambda row: row['clean_ready'])) + '。',
        '', '### 一级路线', '']
    for trip in result['e1_trips']:
        events = ' → '.join(f'{event["site_id"]}{"送" if event["kind"] == "d" else "取"}'
            f'{event["quantity"]}箱({clock(event["start"], result)})' for event in trip['events'])
        lines.append(f'- {trip["batch_id"]} / 车{trip["vehicle_id"]}：'
            f'总仓({clock(trip["departure"], result)}) → {events} → '
            f'总仓卸完({clock(trip["return_complete"], result)})。')
    lines += ['', '### 二级路线', '', '车辆编号在各站内单独计数；各路线从所属站出发并返回。'
        '括号内为服务开始时间，回收允许跨配送批次。', '',
        '| 站点 | 车辆/趟次 | 出发 | 返回卸完 | 配送批次 | 顾客送取顺序 |',
        '| --- | --- | --- | --- | --- | --- |']
    for trip in sorted(result['e2_trips'], key=lambda row: (row['satellite_id'], row['vehicle_id'], row['trip_index'])):
        events = ' → '.join(f'{orders[event["order_id"]].customer}'
            f'{"送" if event["kind"] == "d" else "取"}({clock(event["start"], result)})'
            for event in trip['events'])
        batches = sorted({result['order_batch_assignment'][event['order_id']]
                          for event in trip['events'] if event['kind'] == 'd'})
        lines.append(f'| {trip["satellite_id"]} | {trip["vehicle_id"]}/{trip["trip_index"]} | '
            f'{clock(trip["departure"], result)} | {clock(trip["return_complete"], result)} | '
            f'{", ".join(batches) or "仅回收"} | {events} |')
    lines += ['']
(ROOT / 'routes_and_batches.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')
(ROOT / 'verified_candidates.json').write_text(json.dumps(verified, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')

if candidates:
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    figure, axes = plt.subplots(len(candidates), 1, figsize=(14, 3.1 * len(candidates)),
                                squeeze=False, constrained_layout=True)
    for ax, (result, audit) in zip(axes[:, 0], candidates):
        factor = result['time_tick_seconds'] / result.get('time_tick_subdivision', 1) / 3600
        ledger = audit['container_ledger']
        times = [problem.operating_policy.cycle_start * factor] + [row['time'] * factor for row in ledger]
        stock = [result['container_pool_size']] + [row['states']['clean'] for row in ledger]
        ax.step(times, stock, where='post', color='#1976D2', lw=2, label='Clean boxes at depot')
        for row in audit['clean_stock']:
            x = row['time'] * factor
            ax.axvline(x, ls='--', color='#D55E00', alpha=.55)
            ax.text(x + .05, 103, f'{row["batch_id"]}: {row["demand"]} deliveries', fontsize=9)
        ax.set_title(f'L05 | K={result["cycle_batch_count"]} | N={result["container_pool_size"]} | '
                     f'cost={result["info"]["objective"]:.2f}'
                     + (' | selected' if result['cycle_batch_count'] == summary['K'] else ' | feasible alternative'))
        ax.set_xlim(8, 21)
        ax.set_ylim(0, 115)
        ax.set_xticks(range(8, 22), [f'{hour:02d}:00' for hour in range(8, 22)])
        ax.set_ylabel('Clean boxes')
        ax.grid(alpha=.2)
    figure.savefig(ROOT / 'batch_comparison.png', dpi=150)
    plt.close(figure)
print(json.dumps(dict(selected_K=summary['K'], candidates=verified, states=dict(status_counts)),
                 ensure_ascii=False, indent=2))
