"""Freeze, resume, audit and package the paired eight-model formal benchmark.

Uses the existing thesis driver's isolated workers and immutable attempts.
Run through the project's configured Python runtime. See --help for commands.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
import csv
import hashlib
from importlib import metadata
import json
import os
from pathlib import Path
import platform
import random
import shutil
import socket
import statistics
import subprocess
import sys
import zipfile


def driver(out=None):
    if out is not None and (out/'sources/current').is_dir():
        sys.path.insert(0, str(out/'sources/current'))
    else:
        sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from experiments.runs import run_thesis_completion
    return run_thesis_completion


def jobs_for(records, exact_seconds, alns_seconds, seeds):
    if exact_seconds <= 0 or alns_seconds <= 0 or not seeds or len(set(seeds)) != len(seeds):
        raise ValueError('Require positive budgets and distinct ALNS seeds')
    jobs = []
    for record in records:
        for method, budget, method_seeds in (
                ('exact', exact_seconds, [42]), ('alns', alns_seconds, seeds)):
            for seed in method_seeds:
                jobs.append(dict(id=f'{record["base_case_id"]}_m{record["model"]}_{method}_s{seed}',
                    case=record['base_case_id'], model=record['model'], method=method,
                    version='current', seed=seed, budget=budget, iterations=10**9,
                    structure=record['size'], size=record['size'], split='formal',
                    family=record['family_id'], source_block=record['source_block'],
                    customer_count=record['customer_count'], case_path=record['case_path']))
    if len({j['id'] for j in jobs}) != len(jobs):
        raise ValueError('Duplicate model configurations')
    return jobs


def prepare(args):
    d = driver()
    from experiments.runs.run_model6_comparison import freeze_case
    from tools.audit.audit_formal_benchmark27 import audit_dataset
    from tools.audit.certify_formal_benchmark27 import verify_witnesses
    from tools.configuration.loader import load_routing_case
    from models.common.cycle import load_instance
    root, out = d.ROOT, Path(args.output).resolve()
    if out.exists() and any(out.iterdir()):
        raise ValueError('Refusing to overwrite existing study')
    if args.workers < 1 or args.timeout_overhead <= 0 or not args.overhead_basis:
        raise ValueError('Register positive concurrency/grace and smoke evidence')
    dataset = (root/args.dataset).resolve()
    input_audit = audit_dataset(dataset)
    witnesses = verify_witnesses(dataset)
    if not input_audit['valid'] or not witnesses['valid']:
        raise ValueError('Dataset or feasibility witness validation failed')
    suite = d.read(dataset/'manifest.json')
    records = [r for r in suite['cases'] if (not args.bases or r['base_case_id'] in args.bases)
               and r['model'] in args.models]
    if not records or (args.bases and set(args.bases) != {r['base_case_id'] for r in records}):
        raise ValueError('Empty or incomplete requested case selection')
    jobs = jobs_for(records, args.exact_seconds, args.alns_seconds, args.seeds)
    for name in ('inputs', 'cases', 'runs', 'sources/current', 'tables'):
        (out/name).mkdir(parents=True, exist_ok=True)
    source_hashes = {f'sources/current/{k}': v for k, v in d.snapshot(root, out/'sources/current').items()}
    shutil.copyfile(__file__, out/'driver.py')
    source_hashes['driver.py'] = d.sha(out/'driver.py')
    evidence, checks, configs = {}, [], {}
    for record in records:
        key = f'{record["base_case_id"]}_m{record["model"]}'
        cfg, original = freeze_case(record['case_path'], out, key)
        evidence.update(original)
        if record['model'] == 3 and getattr(args, 'm3_objective_mode', 'distance') == 'cost':
            cfg['objective_mode'] = 'cost'
            contract = d.read(cfg['parameter_contract'])
            contract['objective'] = 'cost'
            contract['notes'] = [note for note in contract.get('notes', [])
                                 if 'native distance objective of M1-M4' not in note]
            contract['notes'].append('M3 cost comparison uses the same economic profile as M5–M8; '
                                     'assignment, single-batch calendar and pool remain fixed.')
            contract_path = out/'inputs'/f'{key}_cost_parameters.json'
            d.write(contract_path, contract)
            cfg['parameter_contract'] = str(contract_path)
        if record['model'] == 5:
            # Old immutable datasets document a fixed-Q distance baseline.
            # Freeze a separate, explicit current M5 contract for this run.
            cfg.update(objective_mode='cost', pool_options=dict(pool_min=1, pool_max=record['total_container_uses']))
            cfg.pop('container_pool_size', None)
            contract = d.read(cfg['parameter_contract'])
            contract['objective'] = 'cost'
            contract['fixed_parameters'].pop('container_pool_size', None)
            if 'container_pool_size' not in contract['optimized_decisions']:
                contract['optimized_decisions'].append('container_pool_size')
            contract['decision_bounds'] = cfg['pool_options']
            contract_path = out/'inputs'/f'{key}_current_parameters.json'
            d.write(contract_path, contract)
            cfg['parameter_contract'] = str(contract_path)
        target = out/'cases'/f'{key}.json'
        d.write(target, cfg)
        problem = load_instance(*load_routing_case(target, use_pool_config=False))
        check = dict(case=record['base_case_id'], model=record['model'],
                     case_path=target.relative_to(out).as_posix(), Q=problem.total,
                     counts=list(getattr(problem, 'batch_count_candidates',
                                         [len(problem.operating_policy.batches)])),
                     time_tick_seconds=problem.operating_policy.time_tick_seconds)
        if getattr(problem, 'optimize_pool', False):
            check.update(objective_mode='cost', pool_min=problem.pool_min, pool_max=problem.pool_max)
        if getattr(problem, 'cost_objective', False):
            check.update(objective_mode='cost', economic_profile_hash=problem.cost_profile.hash)
        configs[record['case_path']] = check
        checks.append(check)
    for job in jobs:
        check = configs[job['case_path']]
        job.update(case_path=check['case_path'], counts=check['counts'])
        job['command'] = [sys.executable, str(out/'driver.py'), 'worker', '--output', str(out),
                          '--job', job['id'], '--attempt', '{attempt}']
    random.Random(20261015).shuffle(jobs)
    environment = dict(captured_at=d.now(), interpreter=sys.executable, python=sys.version,
        platform=platform.platform(), machine=platform.machine(), processor=platform.processor(),
        hostname=socket.gethostname(), logical_cpus=os.cpu_count(), workers=args.workers,
        exact_threads=1, numerical_library_threads=1,
        dependencies={name: metadata.version(name) for name in
            ('gurobipy', 'matplotlib', 'numpy', 'pandas', 'pyarrow', 'openpyxl', 'pytest')})
    try:
        import psutil
        environment.update(physical_cpus=psutil.cpu_count(logical=False),
                           physical_memory_bytes=psutil.virtual_memory().total)
    except ImportError:
        environment['physical_memory_bytes'] = None
    import gurobipy as gp
    with gp.Env(empty=True) as env:
        env.setParam('OutputFlag', 0)
        env.start()
        with gp.Model(env=env) as model:
            model.Params.Threads = 1
            x = model.addVar(vtype=gp.GRB.INTEGER, lb=0)
            model.addConstr(2*x >= 1)
            model.setObjective(x)
            model.optimize()
            if model.Status != gp.GRB.OPTIMAL or abs(model.ObjVal-1) > 1e-8:
                raise RuntimeError('Integer preflight failed')
            environment.update(gurobi_version=list(gp.gurobi.version()),
                integer_preflight=dict(status=int(model.Status), objective=model.ObjVal))
    d.write(out/'environment.json', environment)
    packages = subprocess.check_output([sys.executable, '-m', 'pip', 'freeze'], text=True, encoding='utf-8')
    (out/'dependencies.txt').write_text(packages, encoding='utf-8')
    d.write(out/'input_audit.json', input_audit)
    d.write(out/'witness_verification.json', witnesses)
    d.write(out/'dataset_manifest.json', suite)
    d.write(out/'comparison_protocol.json', d.read(dataset/'comparison_protocol.json'))
    d.write(out/'source_input_evidence.json', evidence)
    d.write(out/'input_checks.json', checks)
    inputs = {p.relative_to(out).as_posix(): d.sha(p) for folder in ('inputs', 'cases')
              for p in sorted((out/folder).iterdir()) if p.is_file()}
    for name in ('environment.json', 'dependencies.txt', 'input_audit.json', 'witness_verification.json',
                 'dataset_manifest.json', 'comparison_protocol.json', 'source_input_evidence.json', 'input_checks.json'):
        inputs[name] = d.sha(out/name)
    archive = out/'source_current.zip'
    with zipfile.ZipFile(archive, 'x', compression=zipfile.ZIP_DEFLATED) as zipped:
        for name in sorted(source_hashes):
            zipped.write(out/name, name)
    manifest = dict(schema_version=1, phase='formal_benchmark27', registered_at=d.now(),
        study_kind=args.study_kind, original_root=str(out), jobs=jobs, workers=args.workers,
        timeout_overhead=args.timeout_overhead, timeout_overhead_basis=args.overhead_basis,
        source_hashes=source_hashes, input_hashes=inputs,
        source_archive_hashes={archive.name: d.sha(archive)}, source_versions=['current'],
        commit=subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=root, text=True).strip(),
        uncommitted_source_status=subprocess.check_output(['git', 'status', '--porcelain', '--',
            'models', 'experiments', 'tools', 'cli'], cwd=root, text=True, encoding='utf-8'),
        dataset_sha256=d.sha(dataset/'manifest.json'), dataset_revision=suite['dataset_revision'],
        plan_count=len(jobs), total_call_budget=sum(j['budget'] for j in jobs),
        interpreter=sys.executable, ordering_seed=20261015, first_attempt_is_primary=True,
        alns_seeds=args.seeds, exact_seed=42, exact_seconds=args.exact_seconds, alns_seconds=args.alns_seconds,
        witness_initialization=False, source_block_count=len({r['source_block'] for r in records}),
        full_dataset=len(records) == 216,
        objective_by_model={str(m): (getattr(args, 'm3_objective_mode', 'distance') if m == 3
                                    else 'distance' if m <= 4 else 'cost') for m in range(1, 9)},
        limitations=d.read(dataset/'comparison_protocol.json')['scope_limits'])
    d.write(out/'manifest.json', manifest)
    d.write(out/'manifest.sha256.json', dict(sha256=d.sha(out/'manifest.json')))
    (out/'.gitignore').write_text('/sources/\n/inputs/\n/cases/\n/runs/\n/driver.lock\n/controller*.log\n/controller.local.ps1\n/delivery.json\n', encoding='utf-8')
    analyze(out)
    print(json.dumps(dict(output=str(out), planned=len(jobs), call_budget_hours=manifest['total_call_budget']/3600)))


def worker(args):
    out = Path(args.output).resolve()
    d = driver(out)
    # The original driver provides count observation, immutable attempt files,
    # objective checks and independent two-cycle replay for all numbered models.
    d.worker(args)
    path = out/'runs'/args.job/f'attempt_{args.attempt:03d}'
    extras = [name for name in d.read(path/'completion.json')['hashes'] if name != 'row.json']
    # Do not leave a complete marker if metric enrichment raises an exception.
    (path/'completion.json').unlink()
    row = d.read(path/'row.json')
    result = d.read(path/'result.json') if (path/'result.json').exists() else {}
    row['objective_mode'] = result.get('objective_mode', 'distance' if row['model'] <= 5 else 'cost')
    audit = d.read(path/'audit.json')['audit']
    if row['valid']:
        from tools.configuration.loader import load_routing_case
        from models.common.cycle import load_instance
        from models.cost.joint_batch import JointCostProfile
        cfg, fleet = load_routing_case(out/row['case_path'], use_pool_config=False)
        problem = load_instance(cfg, fleet)
        row.update(total_distance=sum(audit['distances'].values()),
                   e1_distance=audit['distances']['e1'], e2_distance=audit['distances']['e2'],
                   Q=problem.total, N=row['pool'] if problem.reusable else None,
                   e1_vehicles=audit['e1_vehicle_count'], e2_vehicles=audit['e2_vehicle_count'],
                   e1_trips=audit['e1_trip_count'], e2_trips=audit['e2_trip_count'])
        if problem.reusable:
            economic = d.read(cfg['economic_config']) if isinstance(cfg['economic_config'], str) else cfg['economic_config']
            cost = JointCostProfile(economic).evaluate(audit, row['pool'])
            row.update(comparable_reusable_cost=cost['total'], reusable_cost_components=cost['components'])
    # An infeasibility claim contradicts the independently verified input witness.
    if row.get('proven_infeasible'):
        row.update(status='WITNESS_CONTRADICTION', valid=False, proven_optimal=False)
    d.finalize_attempt(path, row, extras)


def run(args):
    """Keep the registered workers occupied without prequeueing after STOP."""
    out = Path(args.output).resolve()
    d = driver(out)
    manifest = d.read(out/'manifest.json')
    d.verify(out, manifest)
    with d.study_lock(out):
        pending = []
        for job in manifest['jobs']:
            directories = d.attempt_dirs(out, job)
            for path in directories:
                if not d.complete_attempt(path):
                    launch = d.read(path/'launch.json') if (path/'launch.json').exists() else {}
                    if launch.get('pid') and d.pid_alive(launch['pid']):
                        raise RuntimeError(f'Old worker may still be alive: {path}')
                    row = d.terminal_row(job, 'INTERRUPTED', 'Prior attempt has incomplete records')
                    row['attempt'] = int(path.name.split('_')[-1])
                    d.finalize_attempt(path, row)
            if not directories or (args.retry_failed and d.read(directories[-1]/'row.json')['status'] in
                                    ('ERROR','OUTER_TIMEOUT','INTERRUPTED')):
                pending.append(job)
        d.summarize(out, manifest)
        queue = iter(pending)
        with ThreadPoolExecutor(max_workers=manifest['workers']) as executor:
            active = set()
            def replenish():
                while len(active)<manifest['workers'] and not (out/'STOP').exists():
                    job = next(queue, None)
                    if job is None:
                        break
                    active.add(executor.submit(d.execute, out, manifest, job))
            replenish()
            while active:
                done, _ = wait(active, return_when=FIRST_COMPLETED)
                for future in done:
                    active.remove(future)
                    row = future.result()
                    with (out/'progress.jsonl').open('a', encoding='utf-8') as handle:
                        handle.write(json.dumps(dict(at=d.now(),job=row['id'],attempt=row['attempt'],
                            status=row['status'],valid=row['valid']))+'\n')
                    progress = d.summarize(out, manifest)
                    print(f'{progress["terminal"]}/{progress["planned"]} {row["id"]}: {row["status"]}', flush=True)
                replenish()
        print(json.dumps(analyze(out)))


def write_csv(path, rows):
    keys = sorted({key for row in rows for key in row})
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('w', newline='', encoding='utf-8-sig') as handle:
        writer = csv.DictWriter(handle, fieldnames=keys)
        writer.writeheader()
        writer.writerows({k: json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v
                         for k, v in row.items()} for row in rows)


def primary_rows(out, manifest, d):
    rows, attempts = [], []
    for job in manifest['jobs']:
        complete = [p for p in d.attempt_dirs(out, job) if d.complete_attempt(p)]
        attempts.extend(d.read(p/'row.json') for p in complete)
        rows.append(d.read(complete[0]/'row.json') if complete else d.terminal_row(job, 'NOT_RUN'))
    return rows, attempts


def paired_rows(rows):
    groups, pairs = defaultdict(dict), []
    for r in rows:
        groups[(r['case'], r['method'], r['seed'])][r['model']] = r
    for key, values in sorted(groups.items()):
        for left, right in ((1,2), (3,4), (1,3), (2,4), (3,5), (5,6), (6,7), (7,8), (6,8)):
            if left not in values or right not in values:
                continue
            a, b = values[left], values[right]
            def mode(row):
                return row.get('objective_mode', 'distance' if row['model'] <= 5 else 'cost')
            metric = ('objective' if left <= 5 and right <= 5 and mode(a) == mode(b)
                      else 'comparable_reusable_cost')
            both = bool(a['valid'] and b['valid'])
            av, bv = a.get(metric), b.get(metric)
            pairs.append(dict(case=key[0], method=key[1], seed=key[2], size=a['size'],
                source_block=a['source_block'], left=left, right=right, metric=metric,
                both_valid=both, left_status=a['status'], right_status=b['status'],
                left_value=av, right_value=bv,
                difference_right_minus_left=bv-av if both and av is not None and bv is not None else None,
                interpretation=('M5/M6 uses the same cost objective; changes order batches and calendar freedom'
                                if (left,right)==(5,6) and mode(a) == 'cost'
                                else 'M5/M6 changes both objective and batch rules') if (left,right)==(5,6)
                               else 'paired finite-budget schedules; no universal dominance claim'))
    return pairs


def analyze(out):
    out = Path(out).resolve()
    d = driver(out)
    manifest = d.read(out/'manifest.json')
    d.verify(out, manifest)
    progress = d.summarize(out, manifest)
    rows, attempts = primary_rows(out, manifest, d)
    grouped, aggregates = defaultdict(list), []
    for row in rows:
        grouped[(row['size'], row['model'], row['method'])].append(row)
    for (size, model, method), group in sorted(grouped.items()):
        valid = [r for r in group if r['valid']]
        item = dict(size=size, model=model, method=method, planned=len(group),
                    valid=len(valid), proven_optimal=sum(r['proven_optimal'] for r in group),
                    native_metric=next((r['objective_mode'] for r in group if 'objective_mode' in r),
                        manifest.get('objective_by_model', {}).get(str(model),
                            'distance' if model <= 5 else 'cost')),
                    statuses=dict(Counter(r['status'] for r in group)))
        for metric in ('objective', 'total_distance', 'comparable_reusable_cost', 'N', 'k', 'solve_wall_seconds'):
            numbers = [r[metric] for r in valid if isinstance(r.get(metric), (float, int))]
            item[metric+'_mean'] = statistics.mean(numbers) if numbers else None
        aggregates.append(item)
    per_case = []
    for key in sorted({(r['case'], r['model'], r['method']) for r in rows}):
        group = [r for r in rows if (r['case'], r['model'], r['method']) == key]
        values = [r['objective'] for r in group if r['valid']]
        per_case.append(dict(case=key[0], model=key[1], method=key[2], size=group[0]['size'],
            source_block=group[0]['source_block'], planned=len(group), valid=len(values),
            best=min(values) if values else None, mean=statistics.mean(values) if values else None,
            worst=max(values) if values else None, stdev=statistics.stdev(values) if len(values)>1 else None))
    block_groups = defaultdict(list)
    for r in per_case:
        if r['mean'] is not None:
            block_groups[(r['size'], r['model'], r['method'], r['source_block'])].append(r['mean'])
    block_rows = [dict(size=k[0], model=k[1], method=k[2], source_block=k[3],
                       valid_cases=len(v), native_objective_mean=statistics.mean(v))
                  for k,v in sorted(block_groups.items())]
    block_balanced = defaultdict(list)
    for r in block_rows:
        block_balanced[(r['size'],r['model'],r['method'])].append(r['native_objective_mean'])
    balanced_rows = [dict(size=k[0],model=k[1],method=k[2],valid_source_blocks=len(v),
                         native_objective_mean=statistics.mean(v)) for k,v in sorted(block_balanced.items())]
    refs = {(r['case'],r['model']):r for r in rows if r['method']=='exact'}
    gaps, contradictions = [], []
    for r in rows:
        if r['method']!='alns':
            continue
        ref = refs.get((r['case'],r['model']), {})
        usable = r['valid'] and ref.get('valid')
        optimum = usable and ref.get('proven_optimal')
        difference = r['objective']-ref['objective'] if usable else None
        if optimum and difference < -1e-5*max(1,abs(ref['objective'])):
            contradictions.append(r['id'])
        gaps.append(dict(id=r['id'],case=r['case'],model=r['model'],seed=r['seed'],
            size=r['size'],source_block=r['source_block'],valid_pair=bool(usable),
            reference_proven_optimal=bool(optimum),reference=ref.get('objective'),
            alns_objective=r['objective'],difference=difference,
            optimal_gap_percent=100*difference/ref['objective'] if optimum and ref['objective'] else None,
            feasible_reference_difference_percent=100*difference/ref['objective'] if usable and ref['objective'] else None))
    errors = [r['id'] for r in attempts if r['status'] in (
        'ERROR','OUTER_TIMEOUT','INTERRUPTED','AUDIT_FAILED','DIAGNOSTIC_FAILURE','WITNESS_CONTRADICTION')]
    errors += [r['id'] for r in attempts if r.get('any_audit_failure') or r.get('certificate_errors')]
    gate = dict(complete=progress['state']=='COMPLETED', ready=progress['state']=='COMPLETED' and not errors and not contradictions,
                execution_or_audit_errors=sorted(set(errors)), optimal_reference_contradictions=contradictions,
                first_attempt_denominator=len(rows), updated_at=d.now())
    for name, values in [('runs',rows), ('all_attempts',attempts), ('by_model_size_method',aggregates),
                         ('per_case',per_case), ('source_blocks',block_rows),('block_balanced',balanced_rows),
                         ('paired_models',paired_rows(rows)), ('exact_reference_gaps',gaps)]:
        write_csv(out/'tables'/f'{name}.csv', values)
    d.write(out/'stage_gate.json', gate)
    d.write(out/'analysis.json', dict(progress=progress, gate=gate, groups=aggregates,
        first_attempts_only=True, source_block_count=manifest['source_block_count']))
    lines = ['# 正式27例八模型实验', '',
        f'状态：{progress["state"]}；{progress["terminal"]}/{len(rows)} 次有终态，'
        f'{progress["audited_feasible"]} 次独立双周期审计可行，{progress["optimal"]} 次已证最优。', '',
        f'数据修订 {manifest["dataset_revision"]}；清单 SHA-256：`{manifest["dataset_sha256"]}`。',
        f'源码提交 `{manifest["commit"]}`；实际执行字节见 source_current.zip 和 manifest.json。', '',
        f'精确每例 {manifest["exact_seconds"]:g} 秒、种子42；ALNS每种子 {manifest["alns_seconds"]:g} 秒，'
        f'种子 {manifest["alns_seeds"]}，迭代上限10^9；并发 {manifest["workers"]}、精确单线程。',
        f'登记调用预算 {manifest["total_call_budget"]/3600:g} 小时；外层宽限 {manifest["timeout_overhead"]:g} 秒，'
        '不扩展内部搜索预算。所有计划首次尝试均保留在分母，失败重试另列。', '',
        '| 规模 | 模型 | 方法 | 可行/计划 | 最优 | 原生目标均值（仅可行） |',
        '| --- | ---: | --- | ---: | ---: | ---: |']
    for r in aggregates:
        value = f'{r["objective_mean"]:.4f}' if r['objective_mean'] is not None else '—'
        lines.append(f'| {r["size"]} | M{r["model"]} | {r["method"]} | {r["valid"]}/{r["planned"]} | {r["proven_optimal"]} | {value} |')
    lines += ['', '逐种子、逐例均值/最好/最差/标准差、同源块均值及块平衡均值、模型配对、精确参照误差见 tables/。',
        '均值仅对可行结果计算；可行率以全部计划调用为分母。各规模分别报告，种子不计为独立算例。',
        ('M1–M4原生目标为距离，M5–M8为合成成本。M3–M8另按共同费用评价，M1/M2无一次性包装单价，不报告可比总费用。'
         if manifest.get('objective_by_model', {}).get('5') == 'cost' else
         '历史实验：M1–M5原生目标为距离，M6–M8为合成成本。M3–M8另按共同费用评价。'),
        ('M5/M6使用相同成本目标，比较固定分批日历与优化分批日历；允许域未必嵌套，不声明普遍支配。'
         if manifest.get('objective_by_model', {}).get('5') == 'cost' else
         '历史M5/M6目标和批次作业规则均不同，其差值不能单独归因为增加决策自由度。'),
        '仅精确已证最优参照计算最优误差；限时可行参照只报告费用差。未找到解不等于不可行。',
        '27例来自14个来源块，中大规模含嵌套；不作独立同分布总体推断。可行性见证未作为算法初始解。',
        '完整输入、环境、命令、K域、时间精度、上下界和原始日志随实验保存。', '',
        f'验收状态：{"通过" if gate["ready"] else "待完成或待复核"}。',
        f'执行/审计异常：{len(gate["execution_or_audit_errors"])}；最优参照矛盾：{len(contradictions)}。']
    (out/'report.md').write_text('\n'.join(lines)+'\n', encoding='utf-8')
    return gate


def pack(out):
    out = Path(out).resolve()
    d = driver(out)
    gate = analyze(out)
    if not gate['complete']:
        raise ValueError('Refusing to package an incomplete study')
    files = {p.relative_to(out).as_posix():d.sha(p) for folder in ('inputs','cases','runs')
             for p in sorted((out/folder).rglob('*')) if p.is_file() and p.suffix!='.tmp'}
    archive = out/'raw_artifacts.zip'
    if not archive.exists():
        with zipfile.ZipFile(archive, 'x', compression=zipfile.ZIP_DEFLATED) as zipped:
            for name in files:
                zipped.write(out/name, name)
    with zipfile.ZipFile(archive) as zipped:
        if set(zipped.namelist()) != set(files):
            raise ValueError('Archive inventory differs')
        for name, expected in files.items():
            if hashlib.sha256(zipped.read(name)).hexdigest()!=expected:
                raise ValueError(f'Archive mismatch: {name}')
    parts = []
    if archive.stat().st_size > 64*1024*1024:
        with archive.open('rb') as handle:
            while payload := handle.read(64*1024*1024):
                part = out/f'raw_artifacts.zip.part{len(parts)+1:03d}'
                if part.exists() and part.read_bytes()!=payload:
                    raise ValueError(f'Refusing to overwrite changed archive part: {part}')
                part.write_bytes(payload)
                parts.append(dict(path=part.name,bytes=len(payload),sha256=d.sha(part)))
        ignore = out/'.gitignore'
        text = ignore.read_text(encoding='utf-8')
        if '/raw_artifacts.zip\n' not in text:
            ignore.write_text(text+'/raw_artifacts.zip\n',encoding='utf-8')
    d.write(out/'packed_artifacts.json', dict(archive_sha256=d.sha(archive), files=files, parts=parts,
        restore='If parts are listed, concatenate them in listed order and verify archive_sha256. Verify file hashes, then extract raw_artifacts.zip and source_current.zip into this directory; frozen case paths retain the original execution location.'))
    print(json.dumps(dict(packed_files=len(files), archive_bytes=archive.stat().st_size, gate=gate)))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['prepare','run','worker','status','analyze','pack'])
    parser.add_argument('--output', required=True)
    parser.add_argument('--dataset', default='data/formal_benchmark27_uniform_7_20_50_v1')
    parser.add_argument('--models', nargs='+', type=int, choices=range(1,9), default=list(range(1,9)))
    parser.add_argument('--m3-objective-mode', choices=('distance', 'cost'), default='cost',
                        help='Use cost for matched M3/M5–M8 optimization; distance preserves packaging baselines')
    parser.add_argument('--bases', nargs='+')
    parser.add_argument('--exact-seconds', type=float, default=300)
    parser.add_argument('--alns-seconds', type=float, default=30)
    parser.add_argument('--seeds', nargs='+', type=int, default=[42])
    parser.add_argument('--workers', type=int, default=2)
    parser.add_argument('--timeout-overhead', type=float, default=60)
    parser.add_argument('--overhead-basis', help='Measured pilot evidence; required for preparation')
    parser.add_argument('--study-kind', choices=['smoke','formal'], default='formal')
    parser.add_argument('--job')
    parser.add_argument('--attempt', type=int)
    parser.add_argument('--retry-failed', action='store_true')
    args = parser.parse_args()
    out = Path(args.output).resolve()
    if args.action=='prepare':
        prepare(args)
    elif args.action=='worker':
        worker(args)
    elif args.action=='run':
        run(args)
    elif args.action=='pack':
        pack(out)
    elif args.action=='analyze':
        print(json.dumps(analyze(out)))
    else:
        d = driver(out)
        print(json.dumps(d.read(out/'progress.json'), indent=2))


if __name__=='__main__':
    main()
