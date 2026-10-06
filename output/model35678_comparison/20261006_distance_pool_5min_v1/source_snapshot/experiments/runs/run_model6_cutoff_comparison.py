"""Matched fixed/variable-cutoff experiment; shared audited prior incumbents.

Each M6 ALNS arm gets the identical feasible starting schedule and the same
additional search budget. Historical initialization is recorded separately;
the independent exact solvers receive no heuristic start.
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

from configuration.loader import PROJECT_ROOT,load_routing_case,resolve_project_path
from models.routing.common.cycle import load_instance
from models.routing.common.cycle.audit import audit_solution
from models.routing.common.cycle.joint import lift_result
from experiments.runs.run_model6_comparison import freeze_case,snapshot_source,execute_jobs,write_json


def prior_incumbent(problem,record,study):
    """Rebase/audit old M5 and fixed-calendar M6 schedules under matched data.

    M5 is charged at its replayed required pool, not its preallocated pool.
    No result is accepted from a different time calendar or different physics.
    """
    if study is None:
        return None,[]
    root=resolve_project_path(study)
    rows=json.loads((root/'rows.json').read_text())
    names={record['name'],record.get('source_case_id')}
    evidence=[]; best=None
    for row in rows:
        if row['case'] not in names or not row['valid']:
            continue
        path=root/row['run']/'result.json'
        raw=json.loads(path.read_text())
        if raw.get('cutoff_mode','fixed')!='fixed':
            continue
        if raw.get('model')==6:
            assignment=raw['order_batch_assignment']; pool=raw['container_pool_size']
        else:
            assignment={o.id:o.batch for o in problem.orders}
            projected=problem.project(assignment,problem.total)
            check=audit_solution(projected,{**raw,'model':projected.model_number,
                                'info':{**raw['info'],'objective':None}})
            if not check['valid']:
                raise ValueError(f'Prior M5 schedule does not match current inputs: {path}')
            pool=max(problem.pool_min,check['minimum_pool_for_schedule'])
        copied=deepcopy(raw)
        copied['info']=dict(status='FEASIBLE',proven_optimal=False,proven_infeasible=False,
                            objective=None,best_bound=None,mip_gap=None,proof_scope=None)
        candidate=lift_result(problem,copied,assignment,pool,'alns')
        if not candidate['audit']['valid']:
            raise ValueError(f'Prior incumbent fails matched replay: {path}: {candidate["audit"]["errors"]}')
        evidence.append(dict(run=row['run'],path=str(path.resolve()),
                            sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                            recomputed_cost=candidate['info']['objective'],pool=pool,
                            source_method=row['solver'],source_budget=row['budget']))
        if best is None or candidate['info']['objective']<best['info']['objective']:
            best=candidate
            best['initial_reference_run']=row['run']
    return best,evidence


def summarize(out,rows,records):
    comparisons=[]
    for record in records:
        group=[r for r in rows if r['case']==record['name'] and r['valid']]
        chosen={label:min((r for r in group if r['treatment']==label),key=lambda r:r['cost'],default=None)
                for label in ('M6_fixed_windows','M6_joint')}
        m5=min((r for r in group if r['treatment']=='M5_distance'),
               key=lambda r:(r['minimum_pool_for_schedule'],r['distance']),default=None)
        entry=dict(case=record['name'],source_kind=record.get('source_kind'),
                   orders=record.get('orders',group[0]['order_count'] if group else None),
                   total_container_uses=record.get('total_container_uses',group[0]['total_container_uses'] if group else None),
                   m5_required_pool=m5['minimum_pool_for_schedule'] if m5 else None,
                   m5_delivery_batches=m5['batches'] if m5 else None,
                   complete=all(chosen.values()),fixed=None,optimized=None)
        for label,key in [('M6_fixed_windows','fixed'),('M6_joint','optimized')]:
            row=chosen[label]
            if row is None:
                continue
            result=json.loads((out/row['run']/'result.json').read_text())
            entry[key]=dict(run=row['run'],cost=row['cost'],pool=row['pool'],delivery_batches=row['batches'],
                            distance=row['distance'],method=row['solver'],cost_optimal_proven=row['proven_optimal'],
                            windows=[dict(batch=b['id'],start=b['start']*result['time_tick_seconds']/60,
                                           cutoff=b['cutoff']*result['time_tick_seconds']/60,
                                           end=b['end']*result['time_tick_seconds']/60,
                                           first=(b['cutoff']-b['start'])*result['time_tick_seconds']/60,
                                           second=(b['end']-b['cutoff'])*result['time_tick_seconds']/60)
                                     for b in result['batches']])
        if entry['complete']:
            entry['cost_saved']=entry['fixed']['cost']-entry['optimized']['cost']
            entry['boxes_saved']=entry['fixed']['pool']-entry['optimized']['pool']
            entry['batches_saved']=entry['fixed']['delivery_batches']-entry['optimized']['delivery_batches']
        comparisons.append(entry)
    complete=[c for c in comparisons if c['complete']]
    stats=dict(planned_cases=len(records),complete_comparisons=len(complete),attempted_runs=len(rows),
               valid_runs=sum(r['valid'] for r in rows),
               variable_cost_lower=sum(c['cost_saved']>1e-6 for c in complete),
               variable_cost_equal=sum(abs(c['cost_saved'])<=1e-6 for c in complete),
               variable_cost_higher=sum(c['cost_saved']< -1e-6 for c in complete),
               variable_pool_lower=sum(c['boxes_saved']>0 for c in complete),
               variable_pool_equal=sum(c['boxes_saved']==0 for c in complete),
               variable_pool_higher=sum(c['boxes_saved']<0 for c in complete))
    write_json(out/'cutoff_summary.json',dict(statistics=stats,cases=comparisons))
    lines=['# 模型6：固定截点与优化截点', '',
           '批次起止、顾客预约窗口、数量、网络、车队、清洗时间与费用保持一致。二级车辆在第二阶段仍可回收；截点只决定本轮一级回运资格。', '',
           '两组模型6 ALNS使用相同的已审计历史起点和相同的新增搜索预算。历史起点的来源、费用重算、原预算与哈希见initial_references.json；它们不属于本次新增搜索预算。两个精确求解器独立建模求解，不调用ALNS。', '',
           '所有费用均为未校准的synthetic_units；不代表人民币或实际企业节约。最低成本方案不保证箱数最低。', '',
           '| 算例 | 需求箱次 | M5时间表需箱数/配送批次 | 固定截点M6箱数/配送批次 | 优化截点M6箱数/配送批次 | 测试成本变化（优化−固定） | 优化解成本最优已证明 |',
           '| --- | ---: | --- | --- | --- | ---: | --- |']
    for c in comparisons:
        if not c['complete']:
            lines.append(f'| {c["case"]} | {c["total_container_uses"]} | — | — | — | 缺少有效对照 | False |')
            continue
        f,v=c['fixed'],c['optimized']
        lines.append(f'| {c["case"]} | {c["total_container_uses"]} | {c["m5_required_pool"]}/{c["m5_delivery_batches"]} | '
                     f'{f["pool"]}/{f["delivery_batches"]} | {v["pool"]}/{v["delivery_batches"]} | {-c["cost_saved"]:.3f} | {v["cost_optimal_proven"]} |')
    for c in complete:
        f,v=c['fixed'],c['optimized']
        lines.extend(['',f'### {c["case"]} 的第一窗 / 第二窗（分钟）', '',
                      '固定：'+', '.join(f'{w["batch"]}={w["first"]:g}/{w["second"]:g}' for w in f['windows']), '',
                      '优化：'+', '.join(f'{w["batch"]}={w["first"]:.3f}/{w["second"]:.3f}' for w in v['windows']), ''])
    lines += ['',f'有效结果 {stats["valid_runs"]}/{stats["attempted_runs"]}；优化截点成本降低/相同/升高：'
               f'{stats["variable_cost_lower"]}/{stats["variable_cost_equal"]}/{stats["variable_cost_higher"]}。', '',
              '原始结果、独立审计和成本分项在各运行目录；失败与超时完整保留在rows.csv。M5箱数为本次观察到的时间表最小需要值，不是全局箱数最优证明。', '',
              '原固定方案位于可变截点域内时，可变模型的真实最优成本不会更高；限时搜索结果可能更高，须如实报告。']
    (out/'cutoff_report.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    return stats


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest',required=True)
    parser.add_argument('--initial-study')
    parser.add_argument('--time',type=float,default=10)
    parser.add_argument('--exact-time',type=float,default=30)
    parser.add_argument('--iterations',type=int,default=30000)
    parser.add_argument('--seeds',nargs='+',type=int,default=[42,43,44])
    parser.add_argument('--workers',type=int,default=2)
    parser.add_argument('--timeout-overhead',type=float,default=20)
    parser.add_argument('--output',required=True)
    args=parser.parse_args(argv)
    if (any(not math.isfinite(v) or v<=0 for v in (args.time,args.exact_time,args.timeout_overhead))
            or not 1<=args.workers<=8 or args.iterations<1
            or any(not 0<=s<=2_000_000_000 for s in args.seeds)):
        parser.error('Invalid budgets, worker count, iteration count or seeds')
    args.seeds=list(dict.fromkeys(args.seeds))
    manifest_path=resolve_project_path(args.manifest); manifest=json.loads(manifest_path.read_text())
    records=manifest['cases']; out=resolve_project_path(args.output).resolve()
    if out.exists() and any(out.iterdir()):
        raise ValueError(f'Refusing to overwrite nonempty experiment: {out}')
    for folder in ('inputs','cases','logs'):
        (out/folder).mkdir(parents=True,exist_ok=True)
    write_json(out/'inputs/manifest.json',manifest)
    jobs=[]; hashes={}; references=[]
    for index,record in enumerate(records):
        c5,h5=freeze_case(record['m5_case'],out,f'{index}_M5'); hashes.update(h5)
        c6,h6=freeze_case(record['m6_case'],out,f'{index}_M6'); hashes.update(h6)
        p6=load_instance(*load_routing_case(record['m6_case'],use_pool_config=False))
        if not p6.optimize_cutoffs:
            raise ValueError('Cutoff comparison requires explicit variable-cutoff M6 inputs')
        fixed=deepcopy(c6)
        fixed['joint_batch_options']['optimize_cutoffs']=False
        fixed['joint_batch_options'].pop('cutoff_bounds_minutes',None)
        p_fixed=load_instance(*load_routing_case(record['m6_case'],use_pool_config=False))
        from models.routing.common.cycle.joint import JointBatchInstance
        p_fixed=JointBatchInstance(p_fixed.network,p_fixed.orders,p_fixed.operating_policy,
                                  fixed['joint_batch_options'],p_fixed.cost_profile.config)
        # The original fixed split must be in the optimization domain.
        p6.validated_cutoffs({b.id:b.cutoff for b in p6.operating_policy.batches})
        initial,evidence=prior_incumbent(p_fixed,record,args.initial_study)
        references.append(dict(case=record['name'],candidates=evidence,
                               chosen_reference_run=initial.get('initial_reference_run') if initial else None))
        initial_path=None
        if initial is not None:
            initial_path=out/'inputs'/f'{index}_initial_joint_result.json'
            write_json(initial_path,initial)
            hashes[str(initial_path)]=dict(sha256=hashlib.sha256(initial_path.read_bytes()).hexdigest(),
                                          snapshot=str(initial_path.relative_to(out)))
        for label,config in [('M5_distance',c5),('M6_fixed_windows',fixed),('M6_joint',c6)]:
            for solver,seed,budget in [('exact',args.seeds[0],args.exact_time)]+[('alns',s,args.time) for s in args.seeds]:
                selected=deepcopy(config); selected.update(save_plots=False,alns_iter=args.iterations)
                if label!='M5_distance' and solver=='alns' and initial_path:
                    selected['initial_joint_result']=str(initial_path)
                case_path=out/'cases'/f'{index}_{label}_{solver}_{seed}.json'; write_json(case_path,selected)
                jobs.append(dict(case=record['name'],treatment=label,solver=solver,seed=seed,budget=budget,
                                 run=f'{index:02d}_{label}_{solver}_{seed}',case_path=str(case_path)))
    write_json(out/'initial_references.json',references)
    source_hashes=snapshot_source(out)
    write_json(out/'experiment.json',dict(schema_version=1,source_manifest=str(manifest_path),budgets=vars(args),
              input_hashes=hashes,source_hashes=source_hashes,jobs=jobs,
              design='Matched incremental search budgets; same audited prior schedule in both M6 ALNS arms; exact independent'))
    rows=execute_jobs(jobs,out,args)
    for row in rows:
        row['joint_optimal_gap_percent']=None
        if row['treatment']!='M5_distance' and row['solver']=='alns' and row['valid']:
            exact=next(r for r in rows if r['case']==row['case'] and r['treatment']==row['treatment'] and r['solver']=='exact')
            if exact['proven_optimal']:
                row['joint_optimal_gap_percent']=100*(row['cost']-exact['cost'])/max(1e-9,abs(exact['cost']))
                if row['joint_optimal_gap_percent']< -1e-6:
                    raise RuntimeError('ALNS beats a claimed exact optimum')
    write_json(out/'rows.json',rows)
    with (out/'rows.csv').open('w',newline='',encoding='utf-8') as handle:
        writer=csv.DictWriter(handle,fieldnames=list(rows[0])); writer.writeheader(); writer.writerows(rows)
    stats=summarize(out,rows,records)
    print(json.dumps(stats,ensure_ascii=False),flush=True)
    return out


if __name__=='__main__':
    main()
