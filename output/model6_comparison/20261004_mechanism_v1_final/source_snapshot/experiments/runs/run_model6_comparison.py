"""Frozen M5/M6 mechanism comparison, with matched-cost ablations and seed runs.

Run: python -m experiments.runs.run_model6_comparison --manifest data/model6/mechanism_v1/manifest.json
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import csv
from datetime import datetime
import hashlib
import json
import math
from pathlib import Path
import shutil
import statistics
import subprocess
import sys
import time

from configuration.loader import PROJECT_ROOT, load_routing_case, resolve_project_path
from models.routing.common.cycle import load_instance
from models.routing.common.cycle.audit import audit_solution


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)+'\n', encoding='utf-8')


def freeze_case(source, out, prefix):
    """Copy inputs and rewrite their references before any solver starts."""
    config, _ = load_routing_case(source, use_pool_config=False)
    config = deepcopy(config)
    hashes = {}
    for key in ('data', 'orders', 'routing_config', 'operating_policy', 'algorithm_config',
                'distance_matrix', 'duration_matrix', 'economic_config'):
        value = config.get(key)
        if not isinstance(value, str):
            continue
        original = resolve_project_path(value).resolve()
        payload = original.read_bytes()
        target = out/'inputs'/f'{prefix}_{key}_{original.name}'
        target.write_bytes(payload)
        hashes[str(original)] = dict(sha256=hashlib.sha256(payload).hexdigest(), snapshot=str(target.relative_to(out)))
        config[key] = str(target)
    economics = config.get('economic_config')
    if economics:
        profile = json.loads(Path(economics).read_text()) if isinstance(economics, str) else deepcopy(economics)
        if profile.get('evidence_file'):
            original = resolve_project_path(profile['evidence_file']).resolve()
            target = out/'inputs'/f'{prefix}_evidence_{original.name}'
            payload = original.read_bytes(); target.write_bytes(payload)
            hashes[str(original)] = dict(sha256=hashlib.sha256(payload).hexdigest(), snapshot=str(target.relative_to(out)))
            profile['evidence_file'] = str(target)
            if isinstance(economics, str):
                write_json(Path(economics), profile)
            else:
                config['economic_config'] = profile
    source_path = resolve_project_path(source)
    hashes[str(source_path)] = dict(sha256=hashlib.sha256(source_path.read_bytes()).hexdigest(),
                                    snapshot=f'inputs/{prefix}_source_case.json')
    shutil.copy2(source_path, out/'inputs'/f'{prefix}_source_case.json')
    return config, hashes


def snapshot_source(out):
    hashes = {}
    for directory in ('models', 'configuration', 'cli', 'reporting', 'experiments', 'tools'):
        for original in sorted((PROJECT_ROOT/directory).rglob('*.py')):
            relative = original.relative_to(PROJECT_ROOT)
            target = out/'source_snapshot'/relative
            target.parent.mkdir(parents=True, exist_ok=True)
            payload = original.read_bytes(); target.write_bytes(payload)
            hashes[str(relative)] = hashlib.sha256(payload).hexdigest()
    shutil.copy2(PROJECT_ROOT/'main.py', out/'source_snapshot/main.py')
    hashes['main.py'] = hashlib.sha256((out/'source_snapshot/main.py').read_bytes()).hexdigest()
    return hashes


def run_job(job, out, overhead):
    target = out/job['run']
    command = [sys.executable, str(out/'source_snapshot/main.py'), 'route',
               '--case', job['case_path'], '--solver', job['solver'], '--seed', str(job['seed']),
               '--output', str(target), '--exact-time' if job['solver']=='exact' else '--time', str(job['budget'])]
    begin = time.perf_counter()
    row = {key:value for key,value in job.items() if key!='case_path'}
    row.update(valid=False, status='ERROR', proven_optimal=False, cost=None, distance=None,
               pool=None, batches=None, e1_trips=None, e2_trips=None, best_bound=None, mip_gap=None,
               economic_profile_hash=None, runtime=None, wall_time=None, error='')
    try:
        process = subprocess.run(command, cwd=PROJECT_ROOT, capture_output=True, text=True,
                                 timeout=job['budget']+overhead)
        (out/'logs'/f'{job["run"]}.log').write_text(process.stdout+'\n'+process.stderr, encoding='utf-8')
        if process.returncode:
            raise ValueError(f'Worker exited {process.returncode}; see logs/{job["run"]}.log')
        config, fleet = load_routing_case(job['case_path'], use_pool_config=False)
        problem = load_instance(config, fleet)
        result = json.loads((target/'result.json').read_text(encoding='utf-8'))
        audit = audit_solution(problem, result)
        write_json(target/'independent_audit.json', audit)
        info = result['info']
        row.update(status=info['status'], valid=audit['valid'], proven_optimal=bool(info.get('proven_optimal')) and audit['valid'],
                   best_bound=info.get('best_bound'), mip_gap=info.get('mip_gap'), runtime=info.get('runtime'))
        if not audit['valid']:
            row['error'] = json.dumps(audit['errors'], ensure_ascii=False)
        else:
            from models.cost.joint_batch import JointCostProfile
            economic = config['economic_config']
            profile = JointCostProfile(json.loads(Path(economic).read_text()) if isinstance(economic, str) else economic)
            pool = result['container_pool_size'] if problem.model_number==6 else problem.operating_policy.container_pool_size
            cost = audit['cost'] if problem.model_number==6 else profile.evaluate(audit, pool)
            write_json(target/'comparison_cost.json', cost)
            row.update(cost=cost['total'], economic_profile_hash=profile.hash, pool=pool,
                       distance=sum(audit['distances'].values()),
                       batches=result['delivery_batch_count'] if problem.model_number==6 else sum(bool(v) for v in problem.batch_demands.values()),
                       e1_trips=audit['e1_trip_count'], e2_trips=audit['e2_trip_count'])
    except subprocess.TimeoutExpired as exc:
        row.update(status='OUTER_TIMEOUT', error=f'Worker exceeded {job["budget"]+overhead:g}s')
        def decode(value):
            return value.decode(errors='replace') if isinstance(value, bytes) else value or ''
        (out/'logs'/f'{job["run"]}.log').write_text(decode(exc.stdout)+'\n'+decode(exc.stderr), encoding='utf-8')
    except (OSError, ValueError, KeyError, TypeError) as exc:
        row.update(status='ERROR', valid=False, proven_optimal=False, error=f'{type(exc).__name__}: {exc}')
    row['wall_time'] = time.perf_counter()-begin
    return row


def render_report(rows, cases, args, out):
    lines = ['# 模型6与模型5：静态联合分批对比', '',
             '本次运行全部采用 synthetic_units 合成成本单位，用于检查算法与机制；不代表真实人民币成本或企业节约。', '',
             f'精确求解每次 {args.exact_time:g} 秒；ALNS 每次 {args.time:g} 秒、最多 {args.iterations} 次迭代，种子 {args.seeds}。所有结果均独立审计，包括连续两周期的容器与车辆可复用性。', '',
             '模型5固定原分批和全需求容器池，以距离为目标。其成本是对距离目标所得调度的重算，可能受同距离解的等待和路径选择影响。M6固定方案用同一成本目标优化原分批与原容器池，作为判断联合决策收益的更严格基准。', '',
             '| 算例 | M5精确解重算成本 | M6固定方案成本 | M6联合精确成本 | M6 ALNS 最好/均值/最差 | 联合解容器/配送批次/一级趟/二级趟 |',
             '|---|---:|---:|---:|---:|---:|']
    def number(value):
        return f'{value:.4f}' if isinstance(value,(float,int)) else '—'
    for case in cases:
        group = [r for r in rows if r['case']==case['name']]
        exact = {r['treatment']:r for r in group if r['solver']=='exact'}
        baseline = exact['M5_distance']; fixed = exact['M6_fixed']; joint = exact['M6_joint']
        heuristic = [r['cost'] for r in group if r['solver']=='alns' and r['treatment']=='M6_joint' and r['valid']]
        stats = '/'.join(number(v) for v in (min(heuristic),statistics.mean(heuristic),max(heuristic))) if heuristic else '—'
        counts = '/'.join(str(joint[k]) for k in ('pool','batches','e1_trips','e2_trips')) if joint['valid'] else '—'
        lines.append(f'| {case["name"]} | {number(baseline["cost"])} | {number(fixed["cost"])} | {number(joint["cost"])} | {stats} | {counts} |')
    lines += ['', '## 分解对比与证明范围', '',
              '| 算例 | 处理 | 方法/种子 | 成本 | 距离 | 容器池 | 配送批次 | 一级/二级趟数 | 状态 | 最优证明 | 间隙 |',
              '|---|---|---|---:|---:|---:|---:|---|---|---|---:|']
    for row in rows:
        lines.append(f'| {row["case"]} | {row["treatment"]} | {row["solver"]}/{row["seed"]} | {number(row["cost"])} | {number(row["distance"])} | {row["pool"]} | {row["batches"]} | {row["e1_trips"]}/{row["e2_trips"]} | {row["status"]} | {row["proven_optimal"]} | {number(row.get("joint_optimal_gap_percent"))} |')
    lines += ['', 'M6_fixed：原分批与原容器池均固定；M6_pool_only：只放开容器池；M6_batch_only：只放开分批；M6_joint：同时放开分批和容器池。各组均使用成本目标并联合优化两级路径。不同放开顺序的收益不应相加。', '',
              '表中的间隙单位为百分比，仅对 M6_joint ALNS 相对于已证明最优的 M6_joint 精确值计算；启发式结果不声称全局最优。M5 的最优证明仅针对原固定问题的距离目标。', '',
              '固定候选时段与回收截止始终保留：关闭配送批次不会取消该时段的回收义务。清洗和追踪按全部订单使用量计费，本组订单不可拆分且需求固定，因此这些费用为常数。容器使用磨损与池规模占用费分开；初始投资另列且不重复计入周期成本。', '',
              '合成算例的距离换算为每坐标单位1公里、时间刻度1分钟，等待计入占用工时，趟间闲置薪酬不计。车辆按唯一物理车辆计费，趟次按实际路线计费。候选时段、释放时刻、允许批次和所有价格均在 inputs 快照中。', '',
              '复现命令：', '',
              f'    .venv/bin/python -m experiments.runs.run_model6_comparison --manifest {args.manifest} --exact-time {args.exact_time:g} --time {args.time:g} --iterations {args.iterations} --seeds '+ ' '.join(map(str,args.seeds)), '',
              '逐次运行的原始路线、独立审计、成本分项在各运行目录；rows.csv 保留失败及超时；experiment.json 包含预算、完整输入和源码哈希。', '']
    (out/'report.md').write_text('\n'.join(lines), encoding='utf-8')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', default='data/model6/mechanism_v1/manifest.json')
    parser.add_argument('--exact-time', type=float, default=20)
    parser.add_argument('--time', type=float, default=1)
    parser.add_argument('--iterations', type=int, default=1000)
    parser.add_argument('--seeds', nargs='+', type=int, default=[42,43,44])
    parser.add_argument('--timeout-overhead', type=float, default=15)
    parser.add_argument('--output')
    args = parser.parse_args(argv)
    if any(not math.isfinite(v) or v<=0 for v in (args.time,args.exact_time,args.timeout_overhead)):
        parser.error('Budgets must be finite and positive')
    if args.iterations<1 or any(not 0<=s<=2_000_000_000 for s in args.seeds):
        parser.error('iterations must be positive and seeds between 0 and 2000000000')
    args.seeds = list(dict.fromkeys(args.seeds))
    manifest_path = resolve_project_path(args.manifest)
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    cases = manifest['cases']
    out = resolve_project_path(args.output).resolve() if args.output else PROJECT_ROOT/'output/model6_comparison'/datetime.now().strftime('%Y%m%dT%H%M%S_%f')
    if out.exists() and any(out.iterdir()):
        raise ValueError(f'Refusing to overwrite nonempty output directory: {out}')
    for folder in ('inputs','cases','logs'):
        (out/folder).mkdir(parents=True, exist_ok=True)
    shutil.copy2(manifest_path, out/'inputs/manifest.json')
    input_hashes = {}; jobs = []
    for index, record in enumerate(cases):
        c5,h5 = freeze_case(record['m5_case'],out,f'{index}_M5'); input_hashes.update(h5)
        c6,h6 = freeze_case(record['m6_case'],out,f'{index}_M6'); input_hashes.update(h6)
        p5 = load_instance(*load_routing_case(record['m5_case'],use_pool_config=False))
        p6 = load_instance(*load_routing_case(record['m6_case'],use_pool_config=False))
        def same_input(key):
            first, second = c5.get(key), c6.get(key)
            if isinstance(first,str) and isinstance(second,str):
                return Path(first).read_bytes() == Path(second).read_bytes()
            return first == second
        if (p5.orders!=p6.orders or p5.operating_policy!=p6.operating_policy
                or not all(same_input(key) for key in ('data','routing_config','distance_matrix','duration_matrix','economic_config'))):
            raise ValueError('Comparison requires identical orders, networks and calendars')
        original = {o.id:o.batch for o in p5.orders}
        p6.project(original)  # Original M5 assignment must also obey M6 release/allowed constraints.
        pool = p5.operating_policy.container_pool_size
        variants = {'M5_distance':c5, 'M6_joint':c6}
        for label,fix_batches,fix_pool in [('M6_fixed',True,True),('M6_pool_only',True,False),('M6_batch_only',False,True)]:
            config = deepcopy(c6); options = deepcopy(c6.get('joint_batch_options',{}))
            if fix_batches:
                options['fixed_assignment'] = original
            if fix_pool:
                options.update(pool_min=pool,pool_max=pool)
            config['joint_batch_options'] = options
            variants[label] = config
        for label in ('M5_distance','M6_fixed','M6_pool_only','M6_batch_only','M6_joint'):
            config = deepcopy(variants[label]); config.update(save_plots=False,alns_iter=args.iterations)
            case_path = out/'cases'/f'{index}_{label}.json'; write_json(case_path,config)
            methods = [('exact',args.seeds[0],args.exact_time)]
            if label in ('M5_distance','M6_joint'):
                methods += [('alns',seed,args.time) for seed in args.seeds]
            for solver,seed,budget in methods:
                jobs.append(dict(case=record['name'],treatment=label,solver=solver,seed=seed,budget=budget,
                                 run=f'{index:02d}_{label}_{solver}_{seed}',case_path=str(case_path)))
    source_hashes = snapshot_source(out)
    plan = dict(schema_version=1,source_manifest=str(manifest_path),budgets=vars(args),input_hashes=input_hashes,
                source_hashes=source_hashes,jobs=jobs,cost_warning=manifest.get('description'))
    write_json(out/'experiment.json',plan)
    rows = []
    for index,job in enumerate(jobs):
        row = run_job(job,out,args.timeout_overhead); rows.append(row)
        write_json(out/'rows.json',rows)
        print(f'{index+1}/{len(jobs)} {row["case"]} {row["treatment"]} {row["solver"]}/{row["seed"]}: {row["status"]}, cost={row["cost"]}, valid={row["valid"]}',flush=True)
    for row in rows:
        row['joint_optimal_gap_percent'] = None
        optimum = next(r for r in rows if r['case']==row['case'] and r['treatment']=='M6_joint' and r['solver']=='exact')
        if row['treatment']=='M6_joint' and row['solver']=='alns' and row['valid'] and optimum['proven_optimal']:
            row['joint_optimal_gap_percent'] = 100*(row['cost']-optimum['cost'])/max(1e-9,abs(optimum['cost']))
            if row['joint_optimal_gap_percent'] < -1e-6:
                raise RuntimeError('ALNS result beats a claimed exact optimum')
    write_json(out/'rows.json',rows)
    with (out/'rows.csv').open('w',newline='',encoding='utf-8') as handle:
        writer=csv.DictWriter(handle,fieldnames=list(rows[0])); writer.writeheader(); writer.writerows(rows)
    render_report(rows,cases,args,out)
    print(f'Comparison saved: {out}',flush=True)
    return out


if __name__=='__main__':
    main()
