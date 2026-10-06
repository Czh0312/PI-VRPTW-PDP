"""Fresh 300-second runs excluding work-time, vehicle and trip fixed charges."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timezone, timedelta
import csv
import hashlib
import json
import math
import os
from pathlib import Path
import random
import shutil
import subprocess
import sys
import threading
import time
import traceback

ROOT = Path(__file__).resolve().parents[3]
OUT = Path(__file__).resolve().parent
MODELS = (3, 5, 6, 7, 8)
FAMILIES = [
    'zhou_Set2_100-10-1_subset50', 'zhou_Set2_100-10-1',
    'zhou_Set2_100-10-2_subset50', 'zhou_Set2_100-10-2',
    'zhou_Set2_100-5-1b_subset50', 'zhou_Set2_100-5-1b',
]
MODES = {6: 'uniform_cycle', 7: 'uniform_independent_cutoffs', 8: 'flexible_cycle'}
BUDGET, ITERATIONS, SEED, WORKERS = 300.0, 1000000, 42, 4
PRIOR = ROOT/'output/model35678_comparison/20261006_5min_v1'
PRICING = PRIOR/'reprice_distance_pool_v1'
ZERO_FIELDS = ('e1_work_cost_per_minute', 'e2_work_cost_per_minute',
               'e1_vehicle_cost_per_cycle', 'e2_vehicle_cost_per_cycle',
               'e1_trip_cost', 'e2_trip_cost')


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix+'.tmp')
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)+'\n', encoding='utf-8')
    tmp.replace(path)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def register():
    sys.path.insert(0, str(ROOT))
    from configuration.loader import load_routing_case, load_json
    from models.cost.joint_batch import JointCostProfile
    from models.routing.common.cycle.instance import load_instance
    from models.routing.common.cycle.precheck import check_necessary_conditions
    from experiments.runs.run_model6_comparison import freeze_case, snapshot_source
    if (OUT/'experiment.json').exists():
        raise ValueError('Refusing to replace a registered study')
    for folder in ('inputs', 'cases', 'results', 'logs'):
        (OUT/folder).mkdir(exist_ok=True)
    economics = read(PRICING/'common_economic.json')
    assert all(economics[key] == 0 for key in ZERO_FIELDS)
    common = OUT/'inputs/common_economic.json'
    write(common, economics)
    profile = JointCostProfile(economics)
    assert profile.hash == read(PRICING/'summary.json')['revised_profile_hash']
    records, jobs, input_hashes = [], [], {}
    for index, family in enumerate(FAMILIES):
        source = ROOT/'data/model5_model6/shared_v1_20261005'/family/'spread_medium'
        source_cases = [source/'m5_case.json', source/'m6_case.json']
        originals = [load_routing_case(p, use_pool_config=False) for p in source_cases]
        p5, p6 = [load_instance(*v) for v in originals]
        assert p5.orders == p6.orders and p5.operating_policy == p6.operating_policy
        for key in ('data', 'orders', 'routing_config', 'operating_policy', 'distance_matrix', 'duration_matrix'):
            assert originals[0][0].get(key) == originals[1][0].get(key), (family, key)
        for config, _ in originals:
            assert JointCostProfile(load_json(config['economic_config'])).hash == read(PRIOR/'experiment.json')['economic_profile_hash']
        base, hashes = freeze_case(source_cases[1], OUT, f'{index}_shared')
        input_hashes.update(hashes)
        base.update(economic_config=str(common), save_plots=False, alns_iter=ITERATIONS, alns_time=BUDGET, seed=SEED)
        # All physical inputs match the previous five-minute run.
        old_config, _ = load_routing_case(PRIOR/'cases'/f'{index}_M5.json', use_pool_config=False)
        for key in ('data', 'orders', 'routing_config', 'operating_policy', 'distance_matrix', 'duration_matrix'):
            if key in base:
                assert sha(base[key]) == sha(old_config[key]), (family, key)
        policy = p5.operating_policy
        records.append(dict(family=family, customers=len(p5.network.customers),
            satellites=len(p5.satellites), orders=len(p5.orders), demand=p5.total,
            source_m5=str(source_cases[0]), source_m6=str(source_cases[1]),
            batch_count_candidates=base['joint_batch_options']['batch_count_candidates'],
            m3_fixed_cutoff_minutes=policy.minutes(policy.batches[-1].cutoff),
            m3_single_batch_rule='Collapse source calendar; preserve original final cutoff and cycle boundaries'))
        for model in MODELS:
            cfg = deepcopy(base)
            cfg.update(model=model, case_name=f'{family}_M{model}',
                exact_backend='multibatch_indexed' if model in (3,5) else 'joint_batch_indexed')
            if model in (3,5):
                cfg.pop('joint_batch_options', None)
            else:
                cfg['joint_batch_options']['schedule_mode'] = MODES[model]
            if model == 3:
                orders = read(base['orders'])
                normalized = [dict(row, delivery_batch_id='b1', allowed_batch_ids=['b1']) for row in orders]
                order_path = OUT/'inputs'/f'{index}_M3_orders.json'
                write(order_path, normalized)
                single_policy = read(base['operating_policy'])
                single_policy['batches'] = [dict(id='b1',
                    delivery_duration=policy.minutes(policy.batches[-1].cutoff-policy.cycle_start),
                    return_duration=policy.minutes(policy.cycle_end-policy.batches[-1].cutoff))]
                policy_path = OUT/'inputs'/f'{index}_M3_policy.json'
                write(policy_path, single_policy)
                cfg.update(orders=str(order_path), operating_policy=str(policy_path))
            case_path = OUT/'cases'/f'{index}_M{model}.json'
            write(case_path, cfg)
            problem = load_instance(*load_routing_case(case_path, use_pool_config=False))
            if model == 3:
                expected = tuple(replace(o, batch='b1', allowed_batch_ids=('b1',)) for o in p5.orders)
                assert problem.orders == expected
                assert replace(problem.operating_policy, batches=policy.batches) == policy
                check = check_necessary_conditions(problem)
                write(OUT/'inputs'/f'{index}_M3_precheck.json', check)
            else:
                assert problem.orders == p5.orders and problem.operating_policy == policy
            jobs.append(dict(id=f'{index:02d}_M{model}_alns_{SEED}', family=family, model=model,
                method='alns', seed=SEED, budget=BUDGET, iterations=ITERATIONS, case_path=str(case_path)))
    random.Random(20261006).shuffle(jobs)
    sources = snapshot_source(OUT)
    write(OUT/'experiment.json', dict(
        registered_at=datetime.now(timezone(timedelta(hours=8))).isoformat(),
        commit=subprocess.check_output(['git','rev-parse','HEAD'], cwd=ROOT, text=True).strip(),
        selection='Three preselected spatial families, each with 50-customer subset and full 100 customers; six medium-window cases. No outcome-based selection.',
        cases=records, jobs=jobs, workers=WORKERS, alns_seconds=BUDGET, iterations=ITERATIONS,
        seeds=[SEED], models=MODELS, economic_profile=economics, economic_profile_hash=profile.hash,
        common_economic_sha256=sha(common), source_hashes=sources, input_hashes=input_hashes,
        zero_cost_fields=ZERO_FIELDS, previous_study=str(PRIOR),
        previous_repriced_study=str(PRICING), fresh_search=True,
        runner_sha256=sha(__file__), python=sys.version, interpreter=sys.executable,
        objectives={'3':'distance, repriced after independent audit','5':'distance, repriced after independent audit',
                    '6':'cost','7':'cost','8':'cost'},
        m3_transform='Only calendar grouping and order batch labels change. Physical orders, absolute windows, release, fleet, cleaning, cycle, pool and prices remain the same.',
        limitations=['Synthetic costs, not CNY.', 'One seed per case; no randomness stability claim.',
                     'Limited time is not an optimality certificate.', 'Nested sizes are not independent population samples.',
                     '4 workers share the machine; budgets are soft deadlines, not a speed benchmark.',
                     'M3 and M5 have distance objectives; M6--M8 have cost objectives.']))
    print(f'REGISTERED {len(records)} cases / {len(jobs)} runs / {BUDGET:g}s / seed {SEED} / {WORKERS} workers', flush=True)


def worker(job_id):
    plan = read(OUT/'experiment.json')
    job = next(r for r in plan['jobs'] if r['id'] == job_id)
    target = OUT/'results'/job_id
    if target.exists():
        raise ValueError('Refusing to overwrite a run')
    target.mkdir()
    sys.path.insert(0, str(OUT/'source_snapshot'))
    from configuration.loader import load_routing_case
    from models.routing.common.cycle.instance import load_instance
    from models.routing.common.cycle.audit import audit_solution
    from models.cost.comparison import evaluate_comparison_cost
    from models.routing.service import solve_routing
    for name, digest in plan['source_hashes'].items():
        assert sha(OUT/'source_snapshot'/name) == digest
    assert sha(OUT/'inputs/common_economic.json') == plan['common_economic_sha256']
    config, fleet = load_routing_case(job['case_path'], use_pool_config=False)
    problem = load_instance(config, fleet)
    write(target/'input_manifest.json', dict(config=config, fleet=fleet,
        input_sha256={key:sha(value) for key,value in config.items()
                      if isinstance(value,str) and Path(value).is_file()}))
    begin = time.perf_counter()
    stop = threading.Event()
    def heartbeat():
        import psutil
        process = psutil.Process()
        while not stop.is_set():
            cpu=process.cpu_times()
            write(target/'live_status.json',dict(job_id=job_id,stage='solving',
                elapsed_seconds=time.perf_counter()-begin,
                memory_MiB=process.memory_info().rss/2**20,cpu_seconds=cpu.user+cpu.system))
            stop.wait(15)
    monitor=threading.Thread(target=heartbeat,daemon=True)
    monitor.start()
    try:
        result = solve_routing(model=job['model'], method='alns', problem=problem,
            alns_time=job['budget'], alns_iter=job['iterations'], seed=job['seed'])
    finally:
        stop.set()
        monitor.join(timeout=2)
    elapsed = time.perf_counter()-begin
    write(target/'result.json', result)
    info = result['info']
    row = {key:value for key,value in job.items() if key!='case_path'}
    row.update(status=info['status'], valid=False, cost=None, distance=None, pool=None,
        minimum_pool=None, minimum_pool_reference_cost=None, cost_components=None,
        objective=info.get('objective'), proven_optimal=bool(info.get('proven_optimal')),
        proven_infeasible=bool(info.get('proven_infeasible')), best_bound=info.get('best_bound'),
        solve_wall_seconds=elapsed, iterations_completed=info.get('iterations'),
        economic_profile_hash=None, k=None, delivery_batches=None, audit_errors=[])
    if info.get('objective') is not None:
        audit = audit_solution(problem, result)
        write(target/'independent_audit.json', audit)
        row.update(valid=bool(audit['valid']), audit_errors=audit['errors'])
        if audit['valid']:
            assert audit['periodic_replay']['valid']
            cost = evaluate_comparison_cost(problem, result, config['economic_config'], audit=audit)
            assert cost['economic_profile_hash'] == plan['economic_profile_hash']
            assert math.isclose(cost['total'], sum(cost['components'].values()), abs_tol=1e-6)
            if job['model'] in (6,7,8):
                assert math.isclose(cost['total'], info['objective'], abs_tol=1e-5, rel_tol=1e-7)
            write(target/'comparison_cost.json', cost)
            row.update(cost=cost['total'], cost_components=cost['components'],
                economic_profile_hash=cost['economic_profile_hash'],
                distance=sum(audit['distances'].values()), pool=cost['container_pool_size'],
                minimum_pool=audit['minimum_pool_for_schedule'],
                minimum_pool_reference_cost=cost.get('schedule_minimum_pool_reference',{}).get('total'),
                k=result.get('cycle_batch_count',len(problem.operating_policy.batches)),
                delivery_batches=result.get('delivery_batch_count',sum(bool(v) for v in problem.batch_demands.values())),
                e1_trips=audit['e1_trip_count'], e2_trips=audit['e2_trip_count'],
                e1_vehicles=audit['e1_vehicle_count'], e2_vehicles=audit['e2_vehicle_count'])
        else:
            row.update(status='AUDIT_FAILED', proven_optimal=False)
    write(target/'row.json', row)
    write(target/'live_status.json',dict(job_id=job_id,stage='complete',elapsed_seconds=elapsed,
                                      status=row['status'],valid=row['valid']))
    print(json.dumps(row,ensure_ascii=False),flush=True)


def execute(job):
    env = {**os.environ,'PYTHONIOENCODING':'utf-8','PYTHONUTF8':'1','PYTHONHASHSEED':'0'}
    try:
        process = subprocess.run([sys.executable,str(Path(__file__).resolve()),'--job',job['id']],
            cwd=ROOT,env=env,capture_output=True,text=True,encoding='utf-8',errors='replace',
            timeout=job['budget']+180)
        (OUT/'logs'/f"{job['id']}.log").write_text(process.stdout+'\n'+process.stderr,encoding='utf-8')
        if process.returncode:
            raise RuntimeError(f'Worker exited {process.returncode}; logs/{job["id"]}.log')
        return read(OUT/'results'/job['id']/'row.json')
    except Exception as exc:
        if isinstance(exc,subprocess.TimeoutExpired):
            stdout=exc.stdout.decode('utf-8',errors='replace') if isinstance(exc.stdout,bytes) else exc.stdout or ''
            stderr=exc.stderr.decode('utf-8',errors='replace') if isinstance(exc.stderr,bytes) else exc.stderr or ''
            (OUT/'logs'/f"{job['id']}.log").write_text(stdout+'\n'+stderr,encoding='utf-8')
        return dict(**{k:v for k,v in job.items() if k!='case_path'},valid=False,cost=None,distance=None,
            status='HARD_TIMEOUT' if isinstance(exc,subprocess.TimeoutExpired) else 'ERROR',
            proven_optimal=False,error=str(exc))


def run():
    plan = read(OUT/'experiment.json')
    if (OUT/'rows.json').exists():
        raise ValueError('Refusing to overwrite previous rows')
    rows=[]
    with ThreadPoolExecutor(max_workers=WORKERS) as executor:
        futures={executor.submit(execute,job):job for job in plan['jobs']}
        for future in as_completed(futures):
            row=future.result()
            rows.append(row)
            write(OUT/'rows.json',rows)
            print(f'{len(rows)}/{len(plan["jobs"])} {row["family"]} M{row["model"]}: '
                  f'{row["status"]}, cost={row["cost"]}, valid={row["valid"]}',flush=True)
    summarize()


def summarize():
    # The report writer is defined below before the launcher.
    return build_report()


def export_csv(path, rows):
    columns=sorted({key for row in rows for key in row if key not in ('cost_components','audit_errors')})
    with Path(path).open('w',newline='',encoding='utf-8-sig') as handle:
        writer=csv.DictWriter(handle,columns,extrasaction='ignore')
        writer.writeheader()
        writer.writerows(rows)


def build_report():
    plan = read(OUT/'experiment.json')
    rows = read(OUT/'rows.json')
    assert len(rows) == len(plan['jobs'])
    valid = [row for row in rows if row['valid']]
    assert all(row['economic_profile_hash'] == plan['economic_profile_hash'] for row in valid)
    cases = []
    for record in plan['cases']:
        selected = {str(model): next((row for row in rows
            if row['family'] == record['family'] and row['model'] == model and row['valid']), None)
            for model in MODELS}
        cases.append(dict(**record, selected=selected))
    payload = dict(planned=len(plan['jobs']), completed=len(rows), valid=len(valid),
        failures=[row for row in rows if not row['valid']], models=MODELS,
        budget_seconds=BUDGET, seeds=[SEED], economic_profile_hash=plan['economic_profile_hash'],
        cases=cases, fresh_search=True, zero_cost_fields=ZERO_FIELDS)
    write(OUT/'summary.json', payload)
    export_csv(OUT/'runs.csv', rows)
    lines = ['# 新成本口径：五分钟大规模模型对比', '',
        '占用时间费、每辆车固定费、每趟固定费在一级、二级运输均为零。六个原算例、五个模型重新搜索，每次300秒，seed=42，四进程。', '',
        f'完成 {len(rows)}/{len(plan["jobs"])} 次，独立双周期审计通过 {len(valid)} 次。', '',
        '| 算例 | 点数 | 需求箱次 | M3 | M5 | M6 | M7 | M8 |',
        '|---|---:|---:|---:|---:|---:|---:|---:|']
    for case in cases:
        values = [f'{case["selected"][str(model)]["cost"]:.2f}'
                  if case['selected'][str(model)] else '—' for model in MODELS]
        lines.append(f'| {case["family"]} | {case["customers"]} | {case["demand"]} | '
                     + ' | '.join(values) + ' |')
    lines += ['', 'M3/M5保留距离目标；M6–M8优化新的周期成本目标。所有方案仍满足时间窗、车队、容器、清洗和周期末完整回收约束。',
              '', '这是单种子、有限预算的可行解比较，没有全局最优证明。完整方案、独立审计和成本在 results/，冻结输入在 inputs/、cases/，代码在 source_snapshot/。']
    (OUT/'report.md').write_text('\n'.join(lines)+'\n', encoding='utf-8')
    print(f'COMPLETE {len(valid)}/{len(rows)} valid; {OUT/"report.md"}', flush=True)
    return payload


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--job')
    parser.add_argument('--summarize',action='store_true')
    args=parser.parse_args()
    if args.job:
        worker(args.job)
    elif args.summarize:
        summarize()
    else:
        register()
        run()
