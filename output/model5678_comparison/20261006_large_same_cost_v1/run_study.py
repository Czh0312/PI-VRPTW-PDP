"""Run M5--M8 on frozen shared inputs and one common economic profile."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from copy import deepcopy
import csv
from datetime import datetime, timezone, timedelta
import hashlib
import json
import math
import os
from pathlib import Path
import random
import statistics
import subprocess
import sys
import time
import traceback

ROOT = Path(__file__).resolve().parents[3]
OUT = Path(__file__).resolve().parent
FAMILIES = ['zhou_Set2_100-10-1_subset50', 'zhou_Set2_100-10-1']
MODES = {6: 'uniform_cycle', 7: 'uniform_independent_cutoffs', 8: 'flexible_cycle'}


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    tmp.replace(path)


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def register():
    sys.path.insert(0, str(ROOT))
    from configuration.loader import load_routing_case, load_json
    from models.cost.joint_batch import JointCostProfile
    from models.routing.common.cycle.instance import load_instance
    from experiments.runs.run_model6_comparison import freeze_case, snapshot_source
    if (OUT/'experiment.json').exists():
        raise ValueError('Refusing to overwrite registered experiment')
    for name in ['inputs', 'cases', 'results', 'logs']:
        (OUT/name).mkdir(exist_ok=True)
    economic_source = ROOT/'output/model5678_comparison/20261006_same_cost_v1/inputs/common_economic.json'
    economics = load_json(economic_source)
    common = OUT/'inputs/common_economic.json'
    write(common, economics)
    profile = JointCostProfile(economics)
    hashes = {}
    records, exact_jobs, alns_jobs = [], [], []
    for index, family in enumerate(FAMILIES):
        source = ROOT/'data/model5_model6/shared_v1_20261005'/family/'spread_medium'
        paths = [source/'m5_case.json', source/'m6_case.json']
        originals = [load_routing_case(p, use_pool_config=False) for p in paths]
        problems = [load_instance(*v) for v in originals]
        assert problems[0].orders == problems[1].orders
        assert problems[0].operating_policy == problems[1].operating_policy
        for key in ['data', 'routing_config', 'distance_matrix', 'duration_matrix', 'orders', 'operating_policy']:
            a, b = originals[0][0].get(key), originals[1][0].get(key)
            assert a == b, (family, key)
        for cfg, _ in originals:
            assert JointCostProfile(load_json(cfg['economic_config'])).hash == profile.hash
        base, frozen_hashes = freeze_case(paths[1], OUT, f'{index}_shared')
        hashes.update(frozen_hashes)
        base.update(economic_config=str(common), save_plots=False, alns_iter=60000)
        record = dict(family=family, customers=len(problems[0].network.customers),
                      orders=len(problems[0].orders), demand=problems[0].total,
                      source_m5=str(paths[0]), source_m6=str(paths[1]),
                      batch_count_candidates=base['joint_batch_options']['batch_count_candidates'])
        records.append(record)
        for model in (5, 6, 7, 8):
            cfg = deepcopy(base)
            cfg.update(model=model, case_name=f'{family}_M{model}',
                       exact_backend='multibatch_indexed' if model == 5 else 'joint_batch_indexed')
            if model == 5:
                cfg.pop('joint_batch_options', None)
            else:
                cfg['joint_batch_options']['schedule_mode'] = MODES[model]
            case_path = OUT/'cases'/f'{index}_M{model}.json'
            write(case_path, cfg)
            problem = load_instance(*load_routing_case(case_path, use_pool_config=False))
            assert problem.orders == problems[0].orders
            assert problem.operating_policy == problems[0].operating_policy
            methods = [('alns', seed, 60.0) for seed in (42, 43, 44)]
            for method, seed, budget in methods:
                job = dict(id=f'{index:02d}_M{model}_{method}_{seed}', family=family,
                           model=model, method=method, seed=seed, budget=budget,
                           iterations=60000, case_path=str(case_path))
                (exact_jobs if method == 'exact' else alns_jobs).append(job)
    rng = random.Random(20261006)
    rng.shuffle(exact_jobs)
    rng.shuffle(alns_jobs)
    source_hashes = snapshot_source(OUT)
    jobs = exact_jobs + alns_jobs
    write(OUT/'experiment.json', dict(
        registered_at=datetime.now(timezone(timedelta(hours=8))).isoformat(),
        commit=subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
        selection='Preselected large medium-window shared scenarios: Zhou Set2 100-10-1 with 50-customer subset and full 100 customers; no outcome-based selection. These are nested cases, not independent population samples.',
        cases=records, jobs=jobs, workers=2, alns_seconds=60, exact_seconds=None,
        exact_scope='None: large-scale ALNS comparison only', seeds=[42, 43, 44], iterations=60000,
        economic_profile=economics, economic_profile_hash=profile.hash,
        input_hashes=hashes, source_hashes=source_hashes,
        common_economic_sha256=digest(common), runner_sha256=digest(__file__),
        objectives={'5': 'distance, repriced after independent audit', '6': 'cost', '7': 'cost', '8': 'cost'},
        selection_rule='M5 minimum distance then cost; M6--M8 minimum cost; separate ALNS-only comparisons',
        limitations=['Synthetic costs, not CNY.', 'Finite budgets and K domain; a feasible result is not a cost optimum.',
                     'M5 reference minimum-pool cost reprices unchanged schedule only.',
                     '2 workers share the machine; do not infer speed advantages.']))
    print(f'REGISTERED {len(records)} cases / {len(jobs)} runs / common profile {profile.hash}', flush=True)


def worker(job_id):
    plan = read(OUT/'experiment.json')
    job = next(v for v in plan['jobs'] if v['id'] == job_id)
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
    for name, sha in plan['source_hashes'].items():
        assert digest(OUT/'source_snapshot'/name) == sha
    assert digest(OUT/'inputs/common_economic.json') == plan['common_economic_sha256']
    cfg, fleet = load_routing_case(job['case_path'], use_pool_config=False)
    problem = load_instance(cfg, fleet)
    write(target/'input_manifest.json', dict(config=cfg, fleet=fleet,
          input_sha256={key: digest(value) for key, value in cfg.items()
                        if isinstance(value, str) and Path(value).is_file()}))
    started = time.perf_counter()
    result = solve_routing(model=job['model'], method=job['method'], problem=problem,
                           alns_time=job['budget'], exact_time=job['budget'],
                           alns_iter=job['iterations'], seed=job['seed'])
    elapsed = time.perf_counter() - started
    write(target/'result.json', result)
    info = result['info']
    row = {key: value for key, value in job.items() if key != 'case_path'}
    row.update(status=info['status'], valid=False, objective=info.get('objective'),
               proven_optimal=bool(info.get('proven_optimal')), proven_infeasible=bool(info.get('proven_infeasible')),
               best_bound=info.get('best_bound'), solve_wall_seconds=elapsed,
               cost=None, distance=None, pool=None, minimum_pool=None,
               m5_minimum_pool_reference=None, k=None, delivery_batches=None, recovery_rounds=None,
               economic_profile_hash=None, cost_components=None, audit_errors=[])
    if info.get('objective') is not None:
        audit = audit_solution(problem, result)
        write(target/'independent_audit.json', audit)
        row.update(valid=bool(audit['valid']), audit_errors=audit['errors'])
        if audit['valid']:
            cost = evaluate_comparison_cost(problem, result, cfg['economic_config'], audit=audit)
            assert cost['economic_profile_hash'] == plan['economic_profile_hash']
            assert math.isclose(sum(cost['components'].values()), cost['total'], rel_tol=1e-10, abs_tol=1e-7)
            if job['model'] != 5:
                assert math.isclose(cost['total'], info['objective'], rel_tol=1e-7, abs_tol=1e-5)
            write(target/'comparison_cost.json', cost)
            row.update(cost=cost['total'], cost_components=cost['components'],
                       economic_profile_hash=cost['economic_profile_hash'],
                       distance=sum(audit['distances'].values()), pool=cost['container_pool_size'],
                       minimum_pool=audit['minimum_pool_for_schedule'],
                       m5_minimum_pool_reference=cost.get('schedule_minimum_pool_reference', {}).get('total'),
                       k=result.get('cycle_batch_count', len(problem.operating_policy.batches)),
                       delivery_batches=result.get('delivery_batch_count', sum(bool(v) for v in problem.batch_demands.values())),
                       recovery_rounds=result.get('actual_recovery_round_count'),
                       e1_trips=audit['e1_trip_count'], e2_trips=audit['e2_trip_count'],
                       e1_vehicles=audit['e1_vehicle_count'], e2_vehicles=audit['e2_vehicle_count'])
        else:
            row.update(status='AUDIT_FAILED', proven_optimal=False)
    write(target/'row.json', row)
    print(json.dumps(row, ensure_ascii=False), flush=True)


def execute(job):
    env = {**os.environ, 'PYTHONIOENCODING': 'utf-8', 'PYTHONUTF8': '1', 'PYTHONHASHSEED': '0'}
    try:
        process = subprocess.run([sys.executable, str(Path(__file__).resolve()), '--job', job['id']],
                                 cwd=ROOT, env=env, capture_output=True, text=True, encoding='utf-8',
                                 errors='replace', timeout=job['budget'] + 90)
        (OUT/'logs'/f"{job['id']}.log").write_text(process.stdout + '\n' + process.stderr, encoding='utf-8')
        if process.returncode:
            raise RuntimeError(f'Worker exit {process.returncode}; logs/{job["id"]}.log')
        return read(OUT/'results'/job['id']/'row.json')
    except Exception as exc:
        if isinstance(exc, subprocess.TimeoutExpired):
            (OUT/'logs'/f"{job['id']}.log").write_text(str(exc), encoding='utf-8')
        return dict(**{k:v for k,v in job.items() if k != 'case_path'}, status='HARD_TIMEOUT' if isinstance(exc, subprocess.TimeoutExpired) else 'ERROR',
                    valid=False, cost=None, distance=None, proven_optimal=False, error=str(exc))


def run():
    plan = read(OUT/'experiment.json')
    if (OUT/'rows.json').exists():
        raise ValueError('Refusing to retry or overwrite existing rows')
    rows = []
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = {pool.submit(execute, job): job for job in plan['jobs']}
        for future in as_completed(futures):
            row = future.result()
            rows.append(row)
            write(OUT/'rows.json', rows)
            print(f'{len(rows)}/{len(plan["jobs"])} {row["family"]} M{row["model"]} {row["method"]}/{row["seed"]}: '
                  f'{row["status"]} cost={row["cost"]} valid={row["valid"]}', flush=True)
    summarize(rows, plan)


def choose(rows, model):
    group = [v for v in rows if v['model'] == model and v['valid']]
    key = (lambda v: (v['distance'], v['cost'], v['seed'], v['method'])) if model == 5 else (lambda v: (v['cost'], v['seed'], v['method']))
    return min(group, key=key) if group else None


def summarize(rows=None, plan=None):
    rows = read(OUT/'rows.json') if rows is None else rows
    plan = read(OUT/'experiment.json') if plan is None else plan
    valid = [v for v in rows if v['valid']]
    assert all(v['economic_profile_hash'] == plan['economic_profile_hash'] for v in valid)
    certificates = []
    for case in plan['cases']:
        for model in (6, 7, 8):
            group = [r for r in valid if r['family'] == case['family'] and r['model'] == model]
            exact = next((r for r in group if r['method'] == 'exact' and r['proven_optimal']), None)
            if exact:
                assert all(r['cost'] >= exact['cost'] - 1e-5 for r in group), (case['family'], model, 'claimed optimum beaten')
                certificates.append(exact['id'])
    columns = sorted({k for row in rows for k in row if k not in ('cost_components', 'audit_errors')})
    with (OUT/'runs.csv').open('w', newline='', encoding='utf-8-sig') as handle:
        writer = csv.DictWriter(handle, columns, extrasaction='ignore')
        writer.writeheader()
        writer.writerows(rows)
    selections, stats = [], []
    for case in plan['cases']:
        group = [v for v in rows if v['family'] == case['family']]
        selected = {str(m): choose(group, m) for m in (5, 6, 7, 8)}
        selections.append(dict(**case, selected=selected))
        for model in (5, 6, 7, 8):
            heuristic = [v for v in group if v['model'] == model and v['method'] == 'alns']
            values = [v['cost'] for v in heuristic if v['valid']]
            stats.append(dict(family=case['family'], model=model, attempted=len(heuristic), valid=len(values),
                              min=min(values) if values else None, mean=statistics.mean(values) if values else None,
                              max=max(values) if values else None, sd=statistics.stdev(values) if len(values)>1 else None))
    write(OUT/'summary.json', dict(planned=len(plan['jobs']), completed=len(rows), valid=len(valid),
          economic_profile_hash=plan['economic_profile_hash'], cases=selections, alns_statistics=stats,
          proven_cost_optimal_runs=certificates, failures=[v for v in rows if not v['valid']]))
    fmt = lambda v: '—' if v is None else f'{v:,.2f}'
    lines = ['# 大规模模型 5、6、7、8 同成本参数对比', '',
        '运行日期：2026-10-06。两个预先选定的 50/100 点共享 spread_medium 场景，每场景四模型使用相同订单、网络、车队、周期、清洗参数及同一份 common_economic.json。', '',
        'ALNS：每次 60 秒、最多 60000 次迭代，种子 42/43/44；两个进程。M6–M8 的总预算在 K=1/2/3 间共享。本轮只比较大规模 ALNS，不包含精确法。', '',
        f'全部尝试 {len(rows)}/{len(plan["jobs"])}，通过独立物理与成本审计 {len(valid)} 次。共同成本参数哈希：`{plan["economic_profile_hash"]}`。', '',
        '## 代表方案总成本', '',
        'M5 按最短距离选择（同距离按成本排序），M6–M8 按最低已观察成本选择；各模型均从三个 ALNS 种子的有效方案中选择。', '',
        '| 算例 | 点数 | 需求箱次 | M5 | M6 | M7 | M8 | M8 比 M5 降低 |',
        '|---|---:|---:|---:|---:|---:|---:|---:|']
    for case in selections:
        chosen = case['selected']
        costs = [chosen[str(m)]['cost'] if chosen[str(m)] else None for m in (5,6,7,8)]
        saving = (costs[0]-costs[3])/costs[0]*100 if costs[0] and costs[3] is not None else None
        lines.append(f'| {case["family"]} | {case["customers"]} | {case["demand"]} | ' + ' | '.join(fmt(v) for v in costs) + f' | {fmt(saving)}% |')
    lines += ['', '## 同预算 ALNS 的三个种子', '',
        '| 算例 | 模型 | 有效/尝试 | 最低成本 | 平均成本 | 最高成本 | 样本标准差 |',
        '|---|---|---:|---:|---:|---:|---:|']
    for row in stats:
        lines.append(f'| {row["family"]} | M{row["model"]} | {row["valid"]}/{row["attempted"]} | ' + ' | '.join(fmt(row[k]) for k in ('min','mean','max','sd')) + ' |')
    lines += ['', '## 代表方案资源及证明', '',
        '| 算例 | 模型 | 方法/种子 | 成本 | 箱池 | 原时间表最低需箱 | 周期K/配送批次 | 状态 | 证明目标 |',
        '|---|---|---|---:|---:|---:|---|---|---|']
    for case in selections:
        for model in (5,6,7,8):
            row = case['selected'][str(model)]
            if row:
                proof = ('距离最优' if model==5 else '成本最优') if row['proven_optimal'] else '未证最优'
                lines.append(f'| {case["family"]} | M{model} | {row["method"]}/{row["seed"]} | {fmt(row["cost"])} | {row["pool"]} | {row["minimum_pool"]} | {row["k"]}/{row["delivery_batches"]} | {row["status"]} | {proof} |')
    lines += ['', '## 模型5原时间表最低需箱量参考', '',
        '仅对原方案按审计得到的最低需箱量重新计价，路线及时间表保持原值；不是重新求解模型5或箱数全局最优证明。', '',
        '| 算例 | M5 原配置成本 | M5 最低池参考成本 | M6 | M7 | M8 |', '|---|---:|---:|---:|---:|---:|']
    for case in selections:
        chosen=case['selected']; baseline=chosen['5']
        values=[baseline['cost'] if baseline else None,baseline['m5_minimum_pool_reference'] if baseline else None]
        values += [chosen[str(m)]['cost'] if chosen[str(m)] else None for m in (6,7,8)]
        lines.append(f'| {case["family"]} | '+' | '.join(fmt(v) for v in values)+' |')
    lines += ['', '## 代表方案成本分项', '',
        '| 算例 | 模型 | 箱池 | 距离 | 占用工时 | 车辆 | 趟次 | 清洗/追踪/磨损 |', '|---|---|---:|---:|---:|---:|---:|---:|']
    for case in selections:
        for model in (5,6,7,8):
            row=case['selected'][str(model)]
            if row:
                c=row['cost_components']
                amounts=[c['container_pool'],c['e1_distance']+c['e2_distance'],c['e1_work']+c['e2_work'],
                         c['e1_vehicles']+c['e2_vehicles'],c['e1_trips']+c['e2_trips'],c['cleaning']+c['tracking']+c['container_wear']]
                lines.append(f'| {case["family"]} | M{model} | '+' | '.join(fmt(v) for v in amounts)+' |')
    lines += ['', '## 最优证明', '',
        '| 算例 | 模型 | 状态 | 成本 | 求解器目标下界 | 已证最优 |', '|---|---|---|---:|---:|---|']
    for row in rows:
        if row['method']=='exact':
            lines.append(f'| {row["family"]} | M{row["model"]} | {row["status"]} | {fmt(row["cost"])} | {fmt(row.get("best_bound"))} | {row["proven_optimal"]} |')
    lines += ['', '## 解释范围', '',
        '- 单位均为 synthetic_units（合成成本单位），不是人民币实测费用；箱体初始投入在 comparison_cost.json 单列，不并入周期成本。',
        '- M5 固定分批与箱池并优化距离，成本表是同价重算；M6–M8 直接优化成本。M5 的距离最优证明不能称为成本最优。',
        '- 充分求解时 M8≤M7≤M6；短预算搜索可能返回相反排序。未找到解不等于已证明不可行。',
        '- 全部运行（包含无解、限时和错误）保留在 runs.csv/rows.json，不能把失败成本写成零。',
        '- 这是 50 点子集与完整 100 点算例的有限预算比较，两者来自同一底图，不是独立总体样本；不能外推为全数据集平均收益或算法收敛结论。', '',
        '输入与源码分别冻结在 inputs/、cases/、source_snapshot/；各次原始解、独立审计和费用分项位于 results/；运行日志位于 logs/。']
    (OUT/'report.md').write_text('\n'.join(lines)+'\n', encoding='utf-8')
    print(f'COMPLETE {len(valid)}/{len(rows)} valid; {OUT / "report.md"}', flush=True)


if __name__ == '__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--job')
    parser.add_argument('--summarize', action='store_true')
    args=parser.parse_args()
    if args.job:
        worker(args.job)
    elif args.summarize:
        summarize()
    else:
        register()
        run()
