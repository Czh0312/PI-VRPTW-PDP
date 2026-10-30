"""Frozen, serial M3/M8 cost and M1/M3/M4 distance study with durable stages."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from copy import deepcopy
import csv
from importlib import import_module
import json
import math
import os
from pathlib import Path
import platform
import shutil
import sys
import time
import zipfile

HERE = Path(__file__).resolve().parent
ROOT = HERE/'sources/current' if (HERE/'sources/current').is_dir() else HERE.parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from experiments.runs import run_thesis_completion as base

DATASET = 'data/formal_benchmark27_uniform_7_20_50_v1'
STAGES = ('A_small_cost_300s', 'B_medium_large_cost', 'C_medium_large_distance')
ERRORS = {'ERROR','OUTER_TIMEOUT','INTERRUPTED','AUDIT_FAILED','DIAGNOSTIC_FAILURE'}
COUNTS = {'small':7,'medium':20,'large':50}


def make_jobs(records, *, seed=42):
    jobs = []

    def add(record, stage, model, method, objective, budget):
        name = record['id']
        jobs.append(dict(id=f'{stage[0]}_{name}_m{model}_{objective}_{method}_{budget}s_s{seed}',
            stage=stage, case=name, size=record['size'], customer_count=record['customer_count'],
            model=model, method=method, objective_mode=objective, seed=seed, budget=budget,
            iterations=10**9, version='current',
            variant=f'{name}_m{model}_{objective}', source_case=record['models'][str(model)]))

    for r in records:
        if r['size']=='small':
            add(r,STAGES[0],3,'exact','cost',300)
            add(r,STAGES[0],8,'exact','cost',300)
            add(r,STAGES[0],8,'alns','cost',300)
    for size in ('medium','large'):
        for r in records:
            if r['size']==size:
                for model in (3,8):
                    add(r,STAGES[1],model,'alns','cost',600 if size=='medium' else 1200)
    for size in ('medium','large'):
        for r in records:
            if r['size']==size:
                for model in (1,3,4):
                    add(r,STAGES[2],model,'alns','distance',1200 if size=='medium' else 1800)
    return jobs


def prepare(args):
    from tools.audit.audit_uniform_benchmark27 import audit_uniform
    from experiments.runs.run_model6_comparison import freeze_case
    from tools.configuration.loader import load_routing_case
    from models.common.cycle import load_instance
    from models.cost.joint_batch import JointCostProfile
    dataset = (ROOT/args.dataset).resolve()
    out = Path(args.output).resolve()
    if out.exists() and any(out.iterdir()):
        raise ValueError(f'Refusing to overwrite nonempty study: {out}')
    audited = audit_uniform(dataset)
    data = base.read(dataset/'manifest.json')
    records = data['base_cases']
    if Counter(r['size'] for r in records) != dict(small=9,medium=9,large=9):
        raise ValueError('Expected all nine cases at each size')
    for folder in ('inputs','cases','runs','tables','sources/current'):
        (out/folder).mkdir(parents=True,exist_ok=True)
    jobs = make_jobs(records)
    base.write(out/'input_audit.json',audited)
    shutil.copy2(dataset/'manifest.json',out/'inputs/dataset_manifest.json')
    input_hashes = {'input_audit.json':base.sha(out/'input_audit.json'),
                    'inputs/dataset_manifest.json':base.sha(out/'inputs/dataset_manifest.json')}
    cases, original_hashes = {}, {}
    for job in jobs:
        variant = job['variant']
        if variant not in cases:
            cfg, hashes = freeze_case(job['source_case'],out,variant)
            # The parameter description must be frozen too: freeze_case covers
            # runtime input keys, whereas this file describes the decision scope.
            original_contract = ROOT/cfg['parameter_contract']
            contract = base.read(original_contract)
            original_hashes[str(original_contract)] = base.sha(original_contract)
            shutil.copy2(original_contract,out/f'inputs/{variant}_parameters_original.json')
            contract['objective'] = job['objective_mode']
            if job['model']==8:
                cfg['joint_batch_options'].update(batch_count_candidates=[1,2,3,4],
                                                  calendar_decision_grid_seconds=60)
                contract.setdefault('decision_bounds',{}).update(
                    batch_count_candidates=[1,2,3,4],calendar_decision_grid_seconds=60)
            if job['model']==3:
                cfg['objective_mode'] = job['objective_mode']
                if job['objective_mode']=='distance':
                    contract['notes'] = [n for n in contract.get('notes',[]) if not n.startswith('M3 directly optimizes')]
                    contract['notes'].append('This distance-stage variant minimizes distance; economic cost is evaluated only after solving.')
            if isinstance(cfg.get('batch_count_certificate'),str):
                original_certificate = ROOT/cfg['batch_count_certificate']
                dest = out/f'inputs/{variant}_native_count_certificate.json'
                shutil.copy2(original_certificate,dest)
                original_hashes[str(original_certificate)] = base.sha(original_certificate)
                cfg['batch_count_certificate'] = str(dest)
            cfg.update(seed=42,alns_iter=10**9,save_plots=False)
            base.write(out/f'inputs/{variant}_parameters.json',contract)
            cfg['parameter_contract'] = str(out/f'inputs/{variant}_parameters.json')
            case_path = out/f'cases/{variant}.json'
            base.write(case_path,cfg)
            frozen_cfg,fleet = load_routing_case(case_path,use_pool_config=False)
            p = load_instance(frozen_cfg,fleet)
            if len(p.orders)!=COUNTS[job['size']] or p.total!=COUNTS[job['size']]:
                raise ValueError(f'Wrong physical size: {variant}')
            if job['model']==3 and bool(getattr(p,'cost_objective',False)) != (job['objective_mode']=='cost'):
                raise ValueError(f'M3 objective switch failed: {variant}')
            if job['model']==8:
                for count in (1,2,3,4):
                    # Verify each allowed count can construct its grid domain.
                    fixed = p.fixed_calendar(count)
                    fixed.validated_cutoffs(fixed.default_cutoffs)
                    p.for_count(count)
            profile = JointCostProfile(base.read(cfg['economic_config']))
            cases[variant] = dict(case_path=str(case_path.relative_to(out)),
                economic_profile_hash=profile.hash,
                configured_pool=p.operating_policy.container_pool_size,
                calendar_contract=p.calendar_contract if job['model']==8 else None)
            original_hashes.update({name:v['sha256'] for name,v in hashes.items()})
        job.update(cases[variant])
    # Cost objectives use byte-identical economic profiles within each case.
    for r in records:
        name = r['id']
        if cases[f'{name}_m3_cost']['economic_profile_hash'] != cases[f'{name}_m8_cost']['economic_profile_hash']:
            raise ValueError(f'Mismatched economic profile: {name}')
    source_hashes = base.snapshot(ROOT,out/'sources/current')
    shutil.copy2(Path(__file__),out/'driver.py')
    source_hashes = {f'sources/current/{name}':digest for name,digest in source_hashes.items()}
    source_hashes['driver.py'] = base.sha(out/'driver.py')
    with zipfile.ZipFile(out/'source_current.zip','x',compression=zipfile.ZIP_DEFLATED) as archive:
        for name in source_hashes:
            archive.write(out/name,name)
    source_hashes['source_current.zip'] = base.sha(out/'source_current.zip')
    for folder in ('inputs','cases'):
        input_hashes.update({p.relative_to(out).as_posix():base.sha(p)
                             for p in sorted((out/folder).rglob('*')) if p.is_file()})
    manifest = dict(schema_version=1,created_at=base.now(),dataset=str(dataset),
        dataset_sha256=base.sha(dataset/'manifest.json'),customer_count_by_size=COUNTS,
        cases_per_size=9,seed=42,workers=1,timeout_overhead=180,
        stages=list(STAGES),jobs=jobs,cases=cases,source_hashes=source_hashes,
        input_hashes=input_hashes,original_input_hashes=original_hashes,
        total_call_budget_seconds=sum(j['budget'] for j in jobs),
        exact_budget_scope='public_solver_call_precheck_build_optimize_extract_audit; extraction may slightly overrun',
        alns_budget_scope='entire_ALNS_call_including_construction_and_polishing',
        offline_audit_outside_search_budget=True,external_initial_solutions=False,
        exact_threads=1,exact_formulation='expanded',
        m8_batch_count_candidates=[1,2,3,4],m8_calendar_decision_grid_seconds=60,
        physical_time_tick_seconds=1,m3_preserve_fixed_calendar=True,
        economics='Original per-case synthetic profile, no rental-rate override',
        m1_packaging_price='Unknown all-in single-use packaging cost p; report thresholds and explicit sensitivity',
        pool_charge='5*N acquisition charged once; do not add initial_pool_investment a second time',
        first_attempts_only=True,
        environment=dict(python=sys.executable,python_version=platform.python_version(),
                         platform=platform.platform()))
    base.write(out/'manifest.json',manifest)
    base.write(out/'manifest.sha256.json',dict(sha256=base.sha(out/'manifest.json')))
    base.verify(out,manifest)
    analyze(out)
    print(json.dumps(dict(output=str(out),jobs=len(jobs),
        stages=dict(Counter(j['stage'] for j in jobs)),budget_hours=sum(j['budget'] for j in jobs)/3600),ensure_ascii=False))


def price_physical(problem,audit,economics,pool):
    """Same transport rates; single-use packaging stays symbolic when unknown."""
    from models.cost.joint_batch import JointCostProfile
    profile = JointCostProfile(economics)
    parts = {}
    for layer in ('e1','e2'):
        parts[layer+'_distance'] = audit['distances'][layer]*profile.rates['distance_scale_to_km']*profile.rates[layer+'_distance_cost_per_km']
        parts[layer+'_work'] = audit['time_metrics']['working_minutes'][layer]*profile.rates[layer+'_work_cost_per_minute']
        parts[layer+'_vehicles'] = audit[layer+'_vehicle_count']*profile.rates[layer+'_vehicle_cost_per_cycle']
        parts[layer+'_trips'] = audit[layer+'_trip_count']*profile.rates[layer+'_trip_cost']
    transport = sum(parts.values())
    if problem.reusable:
        value = profile.evaluate(audit,pool)
        return dict(total=value['total'],components=value['components'],transport=transport,
            pool_acquisition=value['components']['container_pool'],
            recurring_packaging=sum(value['components'][k] for k in ('cleaning','tracking','container_wear')),
            currency=value['currency'],economic_profile_hash=profile.hash,
            initial_investment_is_already_in_total=True)
    return dict(total=None,transport=transport,components=parts,container_uses=problem.total,
        total_formula='transport + Q*p; p is all-in single-use packaging cost per delivery',
        currency=economics['currency'],economic_profile_hash=profile.hash)


def worker(args):
    from tools.configuration.loader import load_routing_case
    from models.common.cycle import load_instance
    from models.common.cycle.audit import audit_solution
    from models.routing.service import solve_routing
    out = Path(args.output).resolve()
    manifest = base.read(out/'manifest.json')
    base.verify(out,manifest)
    job = next(j for j in manifest['jobs'] if j['id']==args.job)
    path = out/'runs'/job['id']/f'attempt_{args.attempt:03d}'
    cfg,fleet = load_routing_case(out/job['case_path'],use_pool_config=False)
    p = load_instance(cfg,fleet)
    if len(p.orders)!=job['customer_count']:
        raise ValueError('Frozen input customer count differs')
    started = time.perf_counter()
    if job['method']=='exact':
        module = import_module(f'models.routing.model_{job["model"]}.exact')
        result = module.solve_exact(p,time_limit=job['budget'],seed=job['seed'],
                                   verbose=True,milp_options=dict(threads=1,formulation='expanded'))
    else:
        result = solve_routing(model=job['model'],method='alns',problem=p,
            alns_time=job['budget'],alns_iter=job['iterations'],seed=job['seed'])
    solve_wall = time.perf_counter()-started
    base.write(path/'result.json',result)
    info = result['info']
    row = base.terminal_row(job,info['status'])
    row.update(attempt=args.attempt,solve_wall_seconds=solve_wall,
        solver_runtime=info.get('runtime'),build_time=info.get('build_time'),
        mip_runtime=info.get('mip_runtime'),best_bound=info.get('best_bound'),
        mip_gap=info.get('mip_gap'),proven_infeasible=bool(info.get('proven_infeasible')),
        iterations=info.get('iterations'),k=result.get('cycle_batch_count',1),
        solver_module=result.get('solver_module'),
        any_audit_failure=bool(info.get('audit_failure_count')),
        certificate_errors=info.get('certificate_error_count',0))
    begun = time.perf_counter()
    audit = audit_solution(p,result) if info.get('objective') is not None else dict(
        valid=False,not_applicable=True,errors=[],reason='No feasible solution returned')
    base.write(path/'audit.json',dict(result_sha256=base.sha(path/'result.json'),audit=audit))
    row['offline_audit_seconds'] = time.perf_counter()-begun
    if info.get('objective') is not None:
        if not audit['valid'] or not audit['periodic_replay']['valid']:
            row.update(status='AUDIT_FAILED',audit_errors=audit['errors'])
        else:
            if not math.isclose(audit['objective'],info['objective'],rel_tol=1e-7,abs_tol=1e-6):
                raise ValueError('Independent replay differs from native objective')
            pool = result['container_pool_size'] if job['model']==8 else p.operating_policy.container_pool_size
            cost = price_physical(p,audit,base.read(cfg['economic_config']),pool)
            if job['objective_mode']=='cost' and not math.isclose(cost['total'],info['objective'],rel_tol=1e-7,abs_tol=1e-6):
                raise ValueError('Independent economic replay differs from cost objective')
            if job['model']==8:
                selected = p.selected_problem(result)
                step = selected.calendar_decision_step
                if any(t % step for t in result['batch_boundaries']) or any(t % step for t in result['batch_cutoffs'].values()):
                    raise ValueError('M8 solution violates the minute calendar grid')
                if list(p.batch_count_candidates)!=[1,2,3,4]:
                    raise ValueError('M8 K domain differs from protocol')
            base.write(path/'cost_replay.json',cost)
            original_assignment = {o.customer:o.satellite for o in p.orders}
            assignment = result.get('assignment',original_assignment)
            row.update(valid=True,objective=info['objective'],proven_optimal=bool(info.get('proven_optimal')),
                total_distance=sum(audit['distances'].values()),
                e1_distance=audit['distances']['e1'],e2_distance=audit['distances']['e2'],
                Q=p.total,N=pool if p.reusable else 0,cost=cost.get('total'),
                cost_components=cost['components'],transport_cost=cost['transport'],
                pool_acquisition=cost.get('pool_acquisition',0),
                recurring_packaging=cost.get('recurring_packaging',0),
                minimum_pool=audit['minimum_pool_for_schedule'],periodic_valid=True,
                changed_assignments=sum(assignment[c]!=s for c,s in original_assignment.items()),
                e1_vehicles=audit['e1_vehicle_count'],e2_vehicles=audit['e2_vehicle_count'],
                e1_trips=audit['e1_trip_count'],e2_trips=audit['e2_trip_count'])
    if row['any_audit_failure'] or row['certificate_errors']:
        row.update(status='DIAGNOSTIC_FAILURE',valid=False,objective=None,proven_optimal=False)
    artifacts = ['result.json','audit.json']
    if (path/'cost_replay.json').exists():
        artifacts.append('cost_replay.json')
    base.finalize_attempt(path,row,artifacts)


def primary_rows(out,manifest):
    rows = []
    for job in manifest['jobs']:
        paths = base.attempt_dirs(out,job)
        if paths and base.complete_attempt(paths[0]):
            rows.append(base.read(paths[0]/'row.json'))
        else:
            rows.append(base.terminal_row(job,'RUNNING' if paths else 'NOT_RUN'))
    return rows


def write_csv(path,rows):
    fields = sorted({k for r in rows for k in r})
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('w',encoding='utf-8-sig',newline='') as stream:
        writer = csv.DictWriter(stream,fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({k:json.dumps(v,ensure_ascii=False) if isinstance(v,(dict,list)) else v
                             for k,v in row.items()})


def paired_cost(left,right):
    valid = left['valid'] and right['valid']
    row = dict(case=left['case'],size=left['size'],left=left['id'],right=right['id'],
        both_valid=valid,left_status=left['status'],right_status=right['status'])
    if valid:
        row.update(left_cost=left['cost'],right_cost=right['cost'],
            saving=left['cost']-right['cost'],
            saving_percent=100*(left['cost']-right['cost'])/left['cost'] if left['cost'] else None,
            transport_saving=left['transport_cost']-right['transport_cost'],
            pool_saving=left['pool_acquisition']-right['pool_acquisition'],
            recurring_packaging_saving=left['recurring_packaging']-right['recurring_packaging'],
            left_pool=left['N'],right_pool=right['N'],right_batch_count=right['k'])
    return row


def economic_threshold(single,reuse):
    row = dict(case=single['case'],size=single['size'],seed=single['seed'],
        both_valid=bool(single['valid'] and reuse['valid']))
    if not row['both_valid']:
        return row
    q = single['Q']
    extra_transport = reuse['transport_cost']-single['transport_cost']
    recurring = extra_transport+reuse['recurring_packaging']
    investment = reuse['pool_acquisition']
    row.update(Q=q,single_use_transport=single['transport_cost'],
        reusable_transport=reuse['transport_cost'],extra_transport=extra_transport,
        e1_transport_increment=reuse['cost_components']['e1_distance']-single['cost_components']['e1_distance'],
        e2_transport_increment=reuse['cost_components']['e2_distance']-single['cost_components']['e2_distance'],
        cleaning=reuse['cost_components']['cleaning'],tracking=reuse['cost_components']['tracking'],
        wear=reuse['cost_components']['container_wear'],pool_acquisition=investment,
        first_cycle_single_use_price_threshold=(investment+recurring)/q,
        recurring_single_use_price_threshold=recurring/q,
        recurring_saving_formula=f'{q}*p - ({recurring:.12g})',
        repeated_cycle_saving_formula=f't*({q}*p - ({recurring:.12g})) - ({investment:.12g})',
        repeated_cycle_assumptions='Identical repeated schedules; purchase once; no replacements/losses/discounting; lifetime unspecified')
    return row


def economic_sensitivity(threshold,prices=(0,1,2,3,4,5)):
    if not threshold['both_valid']:
        return []
    rows = []
    for price in prices:
        saving = threshold['Q']*(price-threshold['recurring_single_use_price_threshold'])
        investment = threshold['pool_acquisition']
        rows.append(dict(case=threshold['case'],size=threshold['size'],all_in_single_use_price=price,
            scenario_only=True,first_cycle_saving=saving-investment,
            recurring_saving=saving,
            first_break_even_cycle=max(1,math.ceil((investment-1e-9)/saving)) if saving>0 else None,
            replacement_or_lifetime_not_modelled=True))
    return rows


def analyze(out):
    out = Path(out).resolve()
    manifest = base.read(out/'manifest.json')
    base.verify(out,manifest)
    rows = primary_rows(out,manifest)
    terminal = [r for r in rows if r['status'] not in ('NOT_RUN','RUNNING')]
    errors = [r['id'] for r in terminal if r['status'] in ERRORS]
    stages = []
    for name in STAGES:
        group = [r for r in rows if r['stage']==name]
        done = [r for r in group if r['status'] not in ('NOT_RUN','RUNNING')]
        stages.append(dict(stage=name,planned=len(group),terminal=len(done),
            audited_feasible=sum(r['valid'] for r in group),
            complete=len(done)==len(group),execution_or_audit_errors=[r['id'] for r in done if r['status'] in ERRORS]))
    state = 'COMPLETED' if len(terminal)==len(rows) else 'RUNNING' if (out/'controller.json').exists() else 'PREPARED'
    if (out/'halt.json').exists():
        state = 'HALTED_EXECUTION_OR_AUDIT_ERROR'
    elif (out/'STOP').exists() and state!='COMPLETED':
        state = 'STOP_REQUESTED'
    progress = dict(state=state,planned=len(rows),terminal=len(terminal),
        audited_feasible=sum(r['valid'] for r in rows),optimal=sum(r['proven_optimal'] for r in rows),
        execution_or_audit_errors=errors,stages=stages,updated_at=base.now())
    base.write(out/'progress.json',progress)
    bykey = {(r['case'],r['stage'],r['model'],r['method']):r for r in rows}
    small,cost_pairs,distance_pairs,thresholds,sensitivity = [],[],[],[],[]
    for case in sorted({r['case'] for r in rows}):
        if case.startswith('S'):
            a = bykey[case,STAGES[0],8,'exact']
            b = bykey[case,STAGES[0],8,'alns']
            c = bykey[case,STAGES[0],3,'exact']
            r = dict(case=case,m8_exact_status=a['status'],m8_exact_valid=a['valid'],
                m8_exact_cost=a['objective'],m8_exact_bound=a.get('best_bound'),
                m8_exact_proven_optimal=a['proven_optimal'],
                m8_alns_status=b['status'],m8_alns_valid=b['valid'],m8_alns_cost=b['objective'],
                m3_exact_status=c['status'],m3_exact_valid=c['valid'],m3_exact_cost=c['objective'],
                m3_exact_proven_optimal=c['proven_optimal'],
                m8_alns_minus_exact_cost=b['objective']-a['objective'] if a['valid'] and b['valid'] else None,
                m8_alns_saving_vs_m3_exact=c['objective']-b['objective'] if c['valid'] and b['valid'] else None)
            small.append(r)
        else:
            a,b = [bykey[case,STAGES[1],m,'alns'] for m in (3,8)]
            cost_pairs.append(paired_cost(a,b))
            a,b,c = [bykey[case,STAGES[2],m,'alns'] for m in (1,3,4)]
            threshold = economic_threshold(a,b)
            thresholds.append(threshold)
            sensitivity.extend(economic_sensitivity(threshold))
            for left,right in ((a,b),(b,c)):
                r = dict(case=case,size=a['size'],left_model=left['model'],right_model=right['model'],
                    both_valid=bool(left['valid'] and right['valid']),
                    left_status=left['status'],right_status=right['status'])
                if r['both_valid']:
                    r.update(left_distance=left['total_distance'],right_distance=right['total_distance'],
                        distance_saving=left['total_distance']-right['total_distance'],
                        distance_saving_percent=100*(left['total_distance']-right['total_distance'])/left['total_distance'] if left['total_distance'] else None,
                        e1_distance_saving=left['e1_distance']-right['e1_distance'],
                        e2_distance_saving=left['e2_distance']-right['e2_distance'],
                        changed_assignments=right['changed_assignments'])
                distance_pairs.append(r)
    tables = dict(runs=rows,small_300s=small,m3_m8_cost_benefit=cost_pairs,
        distance_comparison=distance_pairs,m1_m3_economic_threshold=thresholds,
        m1_m3_packaging_price_sensitivity=sensitivity)
    for name,values in tables.items():
        write_csv(out/f'tables/{name}.csv',values)
    base.write(out/'analysis.json',dict(progress=progress,tables=tables,first_attempts_only=True))
    def value(number):
        return f'{number:.4f}' if isinstance(number,(int,float)) else '—'
    lines = ['# 三阶段模型对照实验','',
        f'状态：{state}；完成 {len(terminal)}/{len(rows)} 次；独立审计可行 {progress["audited_feasible"]} 次。',
        '','每档全部9例（7/20/50顾客），种子42，串行执行，精确单线程，无外部初始解。',
        '模型八的两种方法均为K=1—4、整分钟批次边界和分界；行驶、服务、时间窗仍保留秒级物理时钟。',
        '模型三保留原固定单批日历（可含半分钟分界）；M3/M8离散允许域不声称相互包含。',
        '精确预算包含建模和优化；提取、校验存在少量尾部开销。额外独立审计不延长搜索预算。',
        '成本均沿用各例原始合成费用：0.1D1+0.05D2+5N+0.02Q。5N是一次采购项，不再额外叠加同一初始投资。','',
        '| 阶段 | 完成/计划 | 审计可行 |', '| --- | ---: | ---: |']
    for stage in stages:
        lines.append(f'| {stage["stage"]} | {stage["terminal"]}/{stage["planned"]} | {stage["audited_feasible"]} |')
    lines += ['','## 小规模：300秒成本对照','',
        '| 算例 | M3精确 | M8精确 | M8精确状态 | M8 ALNS | ALNS−精确 |',
        '| --- | ---: | ---: | --- | ---: | ---: |']
    for r in small:
        lines.append(f'| {r["case"]} | {value(r["m3_exact_cost"])} | {value(r["m8_exact_cost"])} | {r["m8_exact_status"]} | {value(r["m8_alns_cost"])} | {value(r["m8_alns_minus_exact_cost"])} |')
    lines += ['','限时精确值是当次最好可行解；仅标记已证最优，不将限时参照差写成最优gap。无解返回不等于已证不可行。',
        '','## 中大规模：成本目标M3/M8','',
        '| 算例 | M3费用 | M8费用 | 节省 | 运输节省 | 箱池节省 |',
        '| --- | ---: | ---: | ---: | ---: | ---: |']
    for r in cost_pairs:
        lines.append(f'| {r["case"]} | {value(r.get("left_cost"))} | {value(r.get("right_cost"))} | {value(r.get("saving"))} | {value(r.get("transport_saving"))} | {value(r.get("pool_saving"))} |')
    lines += ['','中规模每次600秒，大规模1200秒。费用变化是有限预算所得方案的配对差，分批、日历和箱池共同变化。',
        '','## 中大规模：距离目标M1/M3/M4','',
        '| 算例 | 对照 | 左侧距离 | 右侧距离 | 距离节省 | 节省% |',
        '| --- | --- | ---: | ---: | ---: | ---: |']
    for r in distance_pairs:
        lines.append(f'| {r["case"]} | M{r["left_model"]}/M{r["right_model"]} | {value(r.get("left_distance"))} | {value(r.get("right_distance"))} | {value(r.get("distance_saving"))} | {value(r.get("distance_saving_percent"))} |')
    lines += ['','中规模每次1200秒，大规模1800秒。M3/M4用于观察自由选仓；仍同时报告失败及可能的负收益。',
        '','## M1/M3：收益来源与包装单价阈值','',
        '| 算例 | 额外运输费 | 一次采购5N | 清洗/追踪/磨损 | 首周期p阈值 | 后续周期p阈值 |',
        '| --- | ---: | ---: | ---: | ---: | ---: |']
    for r in thresholds:
        recurring = sum(r.get(k,0) for k in ('cleaning','tracking','wear')) if r['both_valid'] else None
        lines.append(f'| {r["case"]} | {value(r.get("extra_transport"))} | {value(r.get("pool_acquisition"))} | {value(recurring)} | {value(r.get("first_cycle_single_use_price_threshold"))} | {value(r.get("recurring_single_use_price_threshold"))} |')
    lines += ['','p为每次配送的一次性包装购置及处置合计费用，与当前合成费用使用同一单位。',
        '首周期节省=Qp−额外运输−清洗追踪磨损−5N；相同方案重复t周期且期间无需换箱时，累计节省=t×(Qp−额外运输−清洗追踪磨损)−5N。',
        '原始费用没有给p或寿命，因此报告阈值；CSV另列p=0/1/2/3/4/5的明确情景，不将其当作已知报价或实际盈利。',
        '所有成本复算使用各自距离目标所得调度，不把复算费用称为费用最优；价格敏感性不重新求路线。','',
        f'执行/审计异常：{len(errors)}。详见tables/与每次runs/下的原始结果、日志和审计。']
    (out/'report.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    return progress


def run(args):
    out = Path(args.output).resolve()
    manifest = base.read(out/'manifest.json')
    base.verify(out,manifest)
    smoke_record = base.read(out/'smoke_validation.json')
    if not smoke_record.get('valid') or smoke_record['source_manifest_sha256'] != base.sha(out/'manifest.json'):
        raise ValueError('The frozen study has not passed its operational smoke checks')
    with base.study_lock(out):
        base.write(out/'controller.json',dict(pid=os.getpid(),started_at=base.now(),python=sys.executable))
        for job in manifest['jobs']:
            if (out/'STOP').exists():
                break
            paths = base.attempt_dirs(out,job)
            if paths:
                if not base.complete_attempt(paths[0]):
                    launch = base.read(paths[0]/'launch.json') if (paths[0]/'launch.json').exists() else {}
                    if launch.get('pid') and base.pid_alive(launch['pid']):
                        raise RuntimeError('A previous worker is still running; refusing a duplicate')
                    row = base.terminal_row(job,'INTERRUPTED','Previous worker did not complete')
                    row['attempt'] = 1
                    base.finalize_attempt(paths[0],row)
                if base.read(paths[0]/'row.json')['status'] in ERRORS:
                    base.write(out/'halt.json',dict(reason='Prior execution/audit error',job=job['id'],at=base.now()))
                    break
                continue
            base.write(out/'active_job.json',dict(job=job['id'],stage=job['stage'],started_at=base.now()))
            print(f'START {job["id"]}',flush=True)
            row = base.execute(out,manifest,job)
            with (out/'progress.jsonl').open('a',encoding='utf-8') as stream:
                stream.write(json.dumps(dict(at=base.now(),job=job['id'],status=row['status'],
                    valid=row['valid'],objective=row['objective']),ensure_ascii=False)+'\n')
            progress = analyze(out)
            print(f'{progress["terminal"]}/{progress["planned"]} {job["id"]}: {row["status"]}; valid={row["valid"]}; objective={row["objective"]}',flush=True)
            if row['status'] in ERRORS:
                base.write(out/'halt.json',dict(reason='Execution/audit error',job=job['id'],at=base.now()))
                break
        progress = analyze(out)
        base.write(out/'controller_exit.json',dict(pid=os.getpid(),finished_at=base.now(),progress=progress))
        print(json.dumps(progress,ensure_ascii=False),flush=True)


def smoke(args):
    """Short isolated dispatch checks; never inserted into the formal ledger."""
    out = Path(args.output).resolve()
    manifest = base.read(out/'manifest.json')
    base.verify(out,manifest)
    smoke_out = out/'smoke'
    if smoke_out.exists():
        raise ValueError('Smoke output already exists; do not overwrite evidence')
    (smoke_out/'runs').mkdir(parents=True)
    selected = [j for j in manifest['jobs'] if j['case']=='S01']
    selected += [j for j in manifest['jobs'] if j['case'] in ('M01','L01') and j['method']=='alns']
    jobs = []
    for original in selected:
        job = deepcopy(original)
        job.update(budget=args.seconds,id='smoke_'+original['id'],
                   case_path=str(out/original['case_path']))
        jobs.append(job)
    # The frozen source tree is selected by the parent driver; worker commands
    # still run that driver and read the separate smoke manifest.
    smoke_manifest = {**manifest,'jobs':jobs,'source_hashes':{},'input_hashes':{}}
    shutil.copy2(out/'driver.py',smoke_out/'driver.py')
    # A tiny bootstrap selects the parent's frozen source root explicitly.
    bootstrap = ('import runpy,sys\nfrom pathlib import Path\n'
        +f'sys.path.insert(0,{str(out/"sources/current")!r})\n'
        +f'runpy.run_path({str(out/"driver.py")!r},run_name="__main__")\n')
    (smoke_out/'driver.py').write_text(bootstrap,encoding='utf-8')
    base.write(smoke_out/'manifest.json',smoke_manifest)
    base.write(smoke_out/'manifest.sha256.json',dict(sha256=base.sha(smoke_out/'manifest.json')))
    rows = []
    for job in jobs:
        row = base.execute(smoke_out,smoke_manifest,job)
        rows.append(row)
        base.write(smoke_out/'rows.json',rows)
        print(f'{job["id"]}: {row["status"]}; valid={row["valid"]}',flush=True)
        if row['status'] in ERRORS:
            raise RuntimeError(f'Smoke failed; see {smoke_out/"runs"/job["id"]}')
    base.write(out/'smoke_validation.json',dict(valid=True,formal_runs=0,jobs=len(rows),
        statuses=dict(Counter(r['status'] for r in rows)),audited_feasible=sum(r['valid'] for r in rows),
        source_manifest_sha256=base.sha(out/'manifest.json'),finished_at=base.now()))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command',choices=('prepare','run','worker','status','smoke'))
    parser.add_argument('--output',required=True)
    parser.add_argument('--dataset',default=DATASET)
    parser.add_argument('--job')
    parser.add_argument('--attempt',type=int,default=1)
    parser.add_argument('--seconds',type=float,default=5)
    args = parser.parse_args()
    if args.command=='prepare':
        prepare(args)
    elif args.command=='worker':
        worker(args)
    elif args.command=='run':
        run(args)
    elif args.command=='smoke':
        smoke(args)
    else:
        print(json.dumps(analyze(Path(args.output)),ensure_ascii=False))


if __name__=='__main__':
    main()
