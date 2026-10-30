"""Independently replay saved medium M8 candidates and report batch/route evidence."""
import hashlib
import json
from pathlib import Path
import sys

PROJECT = Path.cwd()
sys.path.insert(0, str(PROJECT))
from models.common.cycle import load_instance
from models.common.cycle.audit import audit_solution

ROOT = Path(__file__).resolve().parent
TRACE = ROOT / 'M01_full_domain_120s'
manifest = json.loads((TRACE / 'manifest.json').read_text(encoding='utf-8'))
for name, expected in manifest['source_sha256'].items():
    if hashlib.sha256((PROJECT / name).read_bytes()).hexdigest() != expected:
        raise ValueError(f'Source changed: {name}')
for name, expected in manifest['input_sha256'].items():
    if hashlib.sha256(Path(name).read_bytes()).hexdigest() != expected:
        raise ValueError(f'Input changed: {name}')
problem = load_instance(manifest['config'], manifest['fleet'])
orders = {order.id: order for order in problem.orders}
selected = json.loads((TRACE / 'summary.json').read_text(encoding='utf-8'))
scan = json.loads((ROOT / 'progress.json').read_text(encoding='utf-8'))
verified = []
results = []

def clock(value, result):
    seconds = round(value * result['time_tick_seconds'] / result.get('time_tick_subdivision', 1))
    return f'{seconds // 3600:02d}:{seconds // 60 % 60:02d}:{seconds % 60:02d}'

for count in (1, 2, 3):
    result = json.loads((TRACE / f'best_K{count}.json').read_text(encoding='utf-8'))
    audit = audit_solution(problem, result)
    if not audit['valid'] or not audit['periodic_replay']['valid']:
        raise ValueError(audit['errors'])
    components = result['cost_components']
    row = dict(K=count, N=result['container_pool_size'], cost=result['info']['objective'],
        pool_cost=components['container_pool'],
        transport_cost=components['e1_distance'] + components['e2_distance'],
        other_cost=sum(value for name, value in components.items()
                       if name not in ('container_pool', 'e1_distance', 'e2_distance')),
        e1_trips=audit['e1_trip_count'], e2_trips=audit['e2_trip_count'],
        audit_valid=audit['valid'], periodic_replay=audit['periodic_replay']['valid'],
        batches=[dict(batch=stock['batch_id'], loading=clock(stock['time'], result),
                      demand=stock['demand'], available=stock['available'], after=stock['after'])
                 for stock in audit['clean_stock']],
        clean_releases=[dict(quantity=release['quantity'],
                             clean_ready=clock(release['clean_ready'], result))
                        for release in audit['depot_releases']])
    verified.append(row)
    results.append(result)

lines = ['# 中规模 M8 分批候选核查', '',
    '本次使用更新后的中规模车辆参数，只运行 ALNS，没有运行 Gurobi 精确求解。',
    '先查看全部 9 个中规模算例：每例全批次数域短跑 30 秒、seed=42。'
    'M01、M04、M05 找到 1、2、3 批有效候选，最终均选 1 批；其余 6 例在该预算内未找到可行解，不能据此认定不可行。',
    '随后 M01 从空初解重新运行 120 秒，seed=42，完整 K=1～20 域，使用原有自适应预算分配；'
    '仅增加候选解保存和重放，不修改数据、目标函数或搜索算子。',
    f'120 秒运行最终选择 K={selected["selected_K"]}，成本 {selected["selected_cost"]:.4f}。'
    '下面的 2、3 批方案是同一次搜索保存的可行候选，不是最终胜出解，也没有最优性证明。',
    '不同 K 获得的预算不相等；表格不能作为相同计算预算的算法性能比较。', '',
    '| 批次数 K | 箱池 N | 箱池费用 | 两级运输费 | 其他费用 | 总成本 | 一级趟数 | 二级趟数 |',
    '| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |']
for row in verified:
    lines.append(f'| {row["K"]} | {row["N"]} | {row["pool_cost"]:.4f} | '
                 f'{row["transport_cost"]:.4f} | {row["other_cost"]:.4f} | '
                 f'{row["cost"]:.4f} | {row["e1_trips"]} | {row["e2_trips"]} |')
lines += ['', '三个候选都通过独立物理重放：时间窗、顾客先送后取及最低使用时间、车辆连续性、'
    '载重体积、箱量守恒、清洗回用，以及连续两个运营周期。',
    '分批减少了箱池，但当前保存的分批路线增加了运输距离，增加的运输费超过箱池费用节省；'
    '这解释了此次搜索为什么返回 1 批，不证明其他分批路线一定更贵。', '']

for result, row in zip(results, verified):
    count = row['K']
    lines += [f'## K={count}：批次与箱量', '',
        '| 批次 | 仓库装载开始 | 配送需求箱数 | 装载前干净箱数 | 装载后干净箱数 |',
        '| --- | --- | ---: | ---: | ---: |']
    for batch in row['batches']:
        lines.append(f'| {batch["batch"]} | {batch["loading"]} | {batch["demand"]} | '
                     f'{batch["available"]} | {batch["after"]} |')
    lines += ['', '回仓后完成清洗的批量：' + '；'.join(
        f'{release["clean_ready"]}，{release["quantity"]}箱' for release in row['clean_releases']) + '。',
        '', '各批配送顾客（每位顾客 1 箱）：', '']
    for batch in row['batches']:
        customers = sorted(orders[oid].customer for oid, bid in result['order_batch_assignment'].items()
                           if bid == batch['batch'])
        lines.append(f'- {batch["batch"]}：{", ".join(customers)}。')
    lines += ['', '### 一级路线', '']
    for trip in result['e1_trips']:
        events = ' → '.join(f'{event["site_id"]}{"送" if event["kind"] == "d" else "取"}'
            f'{event["quantity"]}箱({clock(event["start"], result)})' for event in trip['events'])
        lines.append(f'- {trip["batch_id"]} / 车{trip["vehicle_id"]}：'
            f'总仓({clock(trip["departure"], result)}) → {events} → '
            f'总仓卸完({clock(trip["return_complete"], result)})。')
    lines += ['', '### 二级路线', '',
        '车辆编号在每个配送站内单独计数；每条路线均从所属配送站出发并返回。'
        '括号内是服务开始时间。回收可以跨配送批次。', '',
        '| 站点 | 车辆/趟次 | 出发 | 回站卸完 | 配送批次 | 顾客送取顺序 |',
        '| --- | --- | --- | --- | --- | --- |']
    for trip in sorted(result['e2_trips'], key=lambda t: (t['satellite_id'], t['vehicle_id'], t['trip_index'])):
        events = ' → '.join(f'{orders[event["order_id"]].customer}'
            f'{"送" if event["kind"] == "d" else "取"}({clock(event["start"], result)})'
            for event in trip['events'])
        batches = sorted({result['order_batch_assignment'][event['order_id']]
                          for event in trip['events'] if event['kind'] == 'd'})
        lines.append(f'| {trip["satellite_id"]} | {trip["vehicle_id"]}/{trip["trip_index"]} | '
            f'{clock(trip["departure"], result)} | {clock(trip["return_complete"], result)} | '
            f'{", ".join(batches) or "仅回收"} | {events} |')
    lines += ['']

(ROOT / 'comparison.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')
summary = dict(case='M01', customers=20, selected_K=selected['selected_K'],
               candidates=verified, gurobi_checked=False, proven_optimal=False,
               scanned_cases=[{key: item.get(key) for key in ('case', 'customers', 'status', 'K', 'cost')}
                              for item in scan['completed_cases']])
(ROOT / 'verified_comparison.json').write_text(
    json.dumps(summary, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
print(json.dumps(summary, ensure_ascii=False, indent=2))
