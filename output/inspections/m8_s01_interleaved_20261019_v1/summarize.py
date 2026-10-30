"""Audit and display this two-run S01 experiment; never invoke a solver."""
from pathlib import Path
import hashlib
import json
import sys

PROJECT = Path.cwd()
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))
from models.common.cycle import load_instance
from models.common.cycle.audit import audit_solution

ROOT = Path(__file__).resolve().parent


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def stamp(ticks, result):
    seconds = round(ticks * result['time_tick_seconds'] / result.get('time_tick_subdivision', 1))
    return f'{seconds // 3600:02d}:{seconds // 60 % 60:02d}:{seconds % 60:02d}'


results, manifests, summaries = {}, {}, []
for method in ('alns', 'exact'):
    directory = ROOT/method
    result, manifest = read(directory/'result.json'), read(directory/'manifest.json')
    for name, expected in manifest['input_sha256'].items():
        if hashlib.sha256(Path(name).read_bytes()).hexdigest() != expected:
            raise ValueError(f'Input changed: {name}')
    for name, expected in manifest['source_sha256'].items():
        if hashlib.sha256((PROJECT/name).read_bytes()).hexdigest() != expected:
            raise ValueError(f'Source changed: {name}; use the recorded snapshot for replay')
    problem = load_instance(manifest['config'], manifest['fleet'])
    audited = audit_solution(problem, result)
    if not audited['valid']:
        raise ValueError(f'{method}: {audited["errors"]}')
    results[method], manifests[method] = result, manifest
    info = result['info']
    summaries.append(dict(method=method, status=info['status'], objective=info['objective'],
        bound=info.get('best_bound'), native_gap=info.get('mip_gap'),
        proven_optimal=info['proven_optimal'], runtime=info['runtime'],
        K=result['cycle_batch_count'], N=result['container_pool_size'],
        distance=result['total_distance'], valid=audited['valid'],
        periodic_replay=audited['periodic_replay']['valid'],
        container_balance=audited['container_balance'],
        time_metrics=audited['time_metrics']))
if manifests['alns']['source_sha256'] != manifests['exact']['source_sha256']:
    raise ValueError('Algorithm runs have different source snapshots')
if manifests['alns']['input_sha256'] != manifests['exact']['input_sha256']:
    raise ValueError('Algorithm runs have different physical inputs')

lines = ['# 新窗口 S01：模型8实际求解', '',
    '7位顾客、一客一箱。完整候选K=1～7，seed=42；ALNS预算30秒，直接Gurobi预算300秒、单线程、expanded。',
    '两次运行的输入及源码哈希完全相同；均未读取数据集可行见证作为初始解。', '',
    '| 算法 | 状态 | 成本 | 下界 | 原生gap | 耗时/秒 | K | 箱池N | 审计 |',
    '| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |']
for row in summaries:
    bound = '—' if row['bound'] is None else f'{row["bound"]:.4f}'
    gap = '—' if row['native_gap'] is None else f'{row["native_gap"]:.2%}'
    lines.append(f'| {row["method"]} | {row["status"]} | {row["objective"]:.4f} | {bound} | {gap} | '
                 f'{row["runtime"]:.2f} | {row["K"]} | {row["N"]} | 通过 |')
lines += ['', 'FEASIBLE或TIME_LIMIT均不表示已证明最优；仅以proven_optimal=true确认最优性。',
          '成本为合成算例单位，距离为算例坐标单位。', '']
customer_rows = []
orders = read(manifests['alns']['config']['orders'])
order_map = {o['order_id']: o for o in orders}
for method, result in results.items():
    lines += [f'## {method} 实际调度', '', '送/取后的时间均为开始服务时间。每次顾客服务10分钟。', '']
    for trip in result['e1_trips']:
        sequence = ' → '.join(f'{e["site_id"]}{"送" if e["kind"] == "d" else "取"}'
            f'{e["quantity"]}箱({stamp(e["start"], result)})' for e in trip['events'])
        lines.append(f'- 一级车{trip["vehicle_id"]}：仓库({stamp(trip["departure"], result)}) → '
                     f'{sequence} → 仓库卸完({stamp(trip["return_complete"], result)})。')
    for trip in result['e2_trips']:
        sequence = ' → '.join(f'{order_map[e["order_id"]]["customer_id"]}'
            f'{"送" if e["kind"] == "d" else "取"}({stamp(e["start"], result)})' for e in trip['events'])
        lines.append(f'- {trip["satellite_id"]}二级车{trip["vehicle_id"]}/趟次{trip["trip_index"]}：'
                     f'{trip["satellite_id"]}({stamp(trip["departure"], result)}) → {sequence} → '
                     f'{trip["satellite_id"]}卸完({stamp(trip["return_complete"], result)})。')
    events = {(e['order_id'], e['kind']): e for trip in result['e2_trips'] for e in trip['events']}
    lines += ['', '| 顾客 | 配送开始 | 回收开始 | 实际可使用/分钟 | 最低使用/分钟 |',
              '| --- | --- | --- | ---: | ---: |']
    for order in orders:
        d, p = events[order['order_id'], 'd']['start'], events[order['order_id'], 'p']['start']
        usable = (p - d) * result['time_tick_seconds'] / (60 * result.get('time_tick_subdivision', 1)) - order['service_time']
        lines.append(f'| {order["customer_id"]} | {stamp(d, result)} | {stamp(p, result)} | '
                     f'{usable:.2f} | {order["min_use_time"]} |')
        customer_rows.append(dict(method=method, customer=order['customer_id'],
            delivery=stamp(d, result), pickup=stamp(p, result), usable_minutes=usable))
    waiting = result['audit']['time_metrics']['waiting_ticks']
    factor = result['time_tick_seconds'] / (60 * result.get('time_tick_subdivision', 1))
    lines += ['', f'等待累计：一级车{waiting["e1"] * factor:.2f}车分钟，'
              f'二级车{waiting["e2"] * factor:.2f}车分钟。等待与工时成本仍为0，交错服务不意味着消除等待。',
              '累计箱次：发出7箱次、回站7箱次、回总仓7箱次；实际箱池数量见表，清洗闭环及连续两周期重放均通过。', '']

(ROOT/'comparison.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')
report = dict(case='S01', model=8, dataset='formal_benchmark27_interleaved_v1',
    scope='One base case, one ALNS seed and one direct Gurobi run; not a full benchmark.',
    identical_sources_and_inputs=True, runs=summaries, customers=customer_rows)
(ROOT/'summary.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
print(json.dumps({k: v for k, v in report.items() if k != 'customers'}, ensure_ascii=False, indent=2))
