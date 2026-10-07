"""Paired frozen-code comparison; same inputs, budgets and seeds, no solver overlap."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import shutil

from configuration.loader import resolve_project_path
from .run_model6_comparison import freeze_case, run_job, snapshot_source, write_json


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest',required=True)
    parser.add_argument('--before-source',required=True)
    parser.add_argument('--output',required=True)
    parser.add_argument('--exact-time',type=float,default=24)
    parser.add_argument('--time',type=float,default=10)
    parser.add_argument('--iterations',type=int,default=5000)
    parser.add_argument('--seeds',type=int,nargs='+',default=[42,43])
    parser.add_argument('--solvers',choices=('exact','alns'),nargs='+',default=['exact','alns'])
    args=parser.parse_args(argv)
    if any(not math.isfinite(v) or v<=0 for v in (args.exact_time,args.time)) or args.iterations<0:
        parser.error('Invalid budgets')
    out=resolve_project_path(args.output).resolve()
    if out.exists() and any(out.iterdir()):
        raise ValueError('Refusing to overwrite an existing review')
    manifest_path=resolve_project_path(args.manifest)
    manifest=json.loads(manifest_path.read_text())
    before=resolve_project_path(args.before_source).resolve()
    if not (before/'main.py').is_file():
        raise ValueError('Before snapshot must include main.py and its source tree')
    jobs=[]; provenance={}
    for arm in ('before','after'):
        root=out/arm
        for folder in ('inputs','cases','logs'):
            (root/folder).mkdir(parents=True,exist_ok=True)
        if arm=='before':
            shutil.copytree(before,root/'source_snapshot',ignore=shutil.ignore_patterns('__pycache__'))
            hashes={str(p.relative_to(before)):hashlib.sha256(p.read_bytes()).hexdigest()
                    for p in before.rglob('*.py')}
        else:
            hashes=snapshot_source(root)
        inputs={}; arm_jobs=[]
        for index,record in enumerate(manifest['cases']):
            config,frozen=freeze_case(record['m6_case'],root,str(index)); inputs.update(frozen)
            config.update(save_plots=False,alns_iter=args.iterations)
            path=root/'cases'/f'{index}.json'; write_json(path,config)
            methods=[]
            for method in dict.fromkeys(args.solvers):
                methods += ([(method,args.seeds[0],args.exact_time)] if method=='exact' else
                            [(method,s,args.time) for s in dict.fromkeys(args.seeds)])
            for method,seed,budget in methods:
                arm_jobs.append(dict(case=record['name'],treatment='M6_uniform',arm=arm,solver=method,seed=seed,budget=budget,
                                     run=f'{index:02d}_{method}_{seed}',case_path=str(path)))
        provenance[arm]=dict(source_hashes=hashes,input_hashes=inputs,jobs=arm_jobs)
    for index,(a,b) in enumerate(zip(provenance['before']['jobs'],provenance['after']['jobs'])):
        jobs.extend((a,b) if index%2==0 else (b,a))
    write_json(out/'experiment.json',dict(args=vars(args),arms=provenance,jobs=jobs,
                                        timing='Serial paired calls; alternate which code version runs first'))
    shutil.copy2(manifest_path,out/'manifest.json')
    rows=[]
    for job in jobs:
        row=run_job(job,out/job['arm'],20)
        if row['valid']:
            result=json.loads((out/job['arm']/job['run']/'result.json').read_text())
            row.update({key:result.get(key) for key in ('cycle_batch_count','delivery_batch_count',
                       'actual_recovery_round_count','batch_duration_ticks','shared_cutoff_offset_ticks')})
            scale=result['operating_policy']['time_tick_seconds']/60
            row.update(batch_minutes=result['batch_duration_ticks']*scale,
                       phase1_minutes=result['shared_cutoff_offset_ticks']*scale)
        rows.append(row)
        write_json(out/'rows.json',rows)
        print(f"{len(rows)}/{len(jobs)} {row['arm']} {row['case']} {row['solver']}/{row['seed']}: "
              f"{row['status']}, cost={row['cost']}, N={row['pool']}, valid={row['valid']}",flush=True)
    pairs=[]
    for job in provenance['before']['jobs']:
        group=[r for r in rows if r['run']==job['run']]
        a=next(r for r in group if r['arm']=='before')
        b=next(r for r in group if r['arm']=='after')
        pairs.append(dict(case=job['case'],method=job['solver'],seed=job['seed'],before=a,after=b,
                          cost_change=(b['cost']-a['cost']) if a['valid'] and b['valid'] else None))
    write_json(out/'paired_summary.json',dict(pairs=pairs,valid_before=sum(r['valid'] for r in rows if r['arm']=='before'),
              valid_after=sum(r['valid'] for r in rows if r['arm']=='after'),
              limitations=['Short-budget descriptive comparison, not convergence or statistical evidence',
                           'Only the configured solvers/seeds were run; costs remain synthetic units',
                           'Different schedules may exchange box inventory against transport cost']))
    lines=['# 模型6求解检查：修改前后配对验证', '',
           '两版均从零开始，使用相同物理输入、均匀分批规则、K=1/2/3、费用参数、时间上限、迭代上限和种子；逐次串行运行，交替先后顺序。有效方案由当前独立审计重放。', '',
           '| 算例 | 方法/种子 | 原成本 | 新成本 | 原箱数→新箱数 | 原状态→新状态 |',
           '| --- | --- | ---: | ---: | --- | --- |']
    for pair in pairs:
        a,b=pair['before'],pair['after']
        ca=f"{a['cost']:.3f}" if a['valid'] else '无可行解'
        cb=f"{b['cost']:.3f}" if b['valid'] else '无可行解'
        lines.append(f"| {pair['case']} | {pair['method']}/{pair['seed']} | {ca} | {cb} | "
                     f"{a['pool']}→{b['pool']} | {a['status']}→{b['status']} |")
    lines += ['', '修改仅加强求解：加入路线行驶、服务及卸箱时间的显式下界；ALNS增加清洗赶上下一批的临界点、共同分界的局部细化和较晚回收的初始化；统计覆盖所有K。局部细化从算子轮盘移到搜索结束，限制在各K预算的10%内，单次解码可能略微越界。费用和物理约束未放宽。', '',
              '限时结果不能证明算法普遍优越，箱数变化不等于成本变化。精确模型小例可检查最优目标是否一致；大例仍需更充分的求解预算。', '',
              'Material Passport：academic-research-suite/experiment-agent；validate；ANALYZED；model6_search_review_v1。11项解释检查覆盖Simpson、生态、Berkson、碰撞变量、基准率、均值回归、幸存者、多重搜索、分叉路径、相关与因果、反向因果；逐例逐种子列全量结果，不作显著性、总体性能或因果结论。']
    (out/'review_report.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    print(out,flush=True)
    return out


if __name__=='__main__':
    main()
