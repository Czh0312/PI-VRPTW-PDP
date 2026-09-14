"""Report paired M4 exchange validation without selecting successful seeds."""
import csv
import argparse
import hashlib
import json
import statistics as stats
from pathlib import Path

from experiments.runs.run_m4_exchange_validation import ROOT, OUT


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('directory',type=Path,nargs='?',default=OUT)
    output=parser.parse_args().directory
    rows=[json.loads(x) for x in (output/'runs.jsonl').read_text(encoding='utf-8').splitlines()]
    manifest=json.loads((output/'manifest.json').read_text(encoding='utf-8'))
    expected={(i,s,e) for i in manifest['instances'] for s in manifest['seeds'] for e in (False,True)}
    assert len(rows)==len(expected)==132
    assert {(r['instance'],r['seed'],r['enabled']) for r in rows}==expected
    assert all(r['audit']['valid'] and abs(r['route_cost_delta'])<1e-6 for r in rows)
    for p,h in manifest['sha256'].items():
        source=ROOT/p
        snapshot=output/'source_snapshot'/source.name
        if snapshot.exists():source=snapshot
        assert hashlib.sha256(source.read_bytes()).hexdigest()==h
    summary={}
    for enabled in (False,True):
        subset=[r for r in rows if r['enabled']==enabled]
        summary['exchange' if enabled else 'baseline']=dict(runs=len(subset),hits=sum(r['hit'] for r in subset),
            mean_gap_pct=stats.mean(r['gap_pct'] for r in subset),max_gap_pct=max(r['gap_pct'] for r in subset),
            mean_seconds=stats.mean(r['wall_time'] for r in subset),
            polished_runs=sum(r['info']['exchange_polish_improvements']>0 for r in subset))
    a={(r['instance'],r['seed']):r for r in rows if not r['enabled']}
    b={(r['instance'],r['seed']):r for r in rows if r['enabled']}
    gains=[100*(a[k]['objective']-b[k]['objective'])/a[k]['objective'] for k in a]
    summary['paired']=dict(wins=sum(g>1e-6 for g in gains),ties=sum(abs(g)<=1e-6 for g in gains),
        losses=sum(g< -1e-6 for g in gains),mean_improvement_pct=stats.mean(gains))
    largest=max(a,key=lambda k:a[k]['gap_pct'])
    summary['sensitivity_without_largest_baseline_gap']=dict(
        excluded_pair=largest,baseline_mean_gap_pct=stats.mean(r['gap_pct'] for k,r in a.items() if k!=largest),
        exchange_mean_gap_pct=stats.mean(r['gap_pct'] for k,r in b.items() if k!=largest))
    summary['verification']=dict(audited=132,unique_runs=132,source_input_hashes_unchanged=True,
        max_distance_error=max(abs(r['route_cost_delta']) for r in rows))
    (output/'summary.json').write_text(json.dumps(summary,indent=2),encoding='utf-8')
    fields=['instance','seed','enabled','objective','exact_objective','gap_pct','hit','wall_time']
    with (output/'paired_results.csv').open('w',encoding='utf-8-sig',newline='') as handle:
        writer=csv.DictWriter(handle,fieldnames=fields,extrasaction='ignore');writer.writeheader();writer.writerows(rows)
    lines=['# M4 原子交换邻域验证','',f"22 个标准当日小规模实例，seed={manifest['seeds']}，独立从头求解，两个配置交替执行；各 66 次。每次共享 5 秒预算，最多 1,000,000 次 ALNS 迭代。",
        f"新配置预留 {manifest.get('polish_reserve_seconds',0.25)} 秒给最好可行解的顾客对交换和同仓路线尾段交换；原配置 exchange_polish=False。均为单阶段 ALNS，不传入精确目标。",
        '', '| 配置 | 命中/66 | 平均偏差% | 最大偏差% | 平均秒 |', '| --- | ---: | ---: | ---: | ---: |']
    for key in ('baseline','exchange'):
        s=summary[key]
        lines.append(f"| {key} | {s['hits']}/66 | {s['mean_gap_pct']:.4f} | {s['max_gap_pct']:.4f} | {s['mean_seconds']:.4f} |")
    p=summary['paired']
    lines += ['',f"新配置：{p['wins']} 胜 / {p['ties']} 平 / {p['losses']} 负；逐实例/种子平均距离改善 {p['mean_improvement_pct']:.4f}%。",'',
        '| 实例 | 原平均偏差% | 新平均偏差% | 原命中/3 | 新命中/3 |','| --- | ---: | ---: | ---: | ---: |']
    for name in manifest['instances']:
        x=[r for r in rows if r['instance']==name and not r['enabled']]
        y=[r for r in rows if r['instance']==name and r['enabled']]
        lines.append(f"| {name} | {stats.mean(r['gap_pct'] for r in x):.4f} | {stats.mean(r['gap_pct'] for r in y):.4f} | {sum(r['hit'] for r in x)}/3 | {sum(r['hit'] for r in y)}/3 |")
    lines += ['','132 次输出均通过独立任务覆盖、容量、时间窗、配送先于回收、E1/E2 同步与距离复算检查。源码和输入哈希在验证期间未变。',
        '该验证使用额外种子，不把早期保存解上的额外后处理收益当成同预算收益。三个种子的表现不能保证所有实例或随机种子均改善。',
        '原先 66 次 M7 均已运行；只有 13 个实例×3 个种子具有精确最优值，因此此前 M7 的命中分母为 39。此报告仅优化 M4。']
    sensitivity=summary['sensitivity_without_largest_baseline_gap']
    lines += ['',f"敏感性检查：排除原配置偏差最大的配对 {largest} 后，原/新平均偏差为 {sensitivity['baseline_mean_gap_pct']:.4f}% / {sensitivity['exchange_mean_gap_pct']:.4f}%。主表仍保留全部配对，不删除极端结果。"]
    if manifest.get('preserve_nominal_schedule'):
        lines += ['','最终版本保留原 5 秒降温、停滞和重启参数，仅将 ALNS 搜索硬截止设为 4.95 秒，剩余时间进行交换检查。',
                  'M4 在至多 20 客户、按时间降温时默认启用；exchange_polish=False 可关闭。其他模型和大规模默认搜索策略未改变。']
    (output/'report.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    print(json.dumps(summary,indent=2))


if __name__=='__main__':
    main()
