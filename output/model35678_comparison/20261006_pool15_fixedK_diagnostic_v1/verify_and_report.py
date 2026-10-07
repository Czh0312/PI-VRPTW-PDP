"""Independently replay nine fixed-K searches and price their tradeoffs."""
from __future__ import annotations
from collections import defaultdict
import csv, hashlib, json, math, sys, tempfile
from pathlib import Path

OUT=Path(__file__).resolve().parent
ROOT=OUT.parents[2]

def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))

def write(path,value):
    Path(path).write_text(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False)+'\n',encoding='utf-8')

def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def rebase(value,old_root):
    if isinstance(value,dict): return {k:rebase(v,old_root) for k,v in value.items()}
    if isinstance(value,list): return [rebase(v,old_root) for v in value]
    if isinstance(value,str):
        normalized=value.replace('\\','/')
        if normalized.startswith(old_root+'/'): return str(ROOT/normalized[len(old_root)+1:])
    return value

def main():
    plan,rows=read(OUT/'experiment.json'),read(OUT/'rows.json')
    assert len(rows)==len(plan['jobs'])==9
    assert {r['id'] for r in rows}=={j['id'] for j in plan['jobs']}
    assert len({r['id'] for r in rows})==9
    assert sha(OUT/'run_study.py')==plan['runner_sha256']
    assert sha(OUT/'inputs/common_economic.json')==plan['common_economic_sha256']
    for file,h in plan['source_hashes'].items(): assert sha(OUT/'source_snapshot'/file)==h
    for record in plan['input_hashes'].values():
        assert sha(OUT/record['snapshot'])==record.get('snapshot_sha256',record['sha256'])
    sys.path.insert(0,str(OUT/'source_snapshot'))
    from configuration.loader import load_routing_case
    from models.routing.common.cycle.instance import load_instance
    from models.routing.common.cycle.audit import audit_solution
    from models.cost.comparison import evaluate_comparison_cost
    original_root=plan['jobs'][0]['case_path'].replace('\\','/').split('/output/model35678_comparison/')[0]
    prior=Path(rebase(plan['prior_study'],original_root))
    assert sha(prior/'experiment.json')==plan['prior_experiment_sha256']
    assert read(prior/'experiment.json')['source_hashes']==plan['source_hashes']
    jobs={j['id']:j for j in plan['jobs']}
    diagnostics,checks=[],[]
    for row in sorted(rows,key=lambda r:(r['model'],r['fixed_k'])):
        if not row['valid']:
            checks.append(dict(id=row['id'],valid=False,status=row['status']))
            continue
        job=jobs[row['id']]
        cfg=rebase(read(OUT/'cases'/Path(job['case_path'].replace('\\','/')).name),original_root)
        with tempfile.TemporaryDirectory(prefix='pool15_fixedk_') as t:
            f=Path(t)/'case.json'; write(f,cfg)
            cfg,fleet=load_routing_case(f,use_pool_config=False)
            problem=load_instance(cfg,fleet)
        assert problem.batch_count_candidates==(job['fixed_k'],)
        assert problem.cost_profile.hash==plan['economic_profile_hash']
        target=OUT/'results'/row['id']
        result,saved_audit,saved_cost=[read(target/name) for name in ('result.json','independent_audit.json','comparison_cost.json')]
        audit=audit_solution(problem,result)
        assert audit['valid'] and audit['periodic_replay']['valid'],(row['id'],audit['errors'])
        assert audit['periodic_replay']['cycles']==2
        assert saved_audit['valid'] and saved_audit['periodic_replay']['valid']
        cost=evaluate_comparison_cost(problem,result,cfg['economic_config'],audit=audit)
        assert cost['economic_profile_hash']==saved_cost['economic_profile_hash']==plan['economic_profile_hash']
        for name,value in cost['components'].items():
            assert math.isclose(value,saved_cost['components'][name],abs_tol=1e-6)
            assert math.isclose(value,row['cost_components'][name],abs_tol=1e-6)
        assert math.isclose(cost['total'],row['cost'],abs_tol=1e-6)
        assert math.isclose(cost['total'],result['info']['objective'],abs_tol=1e-6)
        k=job['fixed_k']; pool=cost['container_pool_size']; balance=audit['container_balance']
        assert result['cycle_batch_count']==k and row['k']==k
        assert len(result['count_search'])==1 and result['count_search'][0]['batch_count']==k
        assert result['count_search'][0]['budget']>299
        assert pool==row['pool']==audit['minimum_pool_for_schedule']
        assert balance['issued']==balance['returned_to_depot']==78
        returned=sum(v['quantity'] for v in audit['depot_releases'])
        assert returned==78
        d1,d2=audit['distances']['e1'],audit['distances']['e2']
        direct=2.535*d1+(.06/.85*.8)*d2+15*pool+.4*balance['issued']+.2*returned
        assert math.isclose(direct,cost['total'],abs_tol=1e-6)
        assert math.isclose(cost['components']['cleaning'],.2*returned,abs_tol=1e-6)
        assert math.isclose(cost['components']['tracking'],.1*balance['issued'],abs_tol=1e-6)
        assert math.isclose(cost['components']['container_wear'],.3*balance['issued'],abs_tol=1e-6)
        demands={b['id']:b['container_demand'] for b in result['batches']}
        actual=sum(q>0 for q in demands.values())
        assert actual==row['delivery_batches']==result['delivery_batch_count']
        returns=defaultdict(int)
        for trip in result['e1_trips']:
            returns[trip['batch_id']]+=sum(e['quantity'] for e in trip['events'] if e['kind']=='p')
        assert sum(returns.values())==returned
        old_result=read(prior/'results'/f'00_M{row["model"]}_alns_42'/'result.json')
        old=next(c for c in old_result['count_search'] if c['batch_count']==k)
        diagnostics.append(dict(id=row['id'],model=row['model'],fixed_k=k,actual_delivery_batches=actual,
            pool=pool,Q=78,unused_calendar_segments=k-actual,d1=d1,d2=d2,total=cost['total'],
            transport=cost['components']['e1_distance']+cost['components']['e2_distance'],
            components=cost['components'],demands=demands,returns={bid:returns[bid] for bid in demands},
            e1_trips=audit['e1_trip_count'],e2_trips=audit['e2_trip_count'],
            e1_vehicles=audit['e1_vehicle_count'],e2_vehicles=audit['e2_vehicle_count'],
            working_minutes=audit['time_metrics']['working_minutes'],
            batch_calendar=result['operating_policy']['batches'],
            periodic_replay=audit['periodic_replay'],
            old_same_k_internal_candidate=old,
            same_k_improvement=old['objective']-cost['total'] if old.get('valid') else None,
            allocated_seconds=result['count_search'][0]['budget'],runtime=row['solve_wall_seconds'],
            iterations=row['iterations_completed']))
        checks.append(dict(id=row['id'],valid=True,fixed_k_verified=k,allocated_seconds=result['count_search'][0]['budget'],
            costs_verified=True,cycles=2,minimum_pool_verified=True,actual_delivery_verified=actual))
    tradeoffs=[]
    for model in (6,7,8):
        group=[r for r in diagnostics if r['model']==model]
        base=next((r for r in group if r['fixed_k']==1),None)
        if not base: continue
        for r in group:
            saving=15*(base['pool']-r['pool'])
            dt=r['transport']-base['transport']
            dc=r['total']-base['total']
            assert math.isclose(dc,dt-saving,abs_tol=1e-6)
            tradeoffs.append(dict(id=r['id'],model=model,k=r['fixed_k'],actual_delivery_batches=r['actual_delivery_batches'],
                baseline_pool=base['pool'],pool=r['pool'],saved_boxes=base['pool']-r['pool'],pool_fee_saving=saving,
                extra_transport=dt,total_increase=dc,
                break_even_pool_price=dt/(base['pool']-r['pool']) if base['pool']>r['pool'] else None,
                genuine_multibatch_cost_win=r['actual_delivery_batches']>1 and dc<-1e-6))
    write(OUT/'verification.json',dict(checked=9,valid=sum(c['valid'] for c in checks),frozen_hashes_valid=True,checks=checks))
    write(OUT/'diagnostics.json',diagnostics); write(OUT/'tradeoffs.json',tradeoffs)
    write(OUT/'findings.json',dict(real_multibatch_solutions=sum(r['actual_delivery_batches']>1 for r in diagnostics),
        reduced_pool_solutions=sum(r['pool']<78 for r in diagnostics),
        real_multibatch_cost_wins=[r['id'] for r in tradeoffs if r['genuine_multibatch_cost_win']],
        empty_calendar_solutions=[r['id'] for r in diagnostics if r['unused_calendar_segments']>0],
        old_candidates_improved=sum(r['same_k_improvement'] is not None and r['same_k_improvement']>1e-6 for r in diagnostics)))
    render(plan,rows,diagnostics,tradeoffs)
    print(f'VERIFIED {sum(c["valid"] for c in checks)}/9 fixed-K routes, costs and two-cycle reuse')

def render(plan,rows,diagnostics,tradeoffs):
    fmt=lambda x:'—' if x is None else f'{x:,.2f}'
    by_id={r['id']:r for r in diagnostics}; ts={r['id']:r for r in tradeoffs}
    real=[r for r in diagnostics if r['actual_delivery_batches']>1]
    wins=[t for t in tradeoffs if t['genuine_multibatch_cost_win']]
    lines=['# 箱价15：固定K与相同搜索预算诊断','',
        '## Material Passport','', '- Origin Skill: academic-research-suite / experiment-agent',
        '- Origin Mode: run (continuous)', '- Origin Date: 2026-10-06',
        '- Verification Status: '+('VERIFIED' if len(diagnostics)==9 else 'PARTIAL'),
        '- Version Label: pool15_fixedK_diagnostic_v1','',
        '算例100-10-1的50点子集，Q=78；M6/M7/M8分别固定K=1/2/3。每次300秒、seed42、迭代上限1000000，3个进程并行。全部使用箱池15、一级2.535、二级0.056470588、清洗0.2、追踪0.1、磨损0.3的同一费用目标。',
        '',f'完成9/9；独立双周期审计通过{len(diagnostics)}/9。物理输入、费用和308份算法源码与上一轮一致。',
        '',f'本轮有{len(real)}条真实多批路线，其中{sum(r["pool"]<78 for r in real)}条减少箱池；相对同模型K1的真实多批费用胜出为{len(wins)}条。搜索预算相等后，当前找到的真实多批方案仍未获得净成本优势。',
        '', 'K是日历段数，现有模型允许空段；实际配送次数、减箱与净收益分别报告。',
        '', '| 模型 | 固定K | 实际配送批次 | 箱池N | D1 | D2 | 运输费 | 箱池费 | 总费用 | 相对同模型K1增量 |',
        '|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|']
    for row in sorted(rows,key=lambda r:(r['model'],r['fixed_k'])):
        if row['id'] not in by_id:
            lines.append(f'| M{row["model"]} | {row["fixed_k"]} | — | — | — | — | — | — | {row["status"]} | — |'); continue
        r=by_id[row['id']]; t=ts.get(row['id'],{})
        values=[r['actual_delivery_batches'],r['pool'],fmt(r['d1']),fmt(r['d2']),fmt(r['transport']),fmt(r['components']['container_pool']),fmt(r['total']),fmt(t.get('total_increase'))]
        lines.append(f'| M{r["model"]} | {r["fixed_k"]} | '+' | '.join(map(str,values))+' |')
    lines+=['','## 减箱与运输权衡','',
        '| 模型 | K | 省箱数 | 箱池费节省 | 新增运输费 | 总费用增量 | 当前两路线的盈亏平衡箱价 |',
        '|---|---:|---:|---:|---:|---:|---:|']
    for t in tradeoffs:
        if t['k']==1: continue
        lines.append(f'| M{t["model"]} | {t["k"]} | {t["saved_boxes"]} | {fmt(t["pool_fee_saving"])} | {fmt(t["extra_transport"])} | {fmt(t["total_increase"])} | {fmt(t["break_even_pool_price"])} |')
    example=next((t for t in tradeoffs if t['model']==7 and t['k']==2),None)
    if example:
        b=next(r for r in diagnostics if r['model']==7 and r['fixed_k']==1)
        r=next(r for r in diagnostics if r['id']==example['id'])
        lines+=['',f'M7的K1→K2：箱池{b["pool"]}→{r["pool"]}；一级距离{b["d1"]:.2f}→{r["d1"]:.2f}，一级趟次{b["e1_trips"]}→{r["e1_trips"]}；省箱费用{example["pool_fee_saving"]:.2f}，额外运输{example["extra_transport"]:.2f}，总费用增加{example["total_increase"]:.2f}。']
    lines+=['','阈值仅针对这两条固定路线；不能解释为全局最优或保证多批胜出的单价。箱次固定费用0.6Q相同，成本差完全等于运输增量减去箱池节省。',
        '', '## 与上一轮同K候选对照','',
        '| 模型 | K | 旧分配秒数 | 本轮分配秒数 | 旧同K费用 | 本轮费用 | 降低费用 |',
        '|---|---:|---:|---:|---:|---:|---:|']
    for r in diagnostics:
        o=r['old_same_k_internal_candidate']
        lines.append(f'| M{r["model"]} | {r["fixed_k"]} | {fmt(o["budget"])} | {fmt(r["allocated_seconds"])} | {fmt(o.get("objective"))} | {fmt(r["total"])} | {fmt(r["same_k_improvement"])} |')
    lines+=['', '旧候选只有求解器内部审计摘要，缺少逐一保存的路线。本轮九条路线均独立重放；不把旧候选称为独立审计方案。预算、迭代上限分配和并行负载有变化；单种子对照不能证明搜索时间是唯一原因。',
        '', '## 每批发箱和回库','', '| 模型 | K | 发箱 | 回库 | 清洗费 | 追踪费 | 磨损费 |', '|---|---:|---|---|---:|---:|---:|']
    for r in diagnostics:
        c=r['components']
        issues=', '.join(f'{k}:{v}' for k,v in r['demands'].items())
        returns=', '.join(f'{k}:{v}' for k,v in r['returns'].items())
        lines.append(f'| M{r["model"]} | {r["fixed_k"]} | {issues} | {returns} | {fmt(c["cleaning"])} | {fmt(c["tracking"])} | {fmt(c["container_wear"])} |')
    lines+=['','## 范围','',
        '- K不强制每段非空；实际配送一次的K2/K3方案不能作为真实多批收益。',
        '- 无热启动；求解器源码未改。各K分别使用相同300秒，原一百万迭代上限也完整分配给各K。',
        '- 一个算例、一个种子、有限搜索；不能推广至其他算例或证明全局最优。',
        '- 原速度2单位/分钟保持不变；本轮仍是成本比例测试。',
        '- results/保存路线、独立审计和成本；diagnostics.json记录逐批量；tradeoffs.json记录成本差。',
        '', '![固定K成本组成](cost_components.png)', '',
        '复核：`python output/model35678_comparison/20261006_pool15_fixedK_diagnostic_v1/verify_and_report.py`。']
    (OUT/'report.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    plot(diagnostics)

def plot(diagnostics):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    groups=[('Pool','#3b6ea8','container_pool'),('Truck','#e99d3c','e1_distance'),('Tricycle','#5aa27a','e2_distance')]
    fig,axes=plt.subplots(1,3,figsize=(14,5))
    for ax,model in zip(axes,(6,7,8)):
        group=[r for r in diagnostics if r['model']==model]
        for r in group:
            x=r['fixed_k']; bottom=0
            for label,color,key in groups:
                value=r['components'][key]; ax.bar(x,value,bottom=bottom,color=color,label=label if x==1 else None); bottom+=value
            other=sum(r['components'][k] for k in ('cleaning','tracking','container_wear'))
            ax.bar(x,other,bottom=bottom,color='#a0a7b0',label='Use / cleaning' if x==1 else None)
            ax.text(x,r['total']+30,f'{r["total"]:.1f}\nN={r["pool"]}, actual={r["actual_delivery_batches"]}',ha='center',fontsize=9)
        ax.set_title(f'M{model}: each K gets 300 seconds'); ax.set_xticks([1,2,3],['K1','K2','K3'])
        ax.set_ylim(0,max((r['total'] for r in group),default=1)*1.22); ax.grid(axis='y',alpha=.2); ax.set_axisbelow(True)
    axes[0].set_ylabel('Synthetic cycle cost')
    h,l=axes[0].get_legend_handles_labels(); fig.legend(h,l,loc='lower center',ncol=4,frameon=False)
    fig.suptitle('Fixed calendar K, pool 15: 50 customers, Q=78, seed 42')
    fig.tight_layout(rect=(0,.1,1,.94)); fig.savefig(OUT/'cost_components.png',dpi=180); fig.savefig(OUT/'cost_components.svg'); plt.close(fig)

if __name__=='__main__': main()
