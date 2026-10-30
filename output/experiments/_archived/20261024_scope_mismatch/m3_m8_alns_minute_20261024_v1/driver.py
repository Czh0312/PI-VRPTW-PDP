"""Frozen paired M3/M8 ALNS experiment. No exact experiment jobs."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from copy import deepcopy
import csv
from dataclasses import asdict
import json
import math
from pathlib import Path
import statistics
import subprocess
import sys
import time
import traceback

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parents[2]
sys.path.insert(0, str(HERE / 'source' if (HERE / 'source').is_dir() else PROJECT))
from experiments.runs import run_thesis_completion as d
from experiments.runs import run_spacetime_quality as q
from experiments.runs.spacetime_windows_io_adapter import atomic_write
import calendar_grid

d.write = atomic_write
BUDGETS = dict(small=300, medium=600, large=1200)
SEEDS = (42, 43, 44)
PILOTS = ('S01', 'M05', 'L05')


def csv_write(path, rows, fields):
    temporary = path.with_name(path.name + '.tmp')
    with temporary.open('w', encoding='utf-8-sig', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction='ignore')
        writer.writeheader()
        writer.writerows(rows)
    for attempt in range(12):
        try:
            temporary.replace(path)
            return
        except PermissionError:
            if attempt == 11:
                raise
            time.sleep(.1)


def collect(out, manifest):
    rows = []
    for job in manifest['jobs']:
        attempts = d.attempt_dirs(out, job)
        complete = [p for p in attempts if d.complete_attempt(p)]
        row = d.read(complete[0] / 'row.json') if complete else d.terminal_row(
            job, 'RUNNING' if attempts else 'NOT_RUN')
        rows.append(row)
    return rows


def summarize(out, manifest):
    rows = collect(out, manifest)
    by_key = {(r['case'], r['seed'], r['model']): r for r in rows}
    pairs = []
    for case, seed in sorted({(r['case'], r['seed']) for r in rows}):
        a, b = by_key[case, seed, 3], by_key[case, seed, 8]
        valid = bool(a.get('valid') and b.get('valid'))
        ca, cb = a.get('objective'), b.get('objective')
        pairs.append(dict(case=case, size=a['size'], customer_count=a['customer_count'], seed=seed,
            valid_pair=valid, m3_status=a['status'], m8_status=b['status'],
            m3_cost=ca, m8_cost=cb,
            saving=ca-cb if valid else None,
            saving_pct=100*(ca-cb)/ca if valid and ca else None,
            m3_pool=a.get('pool'), m8_pool=b.get('pool'), m8_k=b.get('k'),
            m3_transport=a.get('transport_cost'), m8_transport=b.get('transport_cost')))
    case_stats = []
    for case in sorted({r['case'] for r in rows}):
        selected = [p for p in pairs if p['case'] == case]
        valid = [p for p in selected if p['valid_pair']]
        row = dict(case=case, size=selected[0]['size'], customer_count=selected[0]['customer_count'],
                   planned_seeds=len(SEEDS), valid_pairs=len(valid))
        for model in (3, 8):
            values = [r['objective'] for r in rows if r['case'] == case and r['model'] == model and r.get('valid')]
            row.update({f'm{model}_feasible_seeds': len(values),
                        f'm{model}_mean': statistics.mean(values) if values else None,
                        f'm{model}_best': min(values) if values else None,
                        f'm{model}_sd': statistics.stdev(values) if len(values) > 1 else None})
        row['paired_mean_saving_pct'] = statistics.mean(p['saving_pct'] for p in valid) if valid else None
        case_stats.append(row)
    terminal = [r for r in rows if r['status'] not in ('NOT_RUN', 'RUNNING')]
    state = dict(planned=len(rows), completed=len(terminal),
        audited_feasible=sum(bool(r.get('valid')) for r in rows), valid_pairs=sum(p['valid_pair'] for p in pairs),
        planned_pairs=len(pairs), failed=sum(not r.get('valid', False) for r in terminal),
        running=[r['id'] for r in rows if r['status'] == 'RUNNING'],
        status='COMPLETE' if len(terminal) == len(rows) else 'RUNNING', updated_at=d.now())
    d.write(out / 'rows.json', rows)
    d.write(out / 'paired_comparison.json', pairs)
    d.write(out / 'case_statistics.json', case_stats)
    d.write(out / 'progress.json', state)
    csv_write(out / 'runs.csv', rows, ['id','case','size','customer_count','model','seed','budget','status','valid',
        'objective','pool','k','transport_cost','pool_cost','e1_distance','e2_distance','e1_trips','e2_trips',
        'solve_wall_seconds','offline_audit_seconds','periodic_valid','minute_grid_valid','iterations','attempt','error'])
    csv_write(out / 'paired_comparison.csv', pairs, list(pairs[0]))
    csv_write(out / 'case_statistics.csv', case_stats, list(case_stats[0]))
    fmt = lambda value: '—' if value is None else f'{value:.4f}'
    text = ['# 模型3与模型8：ALNS配对对比', '',
        f"完成 {state['completed']}/{state['planned']} 次；独立审计可行 {state['audited_feasible']} 次；有效配对 {state['valid_pairs']}/{state['planned_pairs']}。", '',
        '正式冻结数据 formal_benchmark27_spacetime_v4_1；每规模9例，共27例。',
        '模型3显式优化总成本，保留原固定单批次日历和配置箱池；模型8优化K∈{1,2,3,4}、边界、截点、箱池和路线。',
        '模型8边界和截点在每次物理解码前投影至整分钟；车辆、服务、清洗及行驶计算仍用原始秒级刻度。',
        '原始数据及公共模型源码不改写；实验适配器、源码快照、输入与配置均有哈希。',
        '每次独立冷启动，无外部初解；配对种子42、43、44；单进程串行运行。',
        '小/中/大分别300/600/1200秒；初始解、内部重启和全部K共享一份预算；离线审计与进程开销另列。',
        '先跑S01、M05、L05的种子42共6次；全部通过有效性与分钟约束后自动继续。',
        '节约率=(模型3成本−模型8成本)/模型3成本；负值照实保留，缺失/失败不补零，不只挑选有收益算例。',
        '成本来自独立复算与连续两周期重放；ALNS不提供最优性证明。收益反映两模型决策范围的综合差别。', '',
        '| 算例 | 客户数 | 有效配对/3 | M3平均成本 | M8平均成本 | 配对平均节约率% |',
        '|---|---:|---:|---:|---:|---:|']
    for row in case_stats:
        text.append(f"| {row['case']} | {row['customer_count']} | {row['valid_pairs']} | {fmt(row['m3_mean'])} | {fmt(row['m8_mean'])} | {fmt(row['paired_mean_saving_pct'])} |")
    text += ['', '均值和标准差仅基于已完成且通过审计的运行，正式结论须等全部运行结束后评估完整率。',
             '明细：runs.csv；逐种子配对：paired_comparison.csv；逐例统计：case_statistics.csv；完整解与独立审计：runs/。', '']
    (out / 'report.md').write_text('\n'.join(text), encoding='utf-8')
    return state


def prepare(out):
    if (out / 'manifest.json').exists():
        raise ValueError('Refusing to replace a registered experiment')
    import psutil
    from experiments.runs.run_model6_comparison import freeze_case
    from tools.configuration.loader import load_routing_case
    from models.common.cycle import load_instance
    frozen = d.read(PROJECT / 'config/experiments/benchmark27_spacetime_v4_1/frozen.json')
    dataset = PROJECT / frozen['dataset']
    for name, key in [('manifest.json','manifest_sha256'), ('selection.json','selection_sha256'), ('case_index.csv','case_index_sha256')]:
        assert d.sha(dataset / name) == frozen[key], name
    records = [r for r in d.read(dataset / 'manifest.json')['cases'] if r['model'] in (3, 8)]
    assert len(records) == 54
    for directory in ('inputs', 'cases', 'runs'):
        (out / directory).mkdir(exist_ok=True)
    evidence, configs, problems = {}, {}, {}
    for record in records:
        case, model = record['base_case_id'], record['model']
        prefix = f'{case}_m{model}'
        config, hashes = freeze_case(record['case_path'], out, prefix)
        evidence.update(hashes)
        config.update(solver='alns', alns_time=BUDGETS[record['size']], alns_iter=10**9, seed=42)
        for key in ('initial_joint_result', 'milp_options', 'exact_strategy'):
            config.pop(key, None)
        for key in ('parameter_contract', 'batch_count_certificate'):
            if isinstance(config.get(key), str):
                original = PROJECT / config[key]
                target = out / 'inputs' / f'{prefix}_{key}.json'
                target.write_bytes(original.read_bytes())
                evidence[str(original)] = dict(sha256=d.sha(original), snapshot=target.relative_to(out).as_posix())
                config[key] = str(target)
        if model == 3:
            config['objective_mode'] = 'cost'
        else:
            config['joint_batch_options'] = {**config['joint_batch_options'], 'batch_count_candidates': [1,2,3,4]}
        config['experiment_settings'] = dict(objective='cost', external_initial_solution=False,
            calendar_decision_grid_seconds=60 if model == 8 else None,
            original_fixed_m3_calendar_preserved=model == 3, batch_count_candidates=[1,2,3,4] if model == 8 else [1])
        d.write(out / 'cases' / f'{prefix}.json', config)
        configs[case, model] = (config, record)
        cfg, fleet = load_routing_case(out / 'cases' / f'{prefix}.json', use_pool_config=False)
        problems[case, model] = load_instance(cfg, fleet)
        if model == 3:
            assert problems[case, model].cost_objective
    matched = []
    physical_fields = ('id','customer','satellite','count','goods_weight','release_time','delivery_window','pickup_window','service_time','min_use_time')
    for case in sorted({key[0] for key in configs}):
        a, b = problems[case, 3], problems[case, 8]
        assert a.total == b.total
        assert a.cost_profile.hash == b.cost_profile.hash
        assert {tuple(getattr(o,k) for k in physical_fields) for o in a.orders} == {tuple(getattr(o,k) for k in physical_fields) for o in b.orders}
        for key in ('data','routing_config'):
            assert d.sha(Path(configs[case,3][0][key])) == d.sha(Path(configs[case,8][0][key]))
        pa, pb = asdict(a.operating_policy), asdict(b.operating_policy)
        assert {k:v for k,v in pa.items() if k != 'batches'} == {k:v for k,v in pb.items() if k != 'batches'}
        matched.append(dict(case=case, customer_count=len(a.orders), physical_inputs_match=True,
                            economic_profile_hash=a.cost_profile.hash, policy_differences='batch calendar only'))
    jobs = []
    for seed in SEEDS:
        for index, case in enumerate(sorted({key[0] for key in configs})):
            for model in ((3,8) if (index + seed) % 2 == 0 else (8,3)):
                _, record = configs[case, model]
                size = record['size']
                jobs.append(dict(id=f'{case}_m{model}_alns_{BUDGETS[size]}s_s{seed}', case=case, model=model,
                    method='alns', version='current', seed=seed, budget=BUDGETS[size], iterations=10**9,
                    size=size, structure=size, split='formal', family=record['family_id'],
                    source_block=record['source_block'], customer_count=record['customer_count'],
                    case_path=f'cases/{case}_m{model}.json', pilot=case in PILOTS and seed == 42))
    pilot_order = {(case, model): 2*i+j for i,case in enumerate(PILOTS) for j,model in enumerate((3,8))}
    jobs.sort(key=lambda j: (0,pilot_order[j['case'],j['model']]) if j['pilot'] else (1,SEEDS.index(j['seed']),j['case'],(j['model']+j['seed'])%2))
    sources = {f'source/{name}': digest for name,digest in d.snapshot(PROJECT, out / 'source').items()}
    for name in ('driver.py','calendar_grid.py','test_protocol.py'):
        sources[name] = d.sha(out / name)
    for name, value in [('frozen_dataset.json', frozen), ('source_input_evidence.json', evidence),
        ('paired_input_checks.json', dict(valid=True, cases=matched)),
        ('environment.json', dict(captured_at=d.now(), interpreter=sys.executable, python=sys.version,
            physical_cpus=psutil.cpu_count(logical=False), logical_cpus=psutil.cpu_count(),
            total_memory_mib=psutil.virtual_memory().total//1024**2,
            available_memory_mib=psutil.virtual_memory().available//1024**2))]:
        d.write(out / name, value)
    inputs = {p.relative_to(out).as_posix():d.sha(p) for folder in ('inputs','cases') for p in (out/folder).rglob('*') if p.is_file()}
    for name in ('frozen_dataset.json','source_input_evidence.json','paired_input_checks.json','environment.json'):
        inputs[name] = d.sha(out/name)
    manifest = dict(schema_version=1, phase='paired_m3_m8_alns_minute', registered_at=d.now(),
        jobs=jobs, workers=1, seeds=list(SEEDS), budgets=BUDGETS, pilot_cases=list(PILOTS),
        dataset=frozen['dataset'], source_hashes=sources, input_hashes=inputs,
        timeout_overhead=180, worker_memory_mib=1536, minimum_available_mib=128,
        memory_pressure_grace_seconds=20, interpreter=sys.executable,
        external_starts=False, methods=['alns'], objective_by_model={'3':'cost','8':'cost'},
        m8_batch_count_candidates=[1,2,3,4], m8_calendar_decision_grid_seconds=60,
        physical_time_units='Unchanged original second clock; no travel/service rounding changes',
        total_call_budget=sum(j['budget'] for j in jobs), plan_count=len(jobs),
        first_attempt_is_primary=True, commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=PROJECT,text=True).strip())
    assert len(jobs) == 162 and manifest['total_call_budget'] == 113400
    d.write(out / 'manifest.json', manifest)
    d.write(out / 'manifest.sha256.json', dict(sha256=d.sha(out/'manifest.json')))
    (out / '.gitignore').write_text('/source/\n/driver.lock\n', encoding='utf-8')
    print(json.dumps(summarize(out, manifest)), flush=True)


def worker(args):
    out = Path(args.output).resolve()
    manifest = d.read(out / 'manifest.json')
    d.verify(out, manifest)
    job = next(j for j in manifest['jobs'] if j['id'] == args.job)
    assert job['method'] == 'alns'
    path = out / 'runs' / job['id'] / f'attempt_{args.attempt:03d}'
    from tools.configuration.loader import load_routing_case
    from models.common.cycle import load_instance
    from models.common.cycle.audit import audit_solution
    from models.cost.comparison import evaluate_comparison_cost
    from models.routing.service import solve_routing
    if job['model'] == 8:
        calendar_grid.install()
    config, fleet = load_routing_case(out / job['case_path'], use_pool_config=False)
    problem = load_instance(config, fleet)
    trace, history = [], []
    begun = time.perf_counter()
    if job['model'] == 8:
        from models.routing.model_8.alns import solver, search
        original = solver.aggregate
        observed = d.observe_counts(original, trace, history, begun)
        def observed_aggregate(parent, *, solve_candidate, **kwargs):
            def candidate(child, **options):
                result = solve_candidate(child, **options)
                d.write(path / 'search_live.json', dict(updated_at=d.now(), elapsed=time.perf_counter()-begun,
                    k=child.count, objective=result['info'].get('objective'),
                    child_audit_valid=result.get('audit',{}).get('valid',False),
                    scope='child_return_before_parent_final_confirmation'))
                return result
            return observed(parent, solve_candidate=candidate, **kwargs)
        solver.aggregate = observed_aggregate
    result = solve_routing(model=job['model'], method='alns', problem=problem,
                           alns_time=job['budget'], alns_iter=job['iterations'], seed=job['seed'])
    solve_wall = time.perf_counter() - begun
    # Validate actual result, without changing a returned decision.
    info = result['info']
    has_solution = info.get('objective') is not None
    d.write(path / 'result.json', result)
    audit_started = time.perf_counter()
    audit = audit_solution(problem, result) if has_solution else dict(valid=False, errors=[], not_applicable=True)
    grid = calendar_grid.check_result(problem, result) if job['model'] == 8 else dict(valid=True, not_applicable=True)
    cost = evaluate_comparison_cost(problem,result,config['economic_config'],audit=audit) if audit['valid'] else None
    errors = []
    if has_solution and not audit['valid']:
        errors.append('INDEPENDENT_AUDIT_FAILED')
    if has_solution and not grid['valid']:
        errors.append('MINUTE_GRID_FAILED')
    if cost and not math.isclose(cost['total'],info['objective'],rel_tol=1e-9,abs_tol=1e-6):
        errors.append('INDEPENDENT_COST_FAILED')
    periodic = bool(audit.get('periodic_replay',{}).get('valid'))
    if has_solution and not periodic:
        errors.append('TWO_CYCLE_REPLAY_FAILED')
    if has_solution and result.get('objective_mode') != 'cost':
        errors.append('WRONG_OBJECTIVE_MODE')
    if info.get('audit_failure_count') or info.get('certificate_error_count'):
        errors.append('INTERNAL_DIAGNOSTIC_FAILURE')
    if solve_wall > job['budget'] * 1.05 + 5:
        errors.append('EXCESSIVE_BUDGET_OVERRUN')
    valid = bool(cost and grid['valid'] and periodic and not errors)
    components = cost['components'] if cost else {}
    row = {**job, 'attempt':args.attempt, 'status':'FEASIBLE' if valid else 'VALIDATION_FAILED' if errors else info['status'],
        'valid':valid, 'objective':cost['total'] if valid else None, 'pool':cost['container_pool_size'] if valid else None,
        'k':result.get('cycle_batch_count',1) if has_solution else None,
        'cost_components':components, 'transport_cost':sum(components.get(k,0) for k in ('e1_distance','e2_distance')) if cost else None,
        'pool_cost':components.get('container_pool') if cost else None,
        'solve_wall_seconds':solve_wall, 'offline_audit_seconds':time.perf_counter()-audit_started,
        'iterations':info.get('iterations'), 'periodic_valid':periodic,
        'minute_grid_valid':grid['valid'] if job['model'] == 8 else None,
        'proven_optimal':False, 'proven_infeasible':False, 'best_bound':None, 'mip_gap':None,
        'error':'; '.join(errors) if errors else None, 'finished_at':d.now()}
    if audit['valid']:
        row.update(e1_distance=audit['distances']['e1'],e2_distance=audit['distances']['e2'],
                   e1_trips=audit['e1_trip_count'],e2_trips=audit['e2_trip_count'])
    if job['model'] == 3:
        history = [dict(elapsed=h['time'],objective=h['objective'],source='native_incumbent_history')
                   for h in info.get('incumbent_history',[])]
    history.append(dict(elapsed=solve_wall,objective=row['objective'],source='final_independent_audit',independently_audited=valid))
    d.write(path / 'audit.json', dict(result_sha256=d.sha(path/'result.json'),audit=audit))
    d.write(path / 'cost_check.json', cost)
    d.write(path / 'minute_grid_check.json', grid)
    d.write(path / 'candidate_diagnostics.json', trace)
    d.write(path / 'trajectory.json', history)
    d.finalize_attempt(path,row,('result.json','audit.json','cost_check.json','minute_grid_check.json','candidate_diagnostics.json','trajectory.json'))
    print(json.dumps(dict(job=job['id'],valid=valid,status=row['status'],objective=row['objective'],k=row['k'],pool=row['pool'])),flush=True)


def run(out):
    import os
    import psutil
    manifest = d.read(out / 'manifest.json')
    d.verify(out,manifest)
    with d.study_lock(out):
        d.write(out/'controller_state.json',dict(status='RUNNING',pid=os.getpid(),started_at=d.now()))
        for job in manifest['jobs']:
            if (out/'STOP').exists():
                d.write(out/'controller_state.json',dict(status='STOPPED_AFTER_ACTIVE_JOB',updated_at=d.now()))
                break
            previous = d.attempt_dirs(out,job)
            if previous:
                if not all(d.complete_attempt(p) for p in previous):
                    raise RuntimeError(f"Incomplete prior attempt needs inspection: {job['id']}")
                continue
            if not job['pilot'] and not (out/'pilot_gate.json').exists():
                rows = collect(out,manifest)
                pilots = [r for r in rows if r['pilot']]
                passed = len(pilots) == 6 and all(r.get('valid') for r in pilots)
                d.write(out/'pilot_gate.json',dict(passed=passed,checked_at=d.now(),jobs=pilots))
                if not passed:
                    d.write(out/'controller_state.json',dict(status='PILOT_FAILED_REVIEW_REQUIRED',updated_at=d.now()))
                    print('Pilot gate failed; preserving all primary attempts; full queue not dispatched.',flush=True)
                    break
            if (out/'pilot_gate.json').exists() and not d.read(out/'pilot_gate.json')['passed']:
                break
            while psutil.virtual_memory().available < 384*1024**2:
                d.write(out/'resource_wait.json',dict(job=job['id'],status='WAITING_FOR_MEMORY',
                    available_mib=psutil.virtual_memory().available//1024**2,updated_at=d.now()))
                if (out/'STOP').exists():
                    return
                time.sleep(5)
            print('START '+job['id'],flush=True)
            row = q.execute(out,manifest,job)
            state = summarize(out,manifest)
            with (out/'progress.jsonl').open('a',encoding='utf-8') as handle:
                handle.write(json.dumps(dict(at=d.now(),job=job['id'],status=row['status'],valid=row['valid'],objective=row.get('objective')))+'\n')
            print(json.dumps(dict(progress=state,last_job=job['id'],objective=row.get('objective'),valid=row['valid'])),flush=True)
            if row['status'] in ('ERROR','RESOURCE_LIMIT','OUTER_TIMEOUT','VALIDATION_FAILED'):
                d.write(out/'controller_state.json',dict(status='FAILED_REVIEW_REQUIRED',job=job['id'],updated_at=d.now()))
                break
        state = summarize(out,manifest)
        if state['completed'] == state['planned']:
            d.write(out/'controller_state.json',dict(status='COMPLETE',updated_at=d.now(),summary=state))
        print(json.dumps(state),flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['prepare','run','worker','status'])
    parser.add_argument('--output',required=True)
    parser.add_argument('--job')
    parser.add_argument('--attempt',type=int,default=1)
    arguments = parser.parse_args()
    directory = Path(arguments.output).resolve()
    if arguments.action == 'prepare': prepare(directory)
    elif arguments.action == 'run': run(directory)
    elif arguments.action == 'worker': worker(arguments)
    else: print(json.dumps(summarize(directory,d.read(directory/'manifest.json'))),flush=True)
