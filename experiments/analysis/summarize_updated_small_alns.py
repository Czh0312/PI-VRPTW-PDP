"""Updated six-model tables with explicit retained/refreshed batch provenance."""
import csv
import hashlib
import json
import statistics as stats

from experiments.runs.update_small_alns_comparison import OUT, PREVIOUS, ROOT
from experiments.runs.run_small_alns_validation import read_rows
from experiments.runs.run_model12346_alns_scaling import load_problem, route_cost
from models.routing.common.solution_audit import audit_exact_solution


def summarize(rows):
    paired=[r for r in rows if r['valid'] and r['exact_objective'] is not None]
    return dict(runs=len(rows),valid=sum(r['valid'] for r in rows),exact_pairs=len(paired),
        hits=sum(abs(r['objective']-r['exact_objective'])<=1e-5 for r in paired),
        mean_gap_pct=stats.mean(r['gap_pct'] for r in paired) if paired else None,
        max_gap_pct=max(r['gap_pct'] for r in paired) if paired else None,
        mean_seconds=stats.mean(r['wall_time'] for r in rows))


def compare(left,right):
    a={(r['instance'],r['seed']):r for r in left if r['valid']}
    b={(r['instance'],r['seed']):r for r in right if r['valid']}
    gains=[100*(a[k]['objective']-b[k]['objective'])/a[k]['objective'] for k in sorted(a.keys() & b.keys())]
    return dict(pairs=len(gains),mean_improvement_pct=stats.mean(gains),
        wins=sum(g>1e-6 for g in gains),ties=sum(abs(g)<=1e-6 for g in gains),losses=sum(g< -1e-6 for g in gains))


def main():
    rows=read_rows(OUT/'runs.jsonl')
    manifest=json.loads((OUT/'manifest.json').read_text(encoding='utf-8'))
    retention=json.loads((OUT/'retention_check.json').read_text(encoding='utf-8'))
    assert all(retention[k] for k in ('retained_data_and_fleet_unchanged',
        'retained_model_files_unchanged','m4_operators_and_hooks_used_by_m7_unchanged'))
    expected={(i,s,m) for i in manifest['instances'] for s in manifest['seeds'] for m in manifest['models']}
    assert len(rows)==len(expected)==396
    assert {(r['instance'],r['seed'],r['model']) for r in rows}==expected
    assert all(hashlib.sha256((ROOT/p).read_bytes()).hexdigest()==h for p,h in manifest['sha256'].items())
    audits=[]
    for r in rows:
        problem=load_problem(ROOT/'data/zhou_later_pickup/small/standard_same_day'/f"{r['instance']}.csv",manifest['fleet'])
        e1=r['e1_routes']
        if r['model']==1:
            mapping={s.id+'d':s.id for s in problem.satellites}
            e1=[[mapping.get(n,n) for n in route] for route in e1]
        assert not r['info'].get('transshipment_count',0), 'Relay routes require a relay-aware audit'
        audit=audit_exact_solution(problem,{6:3,7:4}.get(r['model'],r['model']),e1,r['e2_routes'],
            assignment=r['assignment'],objective=r['objective'])
        assert audit['valid'] and abs(route_cost(problem,r['e1_routes'],r['e2_routes'])-r['objective'])<1e-6
        audits.append(dict(instance=r['instance'],seed=r['seed'],model=r['model'],audit=audit))
    (OUT/'independent_audits.json').write_text(json.dumps(audits,ensure_ascii=False,indent=2),encoding='utf-8')
    models={m:summarize([r for r in rows if r['model']==m]) for m in manifest['models']}
    old_m4=[r for r in read_rows(PREVIOUS) if r['model']==4]
    new_m4=[r for r in rows if r['model']==4]
    changes=compare(old_m4,new_m4)
    pairs={f'M{a}_M{b}':compare([r for r in rows if r['model']==a],[r for r in rows if r['model']==b])
           for a,b in ((1,2),(3,4),(3,6),(4,7),(6,7))}
    certified_m7={r['instance'] for r in rows if r['model']==7 and r['exact_objective'] is not None}
    common={m:summarize([r for r in rows if r['model']==m and r['instance'] in certified_m7]) for m in (4,7)}
    summary=dict(models=models,pairs=pairs,m4_old=summarize(old_m4),m4_refresh=changes,
        common_certified_m4_m7=common,verification=dict(total=396,rerun=66,retained=330,
        independent_audits=396,source_input_hashes_unchanged=True,retained_components_verified=True))
    (OUT/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
    fields=['instance','customers','seed','model','valid','objective','exact_objective','gap_pct','wall_time','source_batch','rerun']
    with (OUT/'paired_results.csv').open('w',encoding='utf-8-sig',newline='') as handle:
        writer=csv.DictWriter(handle,fieldnames=fields,extrasaction='ignore');writer.writeheader();writer.writerows(rows)
    lines=['# 更新后的六模型小规模 ALNS 对比','',
        '范围：22 个标准当日实例，7/10/12 客户；seed=42/43/44；每次上限 5 秒，最多 1,000,000 次 ALNS 迭代。',
        '**批次说明：新版 M4 重跑 66 次；M1/M2/M3/M6/M7 沿用原批次的 330 次验证结果。这不是六个模型在同一新批次全部重跑。**',
        '各模型独立求解，无跨模型热启动，也未向 ALNS 提供最优目标。每条数据均保留 source_batch 和 rerun 字段。',
        'M4 新版保持原降温节奏，在总预算内预留最多 0.05 秒执行完整顾客交换和同仓路线尾段交换；模型数学约束与精确参照值未改变。',
        '', '| 模型 | 可行/运行 | 精确配对 | 命中最优 | 命中率 | 平均偏差% | 最大偏差% | 平均秒 | 来源 |',
        '| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |']
    for m,s in models.items():
        lines.append(f"| M{m} | {s['valid']}/{s['runs']} | {s['exact_pairs']} | {s['hits']} | {100*s['hits']/s['exact_pairs']:.2f}% | {s['mean_gap_pct']:.4f} | {s['max_gap_pct']:.4f} | {s['mean_seconds']:.4f} | {'新版重跑' if m==4 else '沿用原批次'} |")
    lines += ['','M7 实际运行 66 次，其中仅 13 个实例×3 个种子有严格最优值，命中率分母为 39；其余 27 次也是可行解，但不计算真实最优值偏差。不同模型样本数不同时不能直接比较平均偏差。',
        '', '## M4 更新前后', '', '| 版本 | 命中/66 | 平均偏差% | 最大偏差% |', '| --- | ---: | ---: | ---: |']
    for label,s in [('原批次 M4',summary['m4_old']),('新版 M4',models[4])]:
        lines.append(f"| {label} | {s['hits']}/66 | {s['mean_gap_pct']:.4f} | {s['max_gap_pct']:.4f} |")
    lines += ['',f"同实例、同种子更新前后为 {changes['wins']} 胜 / {changes['ties']} 平 / {changes['losses']} 负。跨批次历史比较仅作描述，不能单独用于严格归因或速度提升结论；新旧算法交替运行的受控对照见 ../m4_exchange_schedule_20260908/report.md。",'',
        '## 更新后的模型间配对', '', '正数表示后者距离更短。所有比较均使用全部 66 个共同可行的实例/种子组合。', '',
        '| 比较 | 平均距离改善% | 后者胜/平/负 |','| --- | ---: | ---: |']
    for name,p in pairs.items():
        lines.append(f"| {name} | {p['mean_improvement_pct']:.4f} | {p['wins']}/{p['ties']}/{p['losses']} |")
    lines += ['', '## M4 与 M7 的共同精确样本', '', '只比较 M7 已有最优性证明的相同 13 个实例、39 次运行。', '',
        '| 模型 | 命中/39 | 平均偏差% |','| --- | ---: | ---: |']
    for m,s in common.items():lines.append(f"| M{m} | {s['hits']}/39 | {s['mean_gap_pct']:.4f} |")
    lines += ['', '## 逐实例 M4 更新', '', '| 实例 | 原平均偏差% | 新平均偏差% | 原命中/3 | 新命中/3 |', '| --- | ---: | ---: | ---: | ---: |']
    for name in manifest['instances']:
        a=summarize([r for r in old_m4 if r['instance']==name]);b=summarize([r for r in new_m4 if r['instance']==name])
        lines.append(f"| {name} | {a['mean_gap_pct']:.4f} | {b['mean_gap_pct']:.4f} | {a['hits']}/3 | {b['hits']}/3 |")
    lines += ['', '## 验证与来源', '', '396 条路线均重新通过独立任务覆盖、容量、时间窗、先送后取、两级同步和成本审计；没有交接路线。',
        '原始六模型数据：../small_alns_validation_20260908/verified_runs.jsonl。本次新增：rerun_m4.jsonl；合并数据：runs.jsonl；逐行来源：paired_results.csv。',
        '原报告作为历史批次保留。本报告是 M4 默认优化后的更新总表。命中已知最优值不等于 ALNS 自行证明最优。']
    (OUT/'report.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    print(json.dumps(summary,ensure_ascii=False,indent=2))


if __name__=='__main__':
    main()
