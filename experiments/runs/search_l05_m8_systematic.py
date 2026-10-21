"""Budgeted ALNS search on the frozen five-site/25-box case (about 29 min).

Existing ALNS/model code is not modified. Warm starts and fixed-K searches are
explicitly distinguished from full-domain cold starts. No exact solver is used.
"""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
import csv
import hashlib
import json
from pathlib import Path
import platform
import statistics
import subprocess
import time
import zipfile

from tools.configuration.loader import PROJECT_ROOT, load_routing_case
from models.common.cycle import load_instance
from models.common.cycle.audit import audit_solution
from models.common.cycle.calendar import attach_calendar_decisions
from models.cost.comparison import evaluate_comparison_cost
from cli.multibatch import run_case

DATA = Path('data/mechanism_l05_5sat_balanced25_v1')
PREVIOUS = Path('output/l05_5sat_balanced25_design_20261020')
ROOT = Path('output/l05_m8_systematic_search_20261020')
START = PREVIOUS/'merge_diagnosis/M8_feasible_start_coupled_cutoff_earlier_20.json'


def read(path):
    return json.loads((PROJECT_ROOT/path).read_text(encoding='utf-8'))


def write(path, value):
    path = PROJECT_ROOT/path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)+'\n', encoding='utf-8')


def sha(path):
    return hashlib.sha256((PROJECT_ROOT/path).read_bytes()).hexdigest()


def export_full(parent, result, origin):
    result = deepcopy(result)
    searched = list(result.get('calendar_config', {}).get('batch_count_candidates', []))
    attach_calendar_decisions(parent, result, result['cycle_batch_count'], result['batch_boundaries'])
    checked = audit_solution(parent, result)
    if not checked['valid']:
        raise ValueError(f'Full-domain export failed independent audit: {checked["errors"]}')
    result['audit'] = checked
    result['feasible_export'] = dict(origin=origin, original_search_K_domain=searched,
        full_validation_K_domain=list(parent.batch_count_candidates),
        note='A feasible solution in the full domain; no full-domain optimality claim.')
    return result


def metrics(problem, result, label, seed, budget, folder, initial_cost):
    info = result['info']
    audit = audit_solution(problem, result) if info.get('objective') is not None else {}
    write(folder/'independent_audit.json', audit)
    row = dict(label=label, model=problem.model_number, seed=seed, seconds=budget,
               status=info['status'], valid=audit.get('valid', False),
               proven_infeasible=info.get('proven_infeasible',False),
               proven_optimal=info.get('proven_optimal',False), initial_cost=initial_cost,
               wall_time=result.get('wall_time'), source=str(folder),
               iterations=info.get('iterations'), time_to_best=info.get('time_to_best'))
    if not row['valid']:
        return row
    cost = evaluate_comparison_cost(problem, result, str(DATA/'economic.json'), audit=audit)
    write(folder/'comparison_cost.json', cost)
    pool=cost['container_pool_size']
    assignment=result.get('order_batch_assignment',{o.id:'b1' for o in problem.orders})
    departure_w=[]; departure_v=[]; site_count=Counter()
    for trip in result['e1_trips']:
        sites={e['site_id'] for e in trip['events'] if e['kind']=='d'}
        site_count[len(sites)]+=1
        if not sites:
            continue
        orders=[o for o in problem.orders if o.satellite in sites and assignment[o.id]==trip['batch_id']]
        departure_w.append(sum(o.goods_weight+o.count*problem.fleet.container_weight for o in orders)/problem.fleet.e1_weight_cap)
        departure_v.append(sum(o.count*problem.fleet.container_volume for o in orders)/problem.fleet.e1_volume_cap)
    row.update(N=pool, K=result.get('cycle_batch_count',1), total_cost=cost['total'],
        saving_rate=(100-pool)/100, turnover=100/pool,
        D1=audit['distances']['e1'], D2=audit['distances']['e2'],
        trips1=audit['e1_trip_count'], trips2=audit['e2_trip_count'],
        vehicles1=audit['e1_vehicle_count'], vehicles2=audit['e2_vehicle_count'],
        e1_departure_weight_util=statistics.mean(departure_w),
        e1_departure_volume_util=statistics.mean(departure_v),
        e1_delivery_sites_per_trip=dict(site_count), two_cycle_valid=audit['periodic_replay']['valid'],
        cost_components=cost['components'],
        visited_K=sum(bool(r.get('visits')) for r in result.get('count_search',[])),
        configured_K=len(result.get('calendar_config',{}).get('batch_count_candidates',[])))
    return row


def summarize(rows, best, reference):
    write(ROOT/'summary.json', rows)
    cols=sorted({k for r in rows for k in r if k not in ('cost_components','e1_delivery_sites_per_trip')})
    with (PROJECT_ROOT/ROOT/'summary.csv').open('w',encoding='utf-8-sig',newline='') as stream:
        writer=csv.DictWriter(stream,fieldnames=cols,extrasaction='ignore')
        writer.writeheader();writer.writerows(rows)
    groups={}
    for label in dict.fromkeys(r['label'] for r in rows):
        local=[r for r in rows if r['label']==label]; valid=[r for r in local if r['valid']]
        item=dict(runs=len(local),successes=len(valid))
        if valid:
            costs=[r['total_cost'] for r in valid]
            item.update(mean=statistics.mean(costs),sd=statistics.stdev(costs) if len(costs)>1 else None,
                        best=min(costs),worst=max(costs),counts=sorted({r['K'] for r in valid}))
        groups[label]=item
    write(ROOT/'group_summary.json',groups)
    lines=['# 五仓25箱：ALNS系统搜索','',
        '保持算例、成本、模型约束和ALNS源码不变。原生冷启动、已有解启动、固定K集中搜索分开报告。',
        '当前已知387.2903起点来自此前局部合并诊断；它不是本轮ALNS新找到的改进。',
        '完整K域为1…100；固定K=2、3、4仅是额外集中搜索。所有可行解都在原完整域重新审计。','',
        '| 搜索组 | 种子 | 秒数 | 状态 | N | K | 总成本 | 一级趟次 | 一级出仓体积装载率 |',
        '|---|---:|---:|---|---:|---:|---:|---:|---:|']
    for r in rows:
        if r['valid']:
            lines.append(f"| {r['label']} | {r['seed']} | {r['seconds']:.0f} | 可行 | {r['N']} | {r['K']} | {r['total_cost']:.4f} | {r['trips1']} | {r['e1_departure_volume_util']:.2%} |")
        else:
            lines.append(f"| {r['label']} | {r['seed']} | {r['seconds']:.0f} | {r['status']} | — | — | — | — | — |")
    if best is not None:
        a=best['audit'];pool=best['container_pool_size']
        lines+=['','## 当前最好可行解','',
            f"成本{best['info']['objective']:.6f}，箱池{pool}，K={best['cycle_batch_count']}，一级{a['e1_trip_count']}趟，二级{a['e2_trip_count']}趟。",
            '完整方案见best_full_domain.json，来源见best_origin.json。固定K得到的解仍是完整模型的可行解，但不构成完整域最优证明。']
        if reference is not None:
            lines+=[f"本轮M3参考成本{reference['total_cost']:.6f}；上述M8解相对它的成本降低{1-best['info']['objective']/reference['total_cost']:.2%}。"]
    lines+=['','## 口径与限制','',
        '数据为100顾客、5仓、一级25箱/重量500、5辆一级车、25辆二级车。成本为0.1D1+0.05D2+5N+0.02Q，使用合成单位；工时、车辆固定费和趟次固定费为0。',
        'M3原生优化总距离，其共同口径成本由审计重算；M8直接优化成本。M8累计搜索和初解准备投入更多，成本比例不代表等计算预算算法竞赛。',
        '时间预算控制的随机搜索不保证逐位复现。多种子统计仅描述本算例，不能推出跨算例稳定优势。',
        'HEURISTIC_NO_SOLUTION不等于不可行。ALNS所有解均未证明全局最优；最佳K只是已找到解的批次数。',
        '审计包括时间窗、容量、车辆复用、配送回收、容器库存、清洗和两周期重放。输入/源码哈希与执行前快照见protocol.json、source_and_inputs.zip。','',
        '## Material Passport','', '- Origin Skill: academic-research-suite / experiment-agent',
        '- Origin Mode: run','- Origin Date: 2026-10-20','- Verification Status: ANALYZED; independent replay, no reproducibility rerun',
        '- Version Label: l05_m8_systematic_search_v1','']
    (PROJECT_ROOT/ROOT/'report.md').write_text('\n'.join(lines),encoding='utf-8')


def main():
    if (PROJECT_ROOT/ROOT).exists():
        raise FileExistsError(f'Refusing to replace {ROOT}')
    (PROJECT_ROOT/ROOT).mkdir(parents=True)
    cfg8,fc8=load_routing_case(DATA/'M8/case.json',use_pool_config=False)
    full=load_instance(cfg8,fc8)
    best=export_full(full,read(START),str(START));baseline=best['info']['objective']
    write(ROOT/'diagnostic_start_full.json',best)
    write(ROOT/'best_full_domain.json',best)
    write(ROOT/'best_origin.json',dict(origin=str(START),cost=baseline,method='Prior deterministic merge diagnosis, not this ALNS run'))
    input_files={str(p.relative_to(PROJECT_ROOT)):sha(p) for p in (PROJECT_ROOT/DATA).rglob('*') if p.is_file()}
    source={str(p.relative_to(PROJECT_ROOT)):sha(p) for base in ('models','cli','tools','experiments')
            for p in (PROJECT_ROOT/base).rglob('*.py')}
    started=time.time();begun=time.perf_counter();deadline=begun+1800
    write(ROOT/'protocol.json',dict(date='2026-10-20',user_budget='about20-30min',maximum_wall_seconds=1800,
        started_at=started,python=platform.python_version(),platform=platform.platform(),
        phases=dict(cold_full=dict(seeds=[17,42,2026],seconds=180),
                    warm_full=dict(seeds=[17,42,2026],seconds=180,initial='Best audited available after cold stage; common frozen start for all three seeds'),
                    fixed_K=dict(counts=[2,3,4],seed=42,seconds=120),
                    M3_reference=dict(seed=42,seconds=180),
                    final_refinement=dict(seed=99,seconds_up_to=120,initial='Current best audited solution, fixed selected K')),
        nominal_main_solver_seconds=1620,optional_final_seconds=120,initial_diagnostic_cost=baseline,
        iterations_per_run=1000000000,model_or_ALNS_changes=False,exact_solver_calls=0,
        input_sha256=input_files,source_sha256=source,script=__file__,
        git_head=subprocess.check_output(['git','rev-parse','HEAD'],text=True,cwd=PROJECT_ROOT).strip()))
    with zipfile.ZipFile(PROJECT_ROOT/ROOT/'source_and_inputs.zip','x',compression=zipfile.ZIP_DEFLATED) as archive:
        for p in {**input_files,**source}:
            archive.write(PROJECT_ROOT/p,p)
    (PROJECT_ROOT/ROOT/'working_tree.patch').write_bytes(subprocess.check_output(['git','diff','--binary'],cwd=PROJECT_ROOT))
    rows=[];reference=None;incumbents={best['cycle_batch_count']:best}

    def execute(label,seed,seconds,*,count=None,start=None,model=8):
        nonlocal best,reference
        remaining=deadline-time.perf_counter()
        if remaining<seconds+8:
            seconds=min(seconds,max(0,remaining-8))
        if seconds<20:
            write(ROOT/'budget_stop.json',dict(skipped_label=label,seed=seed,remaining=remaining))
            return
        case=deepcopy(cfg8) if model==8 else load_routing_case(DATA/'M3/case.json',use_pool_config=False)[0]
        case.update(seed=seed,alns_time=seconds,alns_iter=1000000000,save_plots=False)
        if count is not None:
            case['joint_batch_options']=dict(case['joint_batch_options'],batch_count_candidates=[count])
        case_path=ROOT/'configs'/f'{label}_{seed}.json'
        initial_cost=None
        if start is not None:
            provisional=load_instance(case,deepcopy(fc8))
            adapted=deepcopy(start)
            attach_calendar_decisions(provisional,adapted,adapted['cycle_batch_count'],adapted['batch_boundaries'])
            checked=audit_solution(provisional,adapted)
            if not checked['valid']:
                raise ValueError(f'Initial solution does not validate under search domain: {checked["errors"]}')
            adapted['audit']=checked;initial_cost=checked['objective']
            start_path=ROOT/'initials'/f'{label}_{seed}.json'
            write(start_path,adapted);case['initial_joint_result']=str(start_path)
        write(case_path,case)
        config,fleet=load_routing_case(case_path,use_pool_config=False)
        problem=load_instance(config,fleet)
        folder=ROOT/label/f'seed_{seed}'
        write(ROOT/'progress.json',dict(current=label,seed=seed,budget=seconds,job_started_at=time.time(),
            elapsed=time.perf_counter()-begun,completed=len(rows),best_cost=best['info']['objective'],
            initial_cost=initial_cost,remaining_wall=deadline-time.perf_counter()))
        print(f'START {label} seed={seed} budget={seconds:.1f}s initial={initial_cost}',flush=True)
        result=run_case(config,fleet,case_path=case_path,output=folder)
        row=metrics(problem,result,label,seed,seconds,folder,initial_cost)
        rows.append(row)
        if row['valid'] and model==8:
            exported=export_full(full,result,str(folder/'result.json'))
            k=exported['cycle_batch_count']
            if k not in incumbents or exported['info']['objective']<incumbents[k]['info']['objective']:
                incumbents[k]=exported
            if exported['info']['objective']<best['info']['objective']-1e-8:
                best=exported
                write(ROOT/'best_full_domain.json',best)
                write(ROOT/'best_origin.json',dict(origin=str(folder/'result.json'),cost=best['info']['objective'],
                    label=label,seed=seed,search_count_domain=list(problem.batch_count_candidates),elapsed=time.perf_counter()-begun))
                print(f'NEW_BEST cost={best["info"]["objective"]:.6f} N={best["container_pool_size"]} K={k}',flush=True)
        if row['valid'] and model==3:
            reference=row
        summarize(rows,best,reference)
        print('RESULT '+json.dumps(row,ensure_ascii=False),flush=True)

    for seed in (17,42,2026):
        execute('M8_full_cold',seed,180)
    common_start=deepcopy(best)
    write(ROOT/'common_warm_start.json',common_start)
    for seed in (17,42,2026):
        execute('M8_full_warm',seed,180,start=common_start)
    for count in (2,3,4):
        execute(f'M8_fixed_K{count}',42,120,count=count,start=incumbents.get(count))
    execute('M3_reference',42,180,model=3)
    execute('M8_best_K_refinement',99,120,count=best['cycle_batch_count'],start=best)
    final=audit_solution(full,best)
    if not final['valid']:
        raise ValueError(f'Final best fails independent replay: {final["errors"]}')
    write(ROOT/'best_independent_audit.json',final)
    changed=[p for p,h in {**input_files,**source}.items() if sha(p)!=h]
    write(ROOT/'integrity.json',dict(passed=not changed,changes=changed,finished_at=time.time(),
        total_wall_seconds=time.perf_counter()-begun,all_valid_results_replayed=all(r.get('two_cycle_valid') for r in rows if r['valid'])))
    if changed:
        raise RuntimeError(f'Input or source changed during execution: {changed}')
    write(ROOT/'progress.json',dict(status='complete',completed=len(rows),best_cost=best['info']['objective'],elapsed=time.perf_counter()-begun))
    summarize(rows,best,reference)
    print(f'COMPLETE best={best["info"]["objective"]:.6f} elapsed={time.perf_counter()-begun:.1f}s',flush=True)


if __name__=='__main__':
    main()
