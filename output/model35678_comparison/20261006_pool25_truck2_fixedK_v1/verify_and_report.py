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
        with tempfile.TemporaryDirectory(prefix='pool25_truck2_fixedk_') as t:
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
        direct=2*d1+(.06/.85*.8)*d2+25*pool+.6*balance['issued']+.2*returned
        assert math.isclose(direct,cost['total'],abs_tol=1e-6)
        assert math.isclose(cost['components']['cleaning'],.2*returned,abs_tol=1e-6)
        assert math.isclose(cost['components']['tracking'],.1*balance['issued'],abs_tol=1e-6)
        assert math.isclose(cost['components']['container_wear'],.5*balance['issued'],abs_tol=1e-6)
        demands={b['id']:b['container_demand'] for b in result['batches']}
        actual=sum(q>0 for q in demands.values())
        assert actual==row['delivery_batches']==result['delivery_batch_count']
        returns=defaultdict(int)
        for trip in result['e1_trips']:
            returns[trip['batch_id']]+=sum(e['quantity'] for e in trip['events'] if e['kind']=='p')
        assert sum(returns.values())==returned
        old_dir=prior/'results'/row['id']
        old_result=read(old_dir/'result.json')
        old_audit=read(old_dir/'independent_audit.json')
        old_cost=read(old_dir/'comparison_cost.json')
        assert old_audit['valid'] and old_audit['periodic_replay']['valid']
        old_pool=old_cost['container_pool_size']
        old_new_cost=problem.cost_profile.evaluate(old_audit,old_pool)
        old_direct=2*old_audit['distances']['e1']+(.06/.85*.8)*old_audit['distances']['e2']+25*old_pool+.6*old_audit['container_balance']['issued']+.2*old_audit['container_balance']['returned_to_depot']
        assert math.isclose(old_direct,old_new_cost['total'],abs_tol=1e-6)
        old=dict(valid=True,budget=old_result['count_search'][0]['budget'],objective=old_new_cost['total'],
            original_total=old_cost['total'],pool=old_pool,actual_delivery_batches=old_result['delivery_batch_count'],
            components=old_new_cost['components'],source_result_sha256=sha(old_dir/'result.json'),
            source_audit_sha256=sha(old_dir/'independent_audit.json'))
        diagnostics.append(dict(id=row['id'],model=row['model'],fixed_k=k,actual_delivery_batches=actual,
            pool=pool,Q=78,unused_calendar_segments=k-actual,d1=d1,d2=d2,total=cost['total'],
            transport=cost['components']['e1_distance']+cost['components']['e2_distance'],
            components=cost['components'],demands=demands,returns={bid:returns[bid] for bid in demands},
            e1_trips=audit['e1_trip_count'],e2_trips=audit['e2_trip_count'],
            e1_vehicles=audit['e1_vehicle_count'],e2_vehicles=audit['e2_vehicle_count'],
            working_minutes=audit['time_metrics']['working_minutes'],
            batch_calendar=result['operating_policy']['batches'],
            periodic_replay=audit['periodic_replay'],
            old_same_k_repriced_route=old,
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
            saving=25*(base['pool']-r['pool'])
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
        old_routes_improved=sum(r['same_k_improvement'] is not None and r['same_k_improvement']>1e-6 for r in diagnostics)))
    render(plan,rows,diagnostics,tradeoffs)
    print(f'VERIFIED {sum(c["valid"] for c in checks)}/9 fixed-K routes, costs and two-cycle reuse')

def render(plan,rows,diagnostics,tradeoffs):
    fmt=lambda x:'—' if x is None else f'{x:,.2f}'
    real=[r for r in diagnostics if r['actual_delivery_batches']>1]
    wins=[t for t in tradeoffs if t['genuine_multibatch_cost_win']]
    ts={t['id']:t for t in tradeoffs}
    lines=['# 箱价25、一级里程2：固定K重新优化','',
        '## Material Passport','', '- Origin Skill: academic-research-suite / experiment-agent',
        '- Origin Mode: run (continuous)', '- Origin Date: 2026-10-06',
        '- Verification Status: '+('VERIFIED' if len(diagnostics)==9 else 'PARTIAL'),
        '- Version Label: pool25_truck2_fixedK_v1','',
        '同一100-10-1的50点算例，Q=78；M6/M7/M8各固定K=1/2/3，每个独享300秒，seed42、迭代上限1000000、3个worker，无热启动。物理输入、308份算法源码、时间预算和并行方式与此前箱价15固定K实验相同。',
        '', '本轮一级大卡车系数2.535→2，箱池15→25，磨损15/50→25/50=0.5；二级电动三轮车仍0.056470588，追踪0.1，清洗按每次回库0.2。',
        '', '`C = 2D1 + 0.056470588D2 + 25N + 0.6U + 0.2R`。完整回收U=R=78，因此使用与清洗合计62.4，各方案相同。N是独立箱池，U/R是累计发箱/回库箱次。',
        '', f'独立路线与双周期审计通过{len(diagnostics)}/9；真实多批方案{len(real)}条、减箱{sum(r["pool"]<78 for r in real)}条、相对同模型K1净成本胜出{len(wins)}条。',
        '', '| 模型 | 日历K | 实际配送批次 | 箱池N | D1 | D2 | 运输费 | 箱池费 | 总成本 | 相对同模型K1增量 |',
        '|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|']
    for r in diagnostics:
        lines.append(f'| M{r["model"]} | {r["fixed_k"]} | {r["actual_delivery_batches"]} | {r["pool"]} | {fmt(r["d1"])} | {fmt(r["d2"])} | {fmt(r["transport"])} | {fmt(r["components"]["container_pool"])} | {fmt(r["total"])} | {fmt(ts[r["id"]]["total_increase"])} |')
    lines+=['', '## 箱池节省与运输增量','',
        '| 模型 | K | 省箱 | 节省箱池费 | 额外运输费 | 总成本增量 | 固定两路线盈亏平衡箱价 |',
        '|---|---:|---:|---:|---:|---:|---:|']
    for t in tradeoffs:
        if t['k']==1: continue
        lines.append(f'| M{t["model"]} | {t["k"]} | {t["saved_boxes"]} | {fmt(t["pool_fee_saving"])} | {fmt(t["extra_transport"])} | {fmt(t["total_increase"])} | {fmt(t["break_even_pool_price"])} |')
    lines+=['', '成本增量=额外运输费−节省箱池费。盈亏平衡箱价仅比较表内这两条固定路线；不代表全局最优或保证重新优化能获得多批收益。',
        '', '## 上一轮路线同价重算','',
        '上一轮箱价15/一级2.535的9条保存路线已通过独立路线和双周期审计。本表利用保存的物理指标重新计算本轮价格，未将不同价格下的总成本直接相减。',
        '', '| 模型 | K | 旧路线箱池 | 旧路线实际批次 | 旧价格总成本 | 旧路线按新价格 | 本轮新路线 | 新搜索降低成本 |',
        '|---|---:|---:|---:|---:|---:|---:|---:|']
    repriced=[]
    for r in diagnostics:
        o=r['old_same_k_repriced_route']
        lines.append(f'| M{r["model"]} | {r["fixed_k"]} | {o["pool"]} | {o["actual_delivery_batches"]} | {fmt(o["original_total"])} | {fmt(o["objective"])} | {fmt(r["total"])} | {fmt(r["same_k_improvement"])} |')
        repriced.append(dict(id=r['id'],model=r['model'],k=r['fixed_k'],**o))
    write(OUT/'previous_routes_repriced.json',repriced)
    known=[]
    for r in diagnostics:
        o=r['old_same_k_repriced_route']
        use_new=r['total']<=o['objective']
        known.append(dict(id=r['id'],model=r['model'],k=r['fixed_k'],
            source='new_search' if use_new else 'previous_verified_route_repriced',
            total=r['total'] if use_new else o['objective'],
            pool=r['pool'] if use_new else o['pool'],
            actual_delivery_batches=r['actual_delivery_batches'] if use_new else o['actual_delivery_batches']))
    write(OUT/'best_known_same_price.json',known)
    lines+=['', '## 两轮保存路线中的同价最好候选','',
        '| 模型 | K | 来源 | 实际配送批次 | 箱池 | 同价总成本 |',
        '|---|---:|---|---:|---:|---:|']
    for r in known:
        label='本轮重新搜索' if r['source']=='new_search' else '旧审计路线重计价'
        lines.append(f'| M{r["model"]} | {r["k"]} | {label} | {r["actual_delivery_batches"]} | {r["pool"]} | {fmt(r["total"])} |')
    lines+=['', '此表保留每个模型/K在两轮保存路线中按新价格计算更便宜的可行路线，仅是已知候选，不是全局最优。']
    lines+=['', '降低成本为负表示本次有限搜索比同价重算的旧保存路线更贵，应同时保留旧路线作为可行候选。时间限制搜索存在波动，不能把结果视为最优解。',
        '', '## 清洗、追踪、磨损与每批数量','',
        '| 模型 | K | 发箱量 | 回库量 | 清洗 | 追踪 | 磨损 |',
        '|---|---:|---|---|---:|---:|---:|']
    for r in diagnostics:
        c=r['components']; issue=', '.join(f'{k}:{v}' for k,v in r['demands'].items()); ret=', '.join(f'{k}:{v}' for k,v in r['returns'].items())
        lines.append(f'| M{r["model"]} | {r["fixed_k"]} | {issue} | {ret} | {fmt(c["cleaning"])} | {fmt(c["tracking"])} | {fmt(c["container_wear"])} |')
    lines+=['', '## 范围与复核','',
        '- K是日历段数，模型允许空段；实际仅配送一次的K2/K3不能算多批次复用收益。',
        '- 单个算例、单个种子、有限搜索，不证明全局最优，也不推广至全部数据集。',
        '- 原距离单位、速度2单位/分钟、时间窗和车队约束保持；这是成本比例测试，不是深圳实测运营成本。',
        '- 每个worker15秒心跳、480秒硬超时；源代码和物理输入冻结，原始路线、审计及费用明细保存在results/。',
        '', '![成本组成](cost_components.png)', '',
        '复核：`python output/model35678_comparison/20261006_pool25_truck2_fixedK_v1/verify_and_report.py`。']
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
    fig.suptitle('Pool25, truck2: 50 customers, Q=78, seed42')
    fig.tight_layout(rect=(0,.1,1,.94)); fig.savefig(OUT/'cost_components.png',dpi=180); fig.savefig(OUT/'cost_components.svg'); plt.close(fig)

if __name__=='__main__': main()
