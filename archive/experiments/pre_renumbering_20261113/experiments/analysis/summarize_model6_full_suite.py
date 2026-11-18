"""Summarize full coverage without hiding missing solutions or worse outcomes."""
from __future__ import annotations

import argparse
from collections import Counter,defaultdict
import csv
import json
import statistics

from tools.configuration.loader import resolve_project_path
from experiments.analysis.summarize_model6_resources import summarize


SOURCE_NAMES={'zhou_derived_scenario':'Zhou派生','lade_derived_scenario':'LaDe派生',
              'synthetic_mechanism':'人工机制'}


def plot_resources(cases,root):
    """Static research plot of observed resources, with equality reference."""
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    complete=[c for c in cases if c['comparison_complete']]
    if not complete:
        return
    colors={'zhou_derived_scenario':'#2563a6','lade_derived_scenario':'#bc6118','synthetic_mechanism':'#5b8c46'}
    labels={'zhou_derived_scenario':'Zhou-derived','lade_derived_scenario':'LaDe-derived','synthetic_mechanism':'Mechanism'}
    fig,(ax,bars)=plt.subplots(1,2,figsize=(11,4.8),layout='constrained')
    for source in sorted({c['source_kind'] for c in complete}):
        group=[c for c in complete if c['source_kind']==source]
        ax.scatter([c['m5_smallest_required_pool_among_runs'] for c in group],
                   [c['m6_pool'] for c in group],s=30,alpha=.7,color=colors[source],label=labels[source],edgecolors='white',linewidths=.4)
    maximum=max(max(c['m5_smallest_required_pool_among_runs'],c['m6_pool']) for c in complete)*1.05
    ax.plot([0,maximum],[0,maximum],color='#444444',linestyle='--',linewidth=1,label='Equal boxes')
    ax.set(xlim=(0,maximum),ylim=(0,maximum),xlabel='M5: minimum needed by observed schedules',
           ylabel='M6: boxes in lowest observed cost solution',title='Entity box counts')
    ax.set_aspect('equal',adjustable='box');ax.grid(alpha=.15);ax.legend(fontsize=8)
    sources=sorted({c['source_kind'] for c in complete})
    left=[0]*len(sources)
    for label,color,test in [('Fewer batches','#479b70',lambda d:d>0),('Same batches','#9ea8b1',lambda d:d==0),
                             ('More batches','#ce685d',lambda d:d<0)]:
        counts=[sum(test(c['m5_delivery_batches']-c['m6_delivery_batches']) for c in complete if c['source_kind']==source) for source in sources]
        bars.barh([labels[s] for s in sources],counts,left=left,color=color,label=label)
        for i,n in enumerate(counts):
            if n:
                bars.text(left[i]+n/2,i,str(n),ha='center',va='center',fontsize=9)
        left=[l+n for l,n in zip(left,counts)]
    bars.set(xlabel='Number of compared configurations',title='M6 delivery batches relative to M5')
    bars.legend(fontsize=8,loc='lower right');bars.grid(axis='x',alpha=.15);bars.set_axisbelow(True)
    fig.suptitle('Full-suite short-budget screening | synthetic cost objective',fontsize=12)
    fig.savefig(root/'resource_comparison.png',dpi=180)
    plt.close(fig)


def comparison_statistics(cases):
    complete=[c for c in cases if c['comparison_complete']]
    def reductions(first,second):
        return [c[first]-c[second] for c in complete]
    def percentages(first,second):
        return [100*(c[first]-c[second])/c[first] for c in complete if c[first]>0]
    def mean(values):
        return statistics.mean(values) if values else None
    pool=reductions('m5_smallest_required_pool_among_runs','m6_pool')
    batches=reductions('m5_delivery_batches','m6_delivery_batches')
    return dict(planned_cases=len(cases),compared_cases=len(complete),missing_cases=len(cases)-len(complete),
                pool_reduced=sum(d>0 for d in pool),pool_equal=sum(d==0 for d in pool),pool_increased=sum(d<0 for d in pool),
                mean_boxes_saved=mean(pool),mean_pool_reduction_percent=mean(percentages('m5_smallest_required_pool_among_runs','m6_pool')),
                mean_m5_boxes=mean([c['m5_smallest_required_pool_among_runs'] for c in complete]),
                mean_m6_boxes=mean([c['m6_pool'] for c in complete]),
                batches_reduced=sum(d>0 for d in batches),batches_equal=sum(d==0 for d in batches),batches_increased=sum(d<0 for d in batches),
                mean_batches_saved=mean(batches),mean_m5_batches=mean([c['m5_delivery_batches'] for c in complete]),
                mean_m6_batches=mean([c['m6_delivery_batches'] for c in complete]),
                mean_distance_reduction_percent=mean(percentages('m5_best_distance','m6_distance')),
                mean_e1_trips_saved=mean(reductions('m5_e1_trips','m6_e1_trips')),
                mean_e2_trips_saved=mean(reductions('m5_e2_trips','m6_e2_trips')),
                pool_minimum_proven=sum(c['resource_certificate']['pool_minimum_proven'] for c in complete),
                conditional_batch_minimum_proven=sum(c['resource_certificate']['delivery_batch_minimum_given_pool_proven'] for c in complete),
                selected_cost_optimal_proven=sum(c['resource_certificate']['cost_optimal_proven'] for c in complete),
                free_batching_cost_better_than_fixed=sum(c['free_assignment_improved_cost_over_fixed'] is True for c in complete),
                fixed_batching_comparable=sum(c['free_assignment_improved_cost_over_fixed'] is not None for c in complete))


def observed_resource_frontier(rows):
    """Non-dominated box/batch pairs, each with an audited M6 witness."""
    witnesses={}
    for row in rows:
        if not row['valid'] or not row['treatment'].startswith('M6_'):
            continue
        pair=(row['pool'],row['batches'])
        if pair not in witnesses or row['cost']<witnesses[pair]['cost']:
            witnesses[pair]=row
    return [dict(pool=n,delivery_batches=b,witness_run=witnesses[n,b]['run'],
                 treatment=witnesses[n,b]['treatment'],cost=witnesses[n,b]['cost'])
            for n,b in sorted(witnesses) if not any(nn<=n and bb<=b and (nn<n or bb<b) for nn,bb in witnesses)]


def full_summary(directory,*,reaudit=True):
    root=resolve_project_path(directory)
    resources=summarize(root) if reaudit else json.loads((root/'resources.json').read_text())
    rows=json.loads((root/'rows.json').read_text())
    plan=json.loads((root/'experiment.json').read_text())
    grouped=defaultdict(list)
    for case in resources['cases']:
        grouped[(case['source_kind'],case['order_count'],case['candidate_batch_count'])].append(case)
    groups=[]
    for (source,n,b),cases in sorted(grouped.items()):
        groups.append(dict(source_kind=source,order_count=n,candidate_batch_count=b,**comparison_statistics(cases)))
    seed_stats=[]
    paired_seed=[]
    frontiers=[]
    for case in resources['cases']:
        frontiers.append(dict(case=case['case'],pairs=observed_resource_frontier([r for r in rows if r['case']==case['case']])))
        valid=[r for r in rows if r['case']==case['case'] and r['valid'] and r['solver']=='alns']
        joint=[r for r in valid if r['treatment']=='M6_joint']
        if joint:
            costs=[r['cost'] for r in joint]
            solver_info=[json.loads((root/r['run']/'result.json').read_text())['info'] for r in joint]
            seed_stats.append(dict(case=case['case'],valid_seeds=len(joint),cost_best=min(costs),cost_mean=statistics.mean(costs),
                                   cost_worst=max(costs),cost_spread_percent=100*(max(costs)-min(costs))/statistics.mean(costs),
                                   pools=[r['pool'] for r in joint],batches=[r['batches'] for r in joint],
                                   iterations=[i.get('iterations') for i in solver_info],
                                   initialization_only=sum(i.get('iterations')==0 for i in solver_info)))
        for seed in plan['budgets']['seeds']:
            baseline=next((r for r in valid if r['seed']==seed and r['treatment']=='M5_distance'),None)
            candidate=next((r for r in valid if r['seed']==seed and r['treatment']=='M6_joint'),None)
            paired_seed.append(dict(case=case['case'],seed=seed,complete=bool(baseline and candidate),
                                    boxes_saved=baseline['minimum_pool_for_schedule']-candidate['pool'] if baseline and candidate else None,
                                    batches_saved=baseline['batches']-candidate['batches'] if baseline and candidate else None))
    calendar_pairs=[]
    families=defaultdict(dict)
    for c in resources['cases']:
        families[c['family_id']][c['candidate_batch_count']]=c
    for family,by_batch in families.items():
        if 2 in by_batch and 3 in by_batch and all(c['comparison_complete'] for c in by_batch.values()):
            b2,b3=by_batch[2],by_batch[3]
            calendar_pairs.append(dict(family_id=family,b2_pool=b2['m6_pool'],b3_pool=b3['m6_pool'],
                                       b2_delivery_batches=b2['m6_delivery_batches'],b3_delivery_batches=b3['m6_delivery_batches']))
    statuses=Counter(r['status'] for r in rows)
    total=comparison_statistics(resources['cases'])
    total['joint_exact_cost_optimal_cases']=len({r['case'] for r in rows if r['treatment']=='M6_joint'
                                              and r['solver']=='exact' and r['valid'] and r['proven_optimal']})
    counterexample=next((c for c in resources['cases'] if c['comparison_complete'] and c['source_kind']!='synthetic_mechanism'
                        and c['resource_certificate']['cost_optimal_proven']
                        and c['m6_pool']>c['m5_smallest_required_pool_among_runs']),None)
    example_text=(f'例如{counterexample["case"]}，模型5已得时间表需{counterexample["m5_smallest_required_pool_among_runs"]}箱/{counterexample["m5_delivery_batches"]}批，'
                  f'模型6已证明测试成本最优的解为{counterexample["m6_pool"]}箱/{counterexample["m6_delivery_batches"]}批。'
                  '这直接说明总成本目标不能保证箱数和配送批次各自最小。' if counterexample else '')
    source_stats=[dict(source_kind=source,**comparison_statistics([c for c in resources['cases'] if c['source_kind']==source]))
                  for source in sorted({c['source_kind'] for c in resources['cases']})]
    summary=dict(overall=total,by_source=source_stats,by_source_size_calendar=groups,
                 attempted_runs=len(rows),planned_runs=len(plan['jobs']),valid_runs=sum(r['valid'] for r in rows),
                 run_statuses=dict(statuses),alns_seed_statistics=seed_stats,paired_alns_seed_results=paired_seed,
                 observed_resource_frontiers=frontiers,
                 calendar_pairs=calendar_pairs,budgets=plan['budgets'],
                 population_note=f'B2/B3 are paired configurations of the same {len(families)} families; repeated geometries and seeds are not independent samples.',
                 comparison_note='M5 pool is the minimum required by its observed feasible schedules, not its preallocated pool or a proved global minimum. M6 chooses lowest observed joint cost, not lowest observed pool.',
                 price_note='Synthetic coefficients only; Zhou coordinate distances are not observed kilometres. No empirical CNY savings claim.')
    (root/'full_summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2,allow_nan=False)+'\n',encoding='utf-8')
    flat=[]
    for c in resources['cases']:
        row={k:v for k,v in c.items() if not isinstance(v,(dict,list))}
        row['observed_m6_box_batch_options']='; '.join(f'{p["pool"]}/{p["delivery_batches"]}' for p in next(f['pairs'] for f in frontiers if f['case']==c['case']))
        if c['comparison_complete']:
            row.update(boxes_saved=c['m5_smallest_required_pool_among_runs']-c['m6_pool'],
                       pool_reduction_percent=100*(c['m5_smallest_required_pool_among_runs']-c['m6_pool'])/c['m5_smallest_required_pool_among_runs'],
                       batches_saved=c['m5_delivery_batches']-c['m6_delivery_batches'])
            row.update({k:v for k,v in c['resource_certificate'].items() if isinstance(v,(int,float,bool))})
            row['joint_exact_cost_optimal_proven']=any(r['case']==c['case'] and r['treatment']=='M6_joint'
                                                       and r['solver']=='exact' and r['valid'] and r['proven_optimal'] for r in rows)
        else:
            row['missing_feasible_treatments']=','.join(c['missing_feasible_treatments'])
        flat.append(row)
    fields=list(dict.fromkeys(k for row in flat for k in row))
    with (root/'case_comparison.csv').open('w',encoding='utf-8-sig',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=fields);writer.writeheader();writer.writerows(flat)
    plot_resources(resources['cases'],root)
    def number(x):
        return '—' if x is None else f'{x:.2f}'
    budgets=plan['budgets']
    source_counts=Counter(c['source_kind'] for c in resources['cases'])
    coverage='、'.join(f'{SOURCE_NAMES.get(source,source)}{n}个' for source,n in sorted(source_counts.items()))
    lines=['# 模型5与模型6：全部多批次算例', '',
           f'本轮清单中的{total["planned_cases"]}个B2/B3配置全部纳入：{coverage}。对应{len(families)}个基础场景；单批次B1属于其他模型研究范围，历史V1数据不混入。', '',
           f'每种处理精确求解{budgets["exact_time"]:g}秒、ALNS每种子{budgets["time"]:g}秒，种子{budgets["seeds"]}，{budgets["workers"]}个并发进程。这是短预算全量筛查，受初始化时间和并发资源竞争影响，不是收敛性能实验。', '',
           f'已完成{len(rows)}/{len(plan["jobs"])}次运行，{sum(r["valid"] for r in rows)}次有独立审计有效的可行解；{total["compared_cases"]}个配置可作M5/M6完整比较，{total["missing_cases"]}个缺少可行对照。所有失败、无解和限时状态均保留；未找到解不等于已证明不可行。', '',
           '## 箱数与配送批次', '',
           'M5取各次已得可行时间表中的最小需箱量，不把N=Q的预设池当成最低需箱量。M6取已得联合方案中成本最低的一次。正的减少量表示模型6较少，负值表示增加。', '',
           '| 来源 | 配置数/有效比较 | 箱数减少/相同/增加 | 平均少箱 | 平均箱数减少率 | 批次减少/相同/增加 |',
           '|---|---:|---:|---:|---:|---:|']
    for s in source_stats+[dict(source_kind='all',**total)]:
        source=SOURCE_NAMES.get(s['source_kind'],'全部')
        lines.append(f'| {source} | {s["planned_cases"]}/{s["compared_cases"]} | {s["pool_reduced"]}/{s["pool_equal"]}/{s["pool_increased"]} | {number(s["mean_boxes_saved"])} | {number(s["mean_pool_reduction_percent"])}% | {s["batches_reduced"]}/{s["batches_equal"]}/{s["batches_increased"]} |')
    lines+=['', '减少率是逐配置相对其M5已得最小需箱量的百分比的算术平均；不是总体实测节约率。配对场景、重复几何和算法种子不能当成独立样本，不报告统计显著性。', '',
            '![箱数与批次数的全量对比](resource_comparison.png)', '',
            '| 来源 | 配送点/区域数 | 候选批次 | 配置数/有效比较 | M5平均箱数 | M6平均箱数 | 平均少箱 | 平均箱数减少率 | 批次减少/相同/增加 |',
            '|---|---:|---:|---:|---:|---:|---:|---:|---:|']
    for g in groups:
        lines.append(f'| {SOURCE_NAMES.get(g["source_kind"],g["source_kind"])} | {g["order_count"]} | {g["candidate_batch_count"]} | {g["planned_cases"]}/{g["compared_cases"]} | {number(g["mean_m5_boxes"])} | {number(g["mean_m6_boxes"])} | {number(g["mean_boxes_saved"])} | {number(g["mean_pool_reduction_percent"])}% | {g["batches_reduced"]}/{g["batches_equal"]}/{g["batches_increased"]} |')
    lines+=['', '## 路线与分批的作用', '',
            f'相对于模型5最短距离方案，模型6所选方案平均距离减少率为{number(total["mean_distance_reduction_percent"])}%（负值表示变长），一级/二级趟数平均分别少{number(total["mean_e1_trips_saved"])}/{number(total["mean_e2_trips_saved"])}趟。成本目标可能用更长路线换取更少的箱子或占用工时，不能将成本下降解释成所有资源同时减少。', '',
            example_text, '',
            f'与固定原分批、优化箱池与路径的成本对照相比，有{total["fixed_batching_comparable"]}例可比，其中{total["free_batching_cost_better_than_fixed"]}例的联合搜索找到了更低测试成本。联合问题包含固定分批方案，但短时ALNS可能未找到它；不得把全部箱数收益都归于重新分批。', '',
            'CSV另列每例已观察到的箱数/批次数取舍，汇集联合搜索和固定分批对照中经审计的模型6方案，按箱数与批次数筛选互不支配的组合，JSON记录每一组合的运行证据。这不是已证明完整的资源前沿，也不能记作联合算法单独找到的结果。', '',
            '## 证明与求解状态', '',
            f'所选方案中，{total["pool_minimum_proven"]}例达到箱池必要下界，{total["conditional_batch_minimum_proven"]}例达到给定箱池下的配送批次必要下界，{total["selected_cost_optimal_proven"]}例所选结果有成本最优证明。联合精确法在全部运行中共证明{total["joint_exact_cost_optimal_cases"]}个配置成本最优；同值ALNS结果仍保留自己的启发式标记。资源与成本证明分别记录。', '',
            f'模型6联合ALNS的逐种子成本、箱数、批次与迭代数保存在JSON中；其中{sum(s["initialization_only"] for s in seed_stats)}次有效运行预算仅够初始化、未完成搜索迭代。', '',
            '| 状态 | 次数 |', '|---|---:|']
    lines += [f'| {status} | {n} |' for status,n in sorted(statuses.items())]
    missing=[c for c in resources['cases'] if not c['comparison_complete']]
    if missing:
        lines+=['', '缺少完整可行对照的配置：', '']+[f'- {c["case"]}：'+', '.join(c['missing_feasible_treatments']) for c in missing]
    lines+=['', '## 数据与费用边界', '',
            '保留当前V2原需求、重量、顾客和仓库、服务窗、原批次、道路矩阵、车队、回收截点与清洗闭合。显式新增假设是全部货物在周期开始可领用，允许在原候选批次内重分配。Zhou货量到箱量、LaDe一包裹一箱、预约与回收等仍是研究情景。', '',
            '费用全部使用synthetic_units测试系数。Zhou距离是坐标单位，LaDe使用既有道路矩阵；本轮不提供真实人民币节约或实测成本标定结论。', '',
            '逐例箱数和批次见 [完整箱数表](resources.md) 与 [186例对比CSV](case_comparison.csv)；逐次状态、成本、路线及种子见 [运行报告](report.md)、[rows.csv](rows.csv)。输入和求解源码冻结于experiment.json；独立审计、下界证据和种子差异分别保留在各运行目录与JSON。', '']
    (root/'full_summary.md').write_text('\n'.join(lines),encoding='utf-8')
    return summary


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--directory',required=True)
    args=p.parse_args(argv)
    result=full_summary(args.directory)
    print(json.dumps(result['overall'],ensure_ascii=False,indent=2))


if __name__=='__main__':
    main()
