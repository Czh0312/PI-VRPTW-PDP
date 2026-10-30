"""Frozen L05 experiment: disposable boxes at 2/use versus rentals at 1/pool/cycle.

Only scenario prices/configurations change; routing algorithms are unchanged.
Prepare first with --prepare; then execute the declared ALNS jobs with --run.
"""
from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
import csv
import hashlib
import json
from pathlib import Path
import platform
import shutil
import statistics
import subprocess
import time
import zipfile

from tools.configuration.loader import PROJECT_ROOT, load_routing_case
from cli.multibatch import run_case
from models.common.cycle import load_instance
from models.common.cycle.audit import audit_solution
from models.common.cycle.calendar import attach_calendar_decisions
from models.common.cycle.joint import lift_result
from models.cost.multibatch import evaluate_cycle_cost

BASE = Path('data/mechanism_l05_5sat_balanced25_v1')
DATA = Path('data/mechanism_l05_m1_m8_rental_v1')
OUT = Path('output/l05_m1_m8_rental_20261021')
OLD = Path('output/l05_m8_systematic_search_20261020')
SEEDS = (17, 42, 2026)


def read(path):
    return json.loads((PROJECT_ROOT / path).read_text(encoding='utf-8'))


def write(path, value):
    path = PROJECT_ROOT / path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)+'\n', encoding='utf-8')


def sha(path):
    return hashlib.sha256((PROJECT_ROOT / path).read_bytes()).hexdigest()


def instance(path):
    config, fleet = load_routing_case(path, use_pool_config=False)
    return config, fleet, load_instance(config, fleet)


def lift_existing(parent, raw, *, assignment, pool, bounds, cutoffs, provenance):
    """Reprice existing physical routes through the normal joint lift and audit."""
    child = parent.fixed_calendar(len(bounds)-1, bounds)
    result = lift_result(child, raw, assignment, pool, 'alns', cutoffs)
    if not result.get('audit', {}).get('valid'):
        raise ValueError(f'Initial physical lift failed: {result.get("audit")}')
    attach_calendar_decisions(parent, result, len(bounds)-1, bounds)
    result['info'].update(status='FEASIBLE', proven_optimal=False, proven_infeasible=False)
    result['scenario_provenance'] = provenance
    audit = audit_solution(parent, result)
    if not audit['valid']:
        raise ValueError(f'Initial full-domain audit failed: {audit["errors"]}')
    result['audit'] = audit
    return result


def prepare():
    for path in (DATA, OUT):
        if (PROJECT_ROOT/path).exists():
            raise FileExistsError(f'Refusing to overwrite {path}')
    (PROJECT_ROOT/OUT).mkdir(parents=True)
    (PROJECT_ROOT/DATA).mkdir(parents=True)
    for name in ('network.csv', 'fleet.json', 'reference_orders.json', 'reference_policy.json'):
        shutil.copy2(PROJECT_ROOT/BASE/name, PROJECT_ROOT/DATA/name)
    economic = read(BASE/'economic.json')
    economic.update(description='M1: 2 per disposable box use; M8: 1 per physical rented box per complete 780-minute cycle. Other rates unchanged.',
        evaluation_period='one complete 780-minute operating cycle (not one delivery batch)',
        pool_cost_basis='rental', container_pool_cost_per_period=1,
        pool_charge_definition='Recurring rental N*1 each complete operating cycle; no acquisition investment, deposit, setup or replacement acquisition charge.',
        container_price=0, rental_includes=[], single_use_package_price=2,
        single_use_disposal_cost_per_package=0,
        price_basis='User-specified synthetic paper price 2/use and rental price 1/physical box/cycle; no empirical calibration')
    write(DATA/'economic.json', economic)
    for model, source in ((1, 'M3'), (8, 'M8')):
        folder = DATA/f'M{model}'
        write(folder/'orders.json', read(BASE/source/'orders.json'))
        write(folder/'policy.json', read(BASE/source/'policy.json'))
        config = read(BASE/source/'case.json')
        config.update(model=model, case_name=f'{DATA.name}_M{model}',
            data=str(DATA/'network.csv'), routing_config=str(DATA/'fleet.json'),
            economic_config=str(DATA/'economic.json'), orders=str(folder/'orders.json'),
            operating_policy=str(folder/'policy.json'), dataset_version=DATA.name,
            alns_time=120, alns_iter=1000000000)
        write(folder/'case.json', config)
    _, _, paper = instance(DATA/'M1/case.json')
    _, _, rental = instance(DATA/'M8/case.json')
    # Compare physical order attributes, deliberately excluding model-specific batch labels.
    fields = ('id', 'customer', 'satellite', 'count', 'goods_weight', 'delivery_window',
              'pickup_window', 'service_time', 'min_use_time', 'release_time')
    assert [{k:getattr(o,k) for k in fields} for o in paper.orders] == [
        {k:getattr(o,k) for k in fields} for o in rental.orders]
    assert paper.total == rental.total == 100
    assert vars(paper.fleet) == vars(rental.fleet)
    initial = {}
    old3_path = OLD/'M3_reference/seed_42/result.json'
    old3 = read(old3_path)
    p = rental.operating_policy
    initial['K1_prior_M3'] = lift_existing(rental, old3,
        assignment={o.id:'b1' for o in rental.orders}, pool=100,
        bounds=[p.cycle_start, p.cycle_end], cutoffs={'b1':old3['batches'][0]['cutoff']},
        provenance=dict(source=str(old3_path), method='Existing M3 ALNS physical routes repriced and lifted to M8 K=1; not a new ALNS discovery'))
    old8_path = OLD/'best_full_domain.json'
    old8 = read(old8_path)
    initial['K3_prior_M8'] = lift_existing(rental, old8,
        assignment=old8['order_batch_assignment'], pool=old8['container_pool_size'],
        bounds=old8['batch_boundaries'], cutoffs=old8['batch_cutoffs'],
        provenance=dict(source=str(old8_path), method='Existing M8 ALNS routes repriced at rental 1; not a new ALNS discovery'))
    for name, result in initial.items():
        write(OUT/'repriced_initials'/f'{name}.json', result)
    write(OUT/'progress.json', dict(status='PREPARED'))
    original = {str(path.relative_to(PROJECT_ROOT)):sha(path) for path in (PROJECT_ROOT/BASE).rglob('*') if path.is_file()}
    inputs = {str(path.relative_to(PROJECT_ROOT)):sha(path) for path in (PROJECT_ROOT/DATA).rglob('*') if path.is_file()}
    sources = {str(path.relative_to(PROJECT_ROOT)):sha(path) for base in ('models','tools','cli','experiments')
               for path in (PROJECT_ROOT/base).rglob('*.py')}
    write(OUT/'protocol.json', dict(date='2026-10-21', mode='continuous',
        skill='academic-research-suite / experiment-agent / run',
        source_data=str(BASE), scenario_data=str(DATA),
        paper_price_per_use=2, rental_per_physical_box_per_complete_cycle=1,
        upfront_costs=dict(acquisition=0, deposit=0, setup=0), rental_includes=[],
        separate_cleaning_and_wear_per_use=.01, initial_pool_investment=0,
        jobs=[dict(label=label,model=model,seed=seed,seconds=120)
              for label,model,seeds in [('M1_cold',1,SEEDS),('M8_cold',8,SEEDS),
                                       ('M8_warm',8,SEEDS),('M8_fixed_K3',8,(42,))]
              for seed in seeds],
        main_solver_seconds=1200, hard_timeout_seconds=1320,
        full_K_domain=list(rental.batch_count_candidates),
        common_warm_start='Best audited available after cold stage, including repriced prior K1/K3; frozen across three warm seeds',
        fixed_K3_start='Repriced prior three-batch solution or better K3 endpoint from main runs',
        unchanged=['coordinates','order quantities','delivery/pickup windows','release times',
                   'customer-site assignments','fleet capacities/counts','cleaning duration','routing algorithms'],
        forward_packaging_assumption='Identical packaging weight/volume in M1 and M8 to isolate the requested economic comparison',
        repetition_assumption='Same demand, service windows, routes and recurring rates each complete operating cycle; no discounting, inflation, deposit or setup cost',
        cost_limitations='Other earlier synthetic rates retained, including zero time/vehicle/trip charges; M1 optimizes total distance, M8 optimizes cost.',
        repriced_initials={name:dict(cost=r['info']['objective'],N=r['container_pool_size'],K=r['cycle_batch_count']) for name,r in initial.items()},
        input_sha256=inputs, original_input_sha256=original, source_sha256=sources,
        python=platform.python_version(), git_head=subprocess.check_output(['git','rev-parse','HEAD'],cwd=PROJECT_ROOT,text=True).strip()))
    with zipfile.ZipFile(PROJECT_ROOT/OUT/'source_and_inputs.zip','x',compression=zipfile.ZIP_DEFLATED) as archive:
        for path in {**inputs, **sources}:
            archive.write(PROJECT_ROOT/path,path)
    (PROJECT_ROOT/OUT/'working_tree.patch').write_bytes(subprocess.check_output(['git','diff','--binary'],cwd=PROJECT_ROOT))
    print(json.dumps(dict(prepared=True,initials={name:r['info']['objective'] for name,r in initial.items()}),ensure_ascii=False),flush=True)


def verify(protocol):
    changed=[path for section in ('input_sha256','original_input_sha256','source_sha256')
             for path,digest in protocol[section].items() if sha(path)!=digest]
    return dict(passed=not changed,changes=changed)


def price_metrics(problem, result, label, seed, seconds, folder, initial):
    row=dict(label=label,model=problem.model_number,seed=seed,seconds=seconds,
             status=result['info']['status'],source=str(folder),
             iterations=result['info'].get('iterations'),
             initial_cost=initial['info']['objective'] if initial else None,
             proven_optimal=bool(result['info'].get('proven_optimal')))
    if result['info'].get('objective') is None:
        return dict(row,valid=False)
    audit=audit_solution(problem,result)
    write(folder/'independent_audit.json',audit)
    if not audit['valid']:
        return dict(row,valid=False,audit_errors=audit['errors'])
    economics=read(DATA/'economic.json')
    if problem.model_number==1:
        cost=evaluate_cycle_cost(problem,result,economics)
        parts=cost['components'];total=cost['period_operating_cost'];pool=None
        assert economics['e1_trip_cost']==economics['e2_trip_cost']==0
    else:
        pool=result['container_pool_size'];cost=problem.cost_profile.evaluate(audit,pool)
        parts=cost['components'];total=cost['total']
    assert cost['initial_pool_investment']==0
    write(folder/'priced_cost.json',cost)
    boxes=[]
    for trip in result['e1_trips']:
        qty=sum(e['quantity'] for e in trip['events'] if e['kind']=='d')
        if qty:boxes.append(qty*problem.fleet.container_volume/problem.fleet.e1_volume_cap)
    return dict(row,valid=True,total_cost=total,components=parts,initial_pool_investment=0,
        N=pool,K=result.get('cycle_batch_count',1),D1=audit['distances']['e1'],D2=audit['distances']['e2'],
        E1_trips=audit['e1_trip_count'],E2_trips=audit['e2_trip_count'],
        E1_vehicles=audit['e1_vehicle_count'],E2_vehicles=audit['e2_vehicle_count'],
        e1_departure_volume_util=statistics.mean(boxes),two_cycle_valid=audit['periodic_replay']['valid'])


def summarize(rows, best, started=None):
    write(OUT/'summary.json',rows)
    fields=sorted({k for r in rows for k in r if k not in ('components','audit_errors')})
    with (PROJECT_ROOT/OUT/'summary.csv').open('w',encoding='utf-8-sig',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=fields,extrasaction='ignore');writer.writeheader();writer.writerows(rows)
    groups={}
    for label in dict.fromkeys(r['label'] for r in rows):
        local=[r for r in rows if r['label']==label];valid=[r for r in local if r['valid']]
        item=dict(runs=len(local),successes=len(valid))
        if valid:
            values=[r['total_cost'] for r in valid]
            item.update(best=min(values),mean=statistics.mean(values),
                sd=statistics.stdev(values) if len(values)>1 else None,
                counts=sorted({r['K'] for r in valid}))
        groups[label]=item
    write(OUT/'group_summary.json',groups)
    pairs=[]
    for label in ('M8_cold','M8_warm'):
        for seed in SEEDS:
            a=next((r for r in rows if r['label']=='M1_cold' and r['seed']==seed and r['valid']),None)
            b=next((r for r in rows if r['label']==label and r['seed']==seed and r['valid']),None)
            if a and b:
                saving=a['total_cost']-b['total_cost']
                pairs.append(dict(M8_group=label,seed=seed,M1_per_cycle=a['total_cost'],
                    M8_per_cycle=b['total_cost'],saving_per_cycle=saving,
                    first_strictly_cheaper_cycle=1 if saving>1e-9 else None,
                    initial_investment_difference=0))
    write(OUT/'paired_comparison.json',pairs)
    paper=[r for r in rows if r['model']==1 and r['valid']]
    if paper and best is not None:
        a=min(paper,key=lambda r:r['total_cost']);b=best['info']['objective']
        parts=best['cost_components'];saving=a['total_cost']-b
        timeline=[dict(cycle=h,M1_total=h*a['total_cost'],M8_total=h*b,net_saving=h*saving)
                  for h in range(1,31)]
        comparison=dict(M1=a,M8_cost=b,M8_components=parts,N=best['container_pool_size'],
            K=best['cycle_batch_count'],initial_investment_M1=0,initial_investment_M8=0,
            saving_per_cycle=saving,cost_reduction=saving/a['total_cost'],
            first_strictly_cheaper_cycle=1 if saving>1e-9 else None,
            no_traditional_capital_payback='Both schemes have zero specified upfront investment; lower recurring cost is cheaper from cycle 1, otherwise repetition cannot create a crossover.',
            hypothetical_setup_cost_payback_formula='For extra setup cost F>0, first integer no-more-expensive cycle is ceil(F/saving_per_cycle), if saving_per_cycle>0.',
            timeline=timeline,selection_scope='Best audited endpoints plus explicitly recorded repriced prior feasible solutions; not an equal-total-compute algorithm comparison')
        write(OUT/'best_comparison.json',comparison)
    lines=['# L05：模型1纸箱2/次与模型8租箱1/周期','',
        '## Material Passport','', '- Origin Skill: academic-research-suite / experiment-agent',
        '- Origin Mode: run','- Origin Date: 2026-10-21','- Verification Status: ANALYZED; independent audits and two-cycle replay; no independent stochastic reproduction rerun',
        '- Version Label: l05_m1_m8_rental_v1','',
        '100顾客、100箱次、5仓；一级25箱/重量500、5辆一级车、25辆二级车。沿用原坐标、订单到达/服务时间、仓库分配和算法。纸箱与循环箱正向重量/体积暂取相同。','',
        '纸箱每次使用购买2；租金每个完整780分钟运营周期按实体箱池N×1收取，每期重新收。每个配送批次不另收一遍整池租金。没有购箱投资、押金或改造费。清洗0.01/回收箱次、磨损0.01/使用箱次单列，租金暂不包含两者；纸箱处置费0。运输系数仍为0.1D1+0.05D2，车辆占用、车辆和趟次固定费为0。','',
        '| 组别 | 种子 | 秒数 | 状态 | N | K | 每周期总成本 | 一级趟次 |',
        '|---|---:|---:|---|---:|---:|---:|---:|']
    for row in rows:
        if row['valid']:
            lines.append(f"| {row['label']} | {row['seed']} | {row['seconds']} | 可行 | {row['N'] if row['N'] is not None else '—'} | {row['K']} | {row['total_cost']:.6f} | {row['E1_trips']} |")
        else:lines.append(f"| {row['label']} | {row['seed']} | {row['seconds']} | {row['status']} | — | — | — | — |")
    if paper and best is not None:
        lines+=['','## 最好可行方案的条件成本比较','',
            f"模型1每周期{a['total_cost']:.6f}；模型8每周期{b:.6f}，N={best['container_pool_size']}、K={best['cycle_batch_count']}。每周期节省{saving:.6f}（{saving/a['total_cost']:.2%}）。",
            '没有前期投入，不能把每期租金当成一次购箱费用反复累计。每期更便宜则第一期起节省；每期更贵则重复运行不会自动回本。逐期累计见best_comparison.json。']
    lines+=['','## 范围与证据','',
        '所有可行结果独立审计，检查容量、时间窗、车辆复用与连续两周期；模型8另外检查回收、清洗与箱池闭环。模型1不承担容器回收清洗。',
        '模型1原生优化距离，纸箱费用因总需求固定而为常数；共同口径仍按两级不同距离系数重算。模型8直接优化成本。冷启动、共同可行解启动和固定三批分开记录。',
        '原有M3单批和M8三批路线重新计价，只作为既有可行候选，不冒充本轮ALNS新发现；见repriced_initials与best_origin.json。模型8投入更多总计算和已有解准备，不是等总预算算法竞赛。',
        '完整K域1…100；固定K=3只是机制对照。更低租金可能改变最佳箱池和批次数，不能强制保留原先35箱三批作为模型8结果。',
        '所有方案均为ALNS可行解，没有全局最优证明。重复周期采用相同需求、路线与价格；没有折现、通胀或额外押金/改造投入。租赁条件为用户指定合成情景，不作为真实市场报价。',
        '输入和源码哈希见protocol.json，结束核验见integrity.json。每次运行自带独立结果、成本和审计。','']
    (PROJECT_ROOT/OUT/'report.md').write_text('\n'.join(lines),encoding='utf-8')


def run():
    protocol=read(OUT/'protocol.json')
    if read(OUT/'progress.json')['status']!='PREPARED':
        raise ValueError('This declared experiment has already started; refusing an implicit retry')
    integrity=verify(protocol)
    if not integrity['passed']:raise ValueError(integrity)
    rows=[];started=time.perf_counter()
    _,_,full=instance(DATA/'M8/case.json')
    starts={name:read(OUT/'repriced_initials'/f'{name}.json') for name in protocol['repriced_initials']}
    best_name,best=min(starts.items(),key=lambda item:item[1]['info']['objective'])
    write(OUT/'best_M8.json',best)
    write(OUT/'best_origin.json',dict(source=str(OUT/'repriced_initials'/f'{best_name}.json'),method='Repriced prior feasible solution',cost=best['info']['objective']))
    best3=starts['K3_prior_M8']
    common=None
    for job in protocol['jobs']:
        label,model,seed=job['label'],job['model'],job['seed']
        config,fleet,_=instance(DATA/f'M{model}/case.json')
        config.update(seed=seed,alns_time=job['seconds'],alns_iter=1000000000)
        initial=None
        if label=='M8_warm':
            if common is None:
                common=deepcopy(best);write(OUT/'common_warm_start.json',common)
            initial=deepcopy(common)
        if label=='M8_fixed_K3':
            config['joint_batch_options']['batch_count_candidates']=[3]
            initial=deepcopy(best3)
            parent=load_instance(config,deepcopy(fleet))
            attach_calendar_decisions(parent,initial,3,initial['batch_boundaries'])
        if initial is not None:
            path=OUT/'initials'/f'{label}_{seed}.json';write(path,initial)
            config['initial_joint_result']=str(path)
        path=OUT/'configs'/f'{label}_{seed}.json';write(path,config)
        config,fleet,problem=instance(path)
        if initial is not None:
            check=audit_solution(problem,initial)
            if not check['valid']:raise ValueError(check['errors'])
        folder=OUT/label/f'seed_{seed}'
        write(OUT/'progress.json',dict(status='RUNNING',current=label,seed=seed,completed=len(rows),
            elapsed=time.perf_counter()-started,job_started_at=time.time(),job_seconds=job['seconds'],best_cost=best['info']['objective']))
        print(f'START {label} seed={seed} seconds={job["seconds"]} initial={initial["info"]["objective"] if initial else None}',flush=True)
        result=run_case(config,fleet,case_path=path,output=folder)
        row=price_metrics(problem,result,label,seed,job['seconds'],folder,initial);rows.append(row)
        if row['valid'] and model==8:
            exported=deepcopy(result)
            attach_calendar_decisions(full,exported,exported['cycle_batch_count'],exported['batch_boundaries'])
            checked=audit_solution(full,exported)
            if not checked['valid']:raise ValueError(checked['errors'])
            exported['audit']=checked
            if exported['cycle_batch_count']==3 and row['total_cost']<best3['info']['objective']:
                best3=exported
            if row['total_cost']<best['info']['objective']-1e-8:
                best=exported;write(OUT/'best_M8.json',best)
                write(OUT/'best_origin.json',dict(source=str(folder/'result.json'),label=label,seed=seed,
                    search_K_domain=list(problem.batch_count_candidates),cost=row['total_cost']))
                print(f'NEW_BEST {row["total_cost"]:.6f} N={row["N"]} K={row["K"]}',flush=True)
        if row['valid'] and model==1:
            old=[r for r in rows[:-1] if r['model']==1 and r['valid']]
            if not old or row['total_cost']<min(r['total_cost'] for r in old):
                write(OUT/'best_M1.json',result)
        summarize(rows,best)
        print('RESULT '+json.dumps(row,ensure_ascii=False),flush=True)
    final=audit_solution(full,best)
    assert final['valid'] and final['periodic_replay']['valid']
    write(OUT/'best_M8_independent_audit.json',final)
    paper_cfg,paper_fleet,paper=instance(DATA/'M1/case.json')
    pa=audit_solution(paper,read(OUT/'best_M1.json'))
    assert pa['valid'] and pa['periodic_replay']['valid']
    write(OUT/'best_M1_independent_audit.json',pa)
    integrity=verify(protocol);integrity.update(total_wall_seconds=time.perf_counter()-started,
        all_feasible_results_two_cycle_valid=all(r['two_cycle_valid'] for r in rows if r['valid']))
    write(OUT/'integrity.json',integrity)
    if not integrity['passed']:raise ValueError(integrity)
    write(OUT/'progress.json',dict(status='COMPLETE',completed=len(rows),elapsed=time.perf_counter()-started,
        best_M8_cost=best['info']['objective']))
    print(f'COMPLETE best_M8={best["info"]["objective"]:.6f} elapsed={time.perf_counter()-started:.1f}',flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    group=parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--prepare',action='store_true');group.add_argument('--run',action='store_true')
    args=parser.parse_args()
    prepare() if args.prepare else run()
