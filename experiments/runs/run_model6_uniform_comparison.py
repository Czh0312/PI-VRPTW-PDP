"""Reproducible smoke comparison of original M5 and the uniform-cycle M6 basic model."""
from __future__ import annotations

import argparse
import csv
from datetime import datetime
from pathlib import Path
import json
import math
import shutil

from configuration.loader import PROJECT_ROOT, load_routing_case, resolve_project_path
from models.routing.common.cycle import load_instance
from .run_model6_comparison import execute_jobs, freeze_case, snapshot_source, write_json


def summarize(out,rows,records):
    cases=[]
    for record in records:
        valid=[r for r in rows if r['case']==record['name'] and r['valid']]
        m5=min((r for r in valid if r['treatment']=='M5_distance'),
               key=lambda r:(r['minimum_pool_for_schedule'],r['distance']),default=None)
        m6=min((r for r in valid if r['treatment']=='M6_uniform'),key=lambda r:r['cost'],default=None)
        item=dict(case=record['name'],total_container_uses=record['total_container_uses'],
                  m5=None if m5 is None else dict(run=m5['run'],required_boxes=m5['minimum_pool_for_schedule'],
                                                 delivery_batches=m5['batches'],distance=m5['distance']),m6=None)
        if m6:
            result=json.loads((out/m6['run']/'result.json').read_text())
            minutes=lambda ticks:ticks*result['time_tick_seconds']/60
            item['m6']=dict(run=m6['run'],boxes=m6['pool'],cycle_batches=result['cycle_batch_count'],
                           delivery_batches=result['delivery_batch_count'],recovery_rounds=result['actual_recovery_round_count'],
                           batch_minutes=minutes(result['batch_duration_ticks']),
                           first_minutes=minutes(result['shared_cutoff_offset_ticks']),
                           second_minutes=minutes(result['batch_duration_ticks']-result['shared_cutoff_offset_ticks']),
                           cost=m6['cost'],distance=m6['distance'],method=m6['solver'],
                           proven_cost_optimal=m6['proven_optimal'],count_search=result['count_search'])
        cases.append(item)
    payload=dict(material_passport=dict(origin='academic-research-suite/experiment-agent',
                 mode='validate',date=datetime.now().date().isoformat(),status='ANALYZED',version='uniform_cycle_v1'),
                 planned_runs=len(rows),valid_runs=sum(r['valid'] for r in rows),cases=cases,
                 limitations=['Synthetic cost parameters, not observed enterprise prices',
                              'Finite K domain and integer time precision; no proof outside configured domain',
                              'M5 keeps original calendar and distance objective; M6 changes calendar and cost objective',
                              'Short-budget, concurrent smoke study; no convergence or runtime advantage claim',
                              'Minimum-cost pool need not minimize number of boxes',
                              'Best-of-observed selection is descriptive, not statistical inference'])
    write_json(out/'uniform_summary.json',payload)
    lines=['# 模型6基础版：均匀分批、统一分界', '',
           'M6在整个原周期上优化K，各批等长，并共用一个相对分界点。原订单数量、顾客时间窗、道路、车队与清洗时长保持不变。第二阶段仍可回收空箱。', '',
           'M5保留原时间表和距离目标；这里比较已审计方案的箱数，不宣称箱数全局最小。M6成本单位为synthetic_units，参数尚未实证校准。', '',
           '| 算例 | 需求箱次 | M5时间表需箱数/配送批次 | M6箱数 | M6周期批次K | 每批第一窗/第二窗（分钟） | M6配送批次/回运轮数 | 成本最优已证明 |',
           '| --- | ---: | --- | ---: | ---: | --- | --- | --- |']
    for c in cases:
        m5,m6=c['m5'],c['m6']
        original=f"{m5['required_boxes']}/{m5['delivery_batches']}" if m5 else '—'
        if not m6:
            lines.append(f"| {c['case']} | {c['total_container_uses']} | {original} | — | — | — | — | False |")
            continue
        lines.append(f"| {c['case']} | {c['total_container_uses']} | {original} | {m6['boxes']} | {m6['cycle_batches']} | "
                     f"{m6['first_minutes']:.3f}/{m6['second_minutes']:.3f} | {m6['delivery_batches']}/{m6['recovery_rounds']} | {m6['proven_cost_optimal']} |")
    lines += ['',f"有效运行 {payload['valid_runs']}/{len(rows)}。每个K的状态、时间预算和成本下界见原始result.json中的count_search；未找到解与已证明不可行分开报告。", '',
              '所有有效方案独立重算成本，并连续重放两个周期验证箱子守恒、回运、清洗和车辆复用。输入与源码哈希、各次失败及超时均保留。', '',
              '这是短预算工程验证。不同原日历与新日历、距离目标与成本目标同时变化，不能把全部差异归因于均匀分批这一项，也不宣称统计显著或真实经济节约。', '',
              '11项解释检查：Simpson、生态、Berkson、碰撞变量、基准率、均值回归、幸存者、多重搜索、分叉路径、相关与因果、反向因果。逐例展示且保留所有失败；最佳观察值仅作描述；未进行群体估计、显著性或因果推断。', '',
              'Material Passport：academic-research-suite/experiment-agent；validate；ANALYZED；uniform_cycle_v1。优化随机运行未重复复现实验，独立物理重放通过不等同于随机搜索完全复现。']
    (out/'uniform_report.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    return payload


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest',required=True)
    parser.add_argument('--output')
    parser.add_argument('--exact-time',type=float,default=30)
    parser.add_argument('--time',type=float,default=10)
    parser.add_argument('--iterations',type=int,default=5000)
    parser.add_argument('--seeds',type=int,nargs='+',default=[42])
    parser.add_argument('--workers',type=int,default=1)
    parser.add_argument('--timeout-overhead',type=float,default=20)
    args=parser.parse_args(argv)
    if (any(not math.isfinite(v) or v<=0 for v in (args.exact_time,args.time,args.timeout_overhead))
            or args.iterations<0 or not 1<=args.workers<=8 or any(not 0<=s<=2_000_000_000 for s in args.seeds)):
        parser.error('Invalid budgets, worker count or seed')
    args.seeds=list(dict.fromkeys(args.seeds))
    manifest_path=resolve_project_path(args.manifest)
    manifest=json.loads(manifest_path.read_text())
    if manifest.get('schedule_mode')!='uniform_cycle' or not manifest.get('cases'):
        raise ValueError('Require a nonempty uniform-cycle suite manifest')
    out=resolve_project_path(args.output).resolve() if args.output else PROJECT_ROOT/'output/model6_comparison'/datetime.now().strftime('%Y%m%dT%H%M%S_%f_uniform')
    if out.exists() and any(out.iterdir()):
        raise ValueError(f'Refusing to overwrite nonempty output directory: {out}')
    for folder in ('inputs','cases','logs'):
        (out/folder).mkdir(parents=True,exist_ok=True)
    shutil.copy2(manifest_path,out/'inputs/manifest.json')
    hashes={}; jobs=[]
    for index,record in enumerate(manifest['cases']):
        for label,key in [('M5_distance','m5_case'),('M6_uniform','m6_case')]:
            config,evidence=freeze_case(record[key],out,f'{index}_{label}')
            hashes.update(evidence)
            if label=='M6_uniform':
                problem=load_instance(*load_routing_case(record[key],use_pool_config=False))
                if not getattr(problem,'uniform_cycle',False):
                    raise ValueError('Every M6 case must use uniform_cycle')
            config.update(save_plots=False,alns_iter=args.iterations)
            path=out/'cases'/f'{index}_{label}.json'; write_json(path,config)
            for method,seed,budget in [('exact',args.seeds[0],args.exact_time)]+[('alns',s,args.time) for s in args.seeds]:
                jobs.append(dict(case=record['name'],treatment=label,solver=method,seed=seed,budget=budget,
                                 run=f'{index:02d}_{label}_{method}_{seed}',case_path=str(path)))
    write_json(out/'experiment.json',dict(schema_version=1,budgets=vars(args),jobs=jobs,
               input_hashes=hashes,source_hashes=snapshot_source(out),source_manifest=str(manifest_path)))
    rows=execute_jobs(jobs,out,args)
    for row in rows:
        if row['treatment']=='M6_uniform' and row['solver']=='alns' and row['valid']:
            exact=next(r for r in rows if r['case']==row['case'] and r['treatment']=='M6_uniform' and r['solver']=='exact')
            if exact['proven_optimal'] and row['cost']<exact['cost']-1e-6:
                raise RuntimeError('ALNS beats a claimed global exact optimum')
    write_json(out/'rows.json',rows)
    with (out/'rows.csv').open('w',newline='',encoding='utf-8') as handle:
        writer=csv.DictWriter(handle,fieldnames=list(rows[0])); writer.writeheader(); writer.writerows(rows)
    summary=summarize(out,rows,manifest['cases'])
    print(f"Saved {summary['valid_runs']}/{summary['planned_runs']} valid runs: {out}",flush=True)
    return out


if __name__=='__main__':
    main()
