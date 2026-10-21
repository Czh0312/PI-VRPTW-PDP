"""Build and run an isolated, predeclared L05 M3/M8 mechanism pilot.

Run from the project root with .venv/bin/python -m experiments.runs.run_l05_dense_pilot.
No solver or formal benchmark inputs are changed. No saved solution is used as a start.
"""
from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
import csv
import hashlib
import itertools
import json
import math
from pathlib import Path
import platform
import random
import statistics
import subprocess
import time

from tools.configuration.loader import PROJECT_ROOT, load_routing_case
from models.common.cycle import load_instance
from models.common.cycle.audit import audit_solution
from models.common.cycle.precheck import check_necessary_conditions
from models.cost.comparison import evaluate_comparison_cost
from tools.data.formal_benchmark27_bounds import productive_count_certificate

DATA = Path('data/mechanism_l05_3sat_balanced_v1')
OUT = Path('output/mechanism_l05_3sat_balanced_20261020')
SOURCE = Path('data/formal_benchmark27_one_box_v1/shared/large/L05')
LABELS = ('M3', 'M8', 'M8_K3')


def read(path):
    return json.loads((PROJECT_ROOT / path).read_text(encoding='utf-8'))


def write(path, value):
    target = PROJECT_ROOT / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)+'\n', encoding='utf-8')


def sha(path):
    return hashlib.sha256((PROJECT_ROOT / path).read_bytes()).hexdigest()


def distance(a, b):
    return math.hypot(float(a['x'])-float(b['x']), float(a['y'])-float(b['y']))


def balanced_counts(counts):
    """Integer quotas closest to per-site 35/35/30, with exact global totals."""
    choices = []
    for n in counts:
        raw = [n*p for p in (.35, .35, .30)]
        choices.append([row for row in itertools.product(*[
            sorted({math.floor(v), math.ceil(v)}) for v in raw]) if sum(row) == n])
    candidates = [rows for rows in itertools.product(*choices)
                  if tuple(map(sum, zip(*rows))) == (35, 35, 30)]
    return min(candidates, key=lambda rows: (
        sum((v-n*p)**2 for n, row in zip(counts, rows) for v, p in zip(row, (.35, .35, .30))), rows))


def build():
    if (PROJECT_ROOT / DATA).exists():
        raise FileExistsError(f'Refusing to replace {DATA}')
    with (PROJECT_ROOT / SOURCE / 'network.csv').open(encoding='utf-8-sig', newline='') as stream:
        reader = csv.DictReader(stream)
        fields, nodes = reader.fieldnames, list(reader)
    customers = [n for n in nodes if n['node_type'] == 'c']
    satellites = [n for n in nodes if n['node_type'] == 's']
    subsets = []
    for subset in itertools.combinations(satellites, 3):
        subsets.append((sum(min(distance(c, s) for s in subset) for c in customers),
                        tuple(s['node_id'] for s in subset), subset))
    score, ids, subset = min(subsets, key=lambda r: (r[0], r[1]))
    assignment = {c['node_id']: min(subset, key=lambda s: (distance(c, s), s['node_id']))['node_id']
                  for c in customers}
    nodes = [deepcopy(n) for n in nodes if n['node_type'] != 's' or n['node_id'] in ids]
    for n in nodes:
        if n['node_type'] == 'c':
            n['assigned_sat'] = assignment[n['node_id']]
    (PROJECT_ROOT / DATA).mkdir(parents=True)
    with (PROJECT_ROOT / DATA / 'network.csv').open('w', encoding='utf-8', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(nodes)
    fleet = read(SOURCE / 'fleet.json')
    fleet['vehicles_per_satellite'] = 8
    write(DATA / 'fleet.json', fleet)
    economic = read(SOURCE / 'economic.json')
    write(DATA / 'economic.json', economic)
    policy = read(SOURCE / 'reference_policy.json')
    policy['description'] = 'Three equal reference waves only; M8 optimizes its actual calendar.'
    orders = read(SOURCE / 'reference_orders.json')
    by_customer = {o['customer_id']: deepcopy(o) for o in orders}
    counts = Counter(assignment.values())
    quotas = balanced_counts([counts[s] for s in ids])
    rng = random.Random(20261020)
    strata, generated = {}, []
    for sid, quota in zip(ids, quotas):
        local = sorted(c for c in assignment if assignment[c] == sid)
        rng.shuffle(local)
        start = 0
        for index, number in enumerate(quota):
            for cid in local[start:start+number]:
                o = deepcopy(by_customer[cid])
                ready = 480+260*index+60+rng.randint(-10, 10)
                o.update(satellite_id=sid, delivery_ready=ready, delivery_due=ready+90,
                         pickup_ready=ready+80, pickup_due=ready+185, release_time=480,
                         delivery_batch_id=f'b{index+1}', allowed_batch_ids=['b1', 'b2', 'b3'])
                strata[o['order_id']] = index+1
                generated.append(o)
            start += number
    generated.sort(key=lambda o: o['order_id'])
    write(DATA / 'reference_orders.json', generated)
    write(DATA / 'reference_policy.json', policy)
    common = dict(schema_version=2, solver='alns', data=str(DATA/'network.csv'),
                  routing_config=str(DATA/'fleet.json'), economic_config=str(DATA/'economic.json'),
                  container_pool_size=100, alns_time=60, alns_iter=1000000000,
                  seed=42, save_plots=False, base_case_id='L05_3sat_balanced',
                  dataset_version=DATA.name)
    for label in LABELS:
        model = 3 if label == 'M3' else 8
        adapted_orders, adapted_policy = deepcopy(generated), deepcopy(policy)
        if model == 3:
            # Same final reference recovery cutoff (19:42), same 21:00 end.
            adapted_policy['batches'] = [dict(id='b1', delivery_duration=702, return_duration=78)]
            for o in adapted_orders:
                o.update(delivery_batch_id='b1', allowed_batch_ids=['b1'])
        write(DATA / label / 'orders.json', adapted_orders)
        write(DATA / label / 'policy.json', adapted_policy)
        config = dict(common, model=model, case_name=f'{DATA.name}_{label}',
                      orders=str(DATA/label/'orders.json'), operating_policy=str(DATA/label/'policy.json'),
                      exact_backend='multibatch_indexed' if model == 3 else 'joint_batch_indexed')
        if model == 8:
            config['joint_batch_options'] = dict(schedule_mode='flexible_cycle',
                batch_count_candidates=[3] if label == 'M8_K3' else list(range(1, 101)),
                min_phase_minutes=1, pool_min=1, pool_max=100)
        write(DATA / label / 'case.json', config)
    checks = {}
    for label in LABELS:
        cfg, fc = load_routing_case(DATA / label / 'case.json', use_pool_config=False)
        instance = load_instance(cfg, fc)
        if label == 'M3':
            checks[label] = check_necessary_conditions(instance)
            assert checks[label]['passed'], checks[label]['errors']
        elif label == 'M8':
            cert = productive_count_certificate(instance)
            assert cert['candidates_by_model']['8'] == list(range(1, 101))
            write(DATA / 'batch_count_certificate.json', cert)
            reference = instance.fixed_calendar(3)
            cutoffs = {b.id:b.start+reference.operating_policy.ticks(182)
                       for b in reference.operating_policy.batches}
            projected = reference.project({o['order_id']: o['delivery_batch_id'] for o in generated}, 100, cutoffs)
            checks['reference_K3_N100'] = check_necessary_conditions(projected)
            assert checks['reference_K3_N100']['passed'], checks['reference_K3_N100']['errors']
    assert len(generated) == len({o['customer_id'] for o in generated}) == 100
    assert sum(o['container_count'] for o in generated) == 100
    physical_keys = ('customer_id', 'goods_weight', 'container_count', 'service_time', 'min_use_time')
    assert all(all(o[k] == by_customer[o['customer_id']][k] for k in physical_keys) for o in generated)
    # The model adaptations preserve all physical order fields and IDs.
    a, b = read(DATA/'M3/orders.json'), read(DATA/'M8/orders.json')
    excluded = {'delivery_batch_id', 'allowed_batch_ids'}
    assert [{k:v for k,v in o.items() if k not in excluded} for o in a] == [
        {k:v for k,v in o.items() if k not in excluded} for o in b]
    write(DATA / 'input_checks.json', checks)
    design = dict(date='2026-10-20', mode='continuous', design_seed=20261020,
        purpose='A single synthetic mechanism check, not a sensitivity study or real business calibration.',
        source=str(SOURCE), source_hashes={p.name: sha(p) for p in SOURCE.iterdir() if p.is_file()},
        selected_satellites=list(ids), subset_selection='Exhaust all 10 subsets; minimize sum of nearest-customer distances; deterministic ID tie break.',
        selection_score=score, customers=100, orders=100, Q=100,
        orders_per_satellite=dict(counts), demand_stratum_counts=dict(zip(ids, quotas)),
        demand_strata=[35,35,30], order_strata=strata,
        time_generation='Three exogenous 260-minute service periods; delivery ready=start+60+U_integer[-10,10], width=90; pickup ready=delivery ready+80, width=105. Original minimum use time retained. All goods released at 08:00. No actual M8 batch binding.',
        preserved=['All customer/depot coordinates', 'Selected satellite coordinates', 'Order/customer identity',
                   'Original goods weights, unit box demand, service and minimum use times',
                   'Cycle 08:00-21:00, capacities, speed, cleaning=15 minutes and handling times', 'Economic coefficients'],
        changed=['5 satellites to 3; nearest selected satellite assignment', 'Synthetic balanced appointment windows',
                 'E2 fleet: 25 to 24 (-4%); both compared models use exactly the same 24 vehicles'],
        fleet=dict(E1=5, E2_per_satellite=8, E2_total=24),
        protocol=dict(seeds=[17,42,2026], seconds_per_run=60, iterations=1000000000,
                      groups=list(LABELS), starts='Solver-native, no external witness or cross-model warm start',
                      M3='K=1, N=Q=100, distance objective; common cost independently recomputed',
                      M8='K=1..100, N=1..100, flexible calendar and order batches, cost objective',
                      M8_K3='Same physical input, explicitly restricted K=3 control',
                      audit='Independent schedule replay, capacities, windows, pool, washing, clean terminal state and two-cycle replay'),
        limitations=['One deliberately balanced synthetic scenario cannot establish robustness or warehouse-count causality.',
                     'Demand strata are input appointments, not optimized batch statistics.',
                     'ALNS best-found results have no global optimality certificate.',
                     'Native M3 optimizes unweighted distance; M8 optimizes common weighted cost.',
                     'Current coefficients price distance and boxes; fixed vehicle, work and trip rates are zero.'])
    write(DATA / 'design.json', design)
    print(json.dumps({k:design[k] for k in ('selected_satellites','orders_per_satellite','demand_stratum_counts','fleet')}, ensure_ascii=False), flush=True)


def summarize(root):
    rows = []
    for label in LABELS:
        for folder in sorted((PROJECT_ROOT/root/label).glob('seed_*')):
            if not (folder/'result.json').exists():
                continue
            result = json.loads((folder/'result.json').read_text())
            cfg, fc = load_routing_case(DATA/label/'case.json', use_pool_config=False)
            instance = load_instance(cfg, fc)
            audit = audit_solution(instance, result) if result['info'].get('objective') is not None else {}
            write(folder.relative_to(PROJECT_ROOT)/'independent_audit.json', audit)
            row = dict(group=label, seed=int(folder.name.split('_')[1]),
                       status=result['info']['status'], valid=audit.get('valid',False),
                       runtime=result.get('wall_time'), source=str(folder.relative_to(PROJECT_ROOT)))
            if row['valid']:
                priced = evaluate_comparison_cost(instance, result, str(DATA/'economic.json'), audit=audit)
                write(folder.relative_to(PROJECT_ROOT)/'comparison_cost.json', priced)
                pool = priced['container_pool_size']
                row.update(N=pool, Q=100, saving_rate=(100-pool)/100, turnover=100/pool,
                           K=result.get('cycle_batch_count',1), total_cost=priced['total'],
                           D1=audit['distances']['e1'], D2=audit['distances']['e2'],
                           trips1=audit['e1_trip_count'], trips2=audit['e2_trip_count'],
                           vehicles1=audit['e1_vehicle_count'], vehicles2=audit['e2_vehicle_count'],
                           two_cycle_valid=audit['periodic_replay']['valid'],
                           recovery_rounds=result.get('actual_recovery_round_count',1),
                           cost_components=priced['components'])
            rows.append(row)
    write(root/'summary.json', rows)
    columns = sorted({key for row in rows for key in row if key != 'cost_components'})
    with (PROJECT_ROOT/root/'summary.csv').open('w',encoding='utf-8-sig',newline='') as stream:
        writer=csv.DictWriter(stream,fieldnames=columns,extrasaction='ignore')
        writer.writeheader(); writer.writerows(rows)
    lines = ['# L05 三仓均衡需求机制验证', '', '## Material Passport', '',
        '- Origin Skill: academic-research-suite / experiment-agent', '- Origin Mode: run',
        '- Origin Date: 2026-10-20', '- Verification Status: ANALYZED (independent replay; no reproducibility rerun)',
        '- Version Label: mechanism_l05_3sat_balanced_v1', '',
        '100顾客、100订单、Q=100，保留 L05 坐标、货重、容量、服务/使用/清洗时间及费用系数。',
        '3仓按纯空间距离规则选择，三个外生服务时段需求35/35/30；M8实际分批由模型决定。',
        '两模型相同5辆一级车、24辆二级车（原L05为25辆）；本实验不能估计从5仓改为3仓的单独因果效应。',
        '原始输入与旧结果保留。详细生成规则和预先声明协议见 data/mechanism_l05_3sat_balanced_v1/design.json。', '',
        '| 组别 | 种子 | 审计 | N | K | 一级距离 | 二级距离 | 一级趟次 | 二级趟次 | 总成本 |',
        '|---|---:|---|---:|---:|---:|---:|---:|---:|---:|']
    for r in rows:
        if r['valid']:
            lines.append(f"| {r['group']} | {r['seed']} | 通过 | {r['N']} | {r['K']} | {r['D1']:.2f} | {r['D2']:.2f} | {r['trips1']} | {r['trips2']} | {r['total_cost']:.2f} |")
        else:
            lines.append(f"| {r['group']} | {r['seed']} | {r['status']} | — | — | — | — | — | — | — |")
    lines += ['', '## 多种子描述统计', '', '| 组别 | 成功数 | 平均N | 平均成本 | 成本标准差 | 最好成本 |', '|---|---:|---:|---:|---:|---:|']
    for label in LABELS:
        local=[r for r in rows if r['group']==label]; valid=[r for r in local if r['valid']]
        if valid:
            costs=[r['total_cost'] for r in valid]
            lines.append(f"| {label} | {len(valid)}/{len(local)} | {statistics.mean(r['N'] for r in valid):.2f} | {statistics.mean(costs):.2f} | {statistics.stdev(costs) if len(costs)>1 else 0:.2f} | {min(costs):.2f} |")
    lines += ['', '成本统一按 0.1D1+0.05D2+5N+0.02Q 重算，单位为合成成本；距离为原坐标单位。',
        'M3原生优化总距离；M8原生优化总成本。M3的成本是相同系数下重算值，不能声称其成本最优。',
        '工作时长、车辆固定费、每趟固定费系数为0；实际趟次单独报告，不能声称已覆盖全部运输组织成本。',
        'M8_K3为限制批次数的控制实验；M8完整域保留1至100，未访问候选及预算见各result.json的count_search。',
        '每仓每批q应按所选解的实际分批另行解释，35/35/30只描述外生时段。',
        '单一均衡机制算例和3个种子的描述结果不支持普遍优势、显著性或最优批次数结论。',
        '失败运行也保留；没有按结果调整窗口、成本、清洗时间或选仓规则。', '']
    (PROJECT_ROOT/root/'report.md').write_text('\n'.join(lines),encoding='utf-8')
    return rows


def run(seconds, seeds, root):
    from cli.multibatch import run_case
    if (PROJECT_ROOT/root).exists():
        raise FileExistsError(f'Refusing to replace {root}')
    (PROJECT_ROOT/root).mkdir(parents=True)
    source_paths = [p.relative_to(PROJECT_ROOT) for directory in ('models','cli','tools','experiments')
                    for p in (PROJECT_ROOT/directory).rglob('*.py')]
    frozen = {str(p):sha(p) for p in source_paths}
    inputs = {str(p.relative_to(PROJECT_ROOT)):sha(p) for p in (PROJECT_ROOT/DATA).rglob('*') if p.is_file()}
    write(root/'execution.json', dict(python=platform.python_version(), platform=platform.platform(),
        seconds=seconds, seeds=seeds, start_time=time.time(), source_sha256=frozen, input_sha256=inputs,
        git_head=subprocess.check_output(['git','rev-parse','HEAD'],cwd=PROJECT_ROOT,text=True).strip(),
        command=f'.venv/bin/python -m experiments.runs.run_l05_dense_pilot run --seconds {seconds} --seeds '+ ' '.join(map(str,seeds)),
        hard_timeout_seconds=1800, external_initial_solutions=False))
    (PROJECT_ROOT/root/'working_tree.patch').write_bytes(subprocess.check_output(['git','diff','--binary'],cwd=PROJECT_ROOT))
    for seed in seeds:
        for label in LABELS:
            print(f'START {label} seed={seed} budget={seconds}s',flush=True)
            cfg, fc = load_routing_case(DATA/label/'case.json', use_pool_config=False)
            cfg.update(seed=seed, alns_time=seconds, alns_iter=1000000000)
            run_case(cfg,fc,case_path=DATA/label/'case.json',output=root/label/f'seed_{seed}')
            rows=summarize(root)
            print('LATEST '+json.dumps(next(r for r in rows if r['group']==label and r['seed']==seed),ensure_ascii=False),flush=True)
    changed=[str(p) for p in frozen if sha(p)!=frozen[p]]
    changed_inputs=[p for p in inputs if sha(p)!=inputs[p]]
    write(root/'integrity.json',dict(source_changes_during_run=changed,input_changes_during_run=changed_inputs,
                                   finished_at=time.time(),passed=not changed and not changed_inputs))
    if changed or changed_inputs:
        raise RuntimeError('Source/input changed during execution; inspect integrity.json')


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('stage',choices=('build','run','summarize'))
    parser.add_argument('--seconds',type=float,default=60)
    parser.add_argument('--seeds',type=int,nargs='+',default=[42])
    parser.add_argument('--output',default=str(OUT))
    args=parser.parse_args()
    if args.stage=='build': build()
    elif args.stage=='run': run(args.seconds,args.seeds,Path(args.output))
    else: summarize(Path(args.output))
