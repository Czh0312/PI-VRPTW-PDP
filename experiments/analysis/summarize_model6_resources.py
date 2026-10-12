"""Re-audit M5/M6 box/batch comparisons and certify simple resource bounds."""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

from tools.configuration.loader import load_routing_case,resolve_project_path
from models.common.cycle import load_instance
from models.common.cycle.audit import audit_solution


def resource_certificate(problem,result):
    """Certify pool size separately from cost optimality.

    Every issued batch simultaneously occupies its delivered boxes, so N is
    at least its forced demand and the largest indivisible order. A box can
    be issued at most once per delivery batch, hence Q <= B_used*N.
    These bounds use necessary legal domains, not heuristic observations.
    """
    if not getattr(problem,'joint_batch',False):
        raise ValueError('Resource certificate requires an original M6 instance')
    audited=audit_solution(problem,result)
    if not audited['valid']:
        raise ValueError(f'Cannot certify an invalid schedule: {audited["errors"]}')
    forced={bid:[o.id for o in problem.orders if problem.legal_batches[o.id]==(bid,)]
            for bid in problem.batch_map}
    demand={bid:sum(problem.order_map[oid].count for oid in ids) for bid,ids in forced.items()}
    lower=max(problem.pool_min,max(o.count for o in problem.orders),max(demand.values(),default=0))
    N=result['container_pool_size']; B=result['delivery_batch_count']
    batch_lower=max(math.ceil(problem.total/N),sum(bool(n) for n in demand.values()))
    if N<lower or B<batch_lower:
        raise RuntimeError('Audited solution violates a necessary resource lower bound')
    return dict(pool=N,delivery_batches=B,total_container_uses=problem.total,
                forced_batch_orders=forced,forced_batch_demands=demand,
                pool_lower_bound=lower,pool_minimum_proven=N==lower,
                delivery_batch_lower_bound_given_pool=batch_lower,
                delivery_batch_minimum_given_pool_proven=B==batch_lower,
                lexicographic_pool_then_batches_proven=N==lower and B==batch_lower,
                cost_optimal_proven=bool(result['info'].get('proven_optimal')),
                scope='Original M6 legal domains, fixed candidate slots, simultaneous batch issues, indivisible orders; batch minimum is conditional on this pool size')


def summarize(directory):
    root=resolve_project_path(directory).resolve()
    rows=json.loads((root/'rows.json').read_text(encoding='utf-8'))
    plan=json.loads((root/'experiment.json').read_text(encoding='utf-8'))
    manifest=json.loads((root/'inputs/manifest.json').read_text(encoding='utf-8'))
    lade=all(c.get('source_kind')=='lade_derived_scenario' for c in manifest['cases'])
    jobs={j['run']:j for j in plan['jobs']}
    verified=[]
    for row in rows:
        if not row['valid']:
            continue
        job=jobs[row['run']]
        problem=load_instance(*load_routing_case(job['case_path'],use_pool_config=False))
        result=json.loads((root/row['run']/'result.json').read_text(encoding='utf-8'))
        audited=audit_solution(problem,result)
        if not audited['valid']:
            raise ValueError(f'{row["run"]}: {audited["errors"]}')
        verified.append(dict(run=row['run'],valid=True,minimum_pool_for_schedule=audited['minimum_pool_for_schedule']))
    summaries=[]
    for record in manifest['cases']:
        name=record['name']
        group=[r for r in rows if r['case']==name and r['valid']]
        baseline=[r for r in group if r['treatment']=='M5_distance']
        candidates=[r for r in group if r['treatment']=='M6_joint']
        metadata=dict(case=name,source_kind=record.get('source_kind'),family_id=record.get('family_id',name),
                      size=record.get('size'),candidate_batch_count=record.get('candidate_batch_count'),
                      order_count=record.get('orders'),total_container_uses=record.get('total_container_uses'))
        if not baseline or not candidates:
            summaries.append(dict(**metadata,comparison_complete=False,
                                  missing_feasible_treatments=[t for t,rs in [('M5_distance',baseline),('M6_joint',candidates)] if not rs],
                                  run_statuses=[dict(run=r['run'],treatment=r['treatment'],status=r['status'],valid=r['valid'],error=r['error'])
                                                for r in rows if r['case']==name]))
            continue
        m5=min(baseline,key=lambda r:(r['distance'],r['seed'],r['solver']))
        conservative=min(baseline,key=lambda r:(r['minimum_pool_for_schedule'],r['distance'],r['seed']))
        m6=min(candidates,key=lambda r:(r['cost'],r['seed'],r['solver']))
        p6=load_instance(*load_routing_case(jobs[m6['run']]['case_path'],use_pool_config=False))
        result=json.loads((root/m6['run']/'result.json').read_text(encoding='utf-8'))
        certificate=resource_certificate(p6,result)
        restricted=[r for r in group if r['treatment']=='M6_pool_only']
        fixed=min(restricted,key=lambda r:r['cost']) if restricted else None
        metadata.update(order_count=m6['order_count'],total_container_uses=m6['total_container_uses'])
        summaries.append(dict(**metadata,comparison_complete=True,
                              m5_preallocated_pool=m5['pool'],m5_best_distance_run=m5['run'],
                              m5_best_distance_schedule_required_pool=m5['minimum_pool_for_schedule'],
                              m5_smallest_required_pool_among_runs=conservative['minimum_pool_for_schedule'],
                              m5_smallest_pool_run=conservative['run'],m5_delivery_batches=m5['batches'],
                              m5_best_distance=m5['distance'],m5_e1_trips=m5['e1_trips'],m5_e2_trips=m5['e2_trips'],
                              m6_selected_run=m6['run'],m6_pool=m6['pool'],m6_delivery_batches=m6['batches'],
                              m6_e1_trips=m6['e1_trips'],m6_e2_trips=m6['e2_trips'],m6_distance=m6['distance'],
                              fixed_assignment_best_pool=fixed['pool'] if fixed else None,
                              fixed_assignment_delivery_batches=fixed['batches'] if fixed else None,
                              fixed_assignment_best_cost=fixed['cost'] if fixed else None,
                              full_assignment_best_cost=m6['cost'],
                              free_assignment_improved_cost_over_fixed=(m6['cost']<fixed['cost']-1e-6) if fixed else None,
                              resource_certificate=certificate))
    output=dict(cases=summaries,independent_reaudit=verified,attempted_runs=len(rows),valid_runs=len(verified),
                no_incumbent_runs=[dict(run=r['run'],status=r['status']) for r in rows if not r['valid']],
                cost_warning='Artificial costs only; AOI parcel-to-box mapping, appointments, recovery and full-stock release are explicit scenario assumptions.')
    complete=[s for s in summaries if s['comparison_complete']]
    incomplete=[s for s in summaries if not s['comparison_complete']]
    output.update(planned_cases=len(manifest['cases']),complete_comparison_cases=len(complete),
                  incomplete_comparison_cases=len(incomplete))
    (root/'resources.json').write_text(json.dumps(output,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    introduction=('本轮使用原项目 operational_scenarios_v2 的 LaDe 派生场景，保留原需求、时间窗、车队和道路矩阵。它们是同一天不同规模的配送区域聚合任务，箱数沿用原场景。'
                  if lade else '本报告按本次冻结输入核对箱池与配送批次；数据来源、数量和窗口见输入清单。')
    lines=['# 箱子和配送批次：独立核对', '',introduction, '',
           '| 算例 | 配送点/区域数 | 当日需求箱次 | M5预设池 | M5最短距离时间表需箱数 | M5各次方案最小需箱数 | M6箱子/配送批次 | M5配送批次 |',
           '|---|---:|---:|---:|---:|---:|---:|---:|']
    for s in complete:
        lines.append(f'| {s["case"]} | {s["order_count"]} | {s["total_container_uses"]} | {s["m5_preallocated_pool"]} | {s["m5_best_distance_schedule_required_pool"]} | {s["m5_smallest_required_pool_among_runs"]} | **{s["m6_pool"]}/{s["m6_delivery_batches"]}** | {s["m5_delivery_batches"]} |')
    lines+=['', 'M5预设池不等于它最低需要的箱数。时间表的最小需箱数来自独立库存重放，M5全部运行中最小值还受路线选择影响；以上两个M5指标均不声称是模型5整体的最小箱数。模型6方案按已运行联合方案的最低成本选取。', '',
            '## 箱数与批次数的单独证明', '',
            '| 算例 | 区域数 | 容器池必要下界 | 实际池 | 箱数最小已证明 | 在该池下配送批次必要下界 | 实际批次 | 条件批次数最小已证明 |',
            '|---|---:|---:|---:|---|---:|---:|---|']
    for s in complete:
        c=s['resource_certificate']
        lines.append(f'| {s["case"]} | {s["order_count"]} | {c["pool_lower_bound"]} | {c["pool"]} | {c["pool_minimum_proven"]} | {c["delivery_batch_lower_bound_given_pool"]} | {c["delivery_batches"]} | {c["delivery_batch_minimum_given_pool_proven"]} |')
    lines+=['', '必要下界来自每批必须归属的订单箱量、单笔不可拆订单箱量和显式池下限。批次同时发箱，因此实体池不能小于该批被强制配送的箱量。当已审计方案达到下界时，才证明其箱数最小；具体强制订单及需求均保存在证据JSON。固定N时，一批最多发N个实体箱，故累计Q箱次至少需 ceil(Q/N) 个配送批次，必须启用的批次也另计下界。表中的批次证明以给定箱池为条件，不等于允许增加箱池后仍至少需要这么多批。', '',
            '## 结果边界', '',
            f'计划{len(summaries)}个算例，共{len(rows)}次运行，其中{len(verified)}次有可行解并重新审计有效；{len(complete)}例有完整M5/M6可行解对比，{len(incomplete)}例缺少可行对照。未得到有效可行解的运行及状态完整保留，限时未找到解不等于问题不可行。两种求解器独立运行；成本最优证明与上面的资源下界证明分别记录。', '',
            '自由分批的搜索空间包含固定分批，但限时启发式未必找到更好的方案；本次原分批对照的箱池和费用单独保留，不能把优化池与调度的作用全部归于分批自由度。', '']
    for s in incomplete:
        lines += [f'{s["case"]}：未得到有效的 '+', '.join(s['missing_feasible_treatments'])+' 可行方案，不计入改善率。','']
    for s in complete:
        if s['free_assignment_improved_cost_over_fixed'] is False:
            lines += [f'{s["case"]}：联合搜索的最低费用未优于原分批对照；原分批对照也获得{ s["fixed_assignment_best_pool"] }箱的可行池。本例不声称已观察到自由分批带来额外收益。','']
    if lade:
        lines += ['包裹数量和区域位置有LaDe原数据依据；一包裹一箱、预约窗口、周转回收、车型和清洗为原研究情景，全部货物在周期开始可领用是本轮明确新增的假设。','']
    lines += ['费用系数的来源和单位以经济配置为准；合成或代理系数不作为真实人民币节约结论。','',
            '完整费用、状态、种子、路线、输入与源码快照见 [运行报告](report.md)，独立资源证据见 [resources.json](resources.json)。','']
    (root/'resources.md').write_text('\n'.join(lines),encoding='utf-8')
    return output


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory',required=True)
    args=parser.parse_args(argv)
    result=summarize(args.directory)
    for case in result['cases']:
        print(case['case'],case['m6_pool'],case['m6_delivery_batches'],case['resource_certificate']['pool_minimum_proven']) if case['comparison_complete'] else print(case['case'],'MISSING',case['missing_feasible_treatments'])


if __name__=='__main__':
    main()
