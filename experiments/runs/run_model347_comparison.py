"""Reproducible paired ALNS comparison of M3/M4/M7; no exact claims."""
from __future__ import annotations
import argparse
import csv
import hashlib
import json
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from experiments.runs.run_model12346_alns_scaling import load_problem, route_cost, solve_one


def summarize(rows, directory):
    good = [r for r in rows if r['feasible']]
    lines = ['# M3 / M4 / M7 标准同日回收 ALNS 对比', '',
             '每个实例使用 seed=42、43、44；同规模各模型预算相同，串行运行。'
             '小规模 1 秒/次，大规模 3 秒/次，迭代上限 1,000,000。'
             'M7 使用当前默认四分仓候选池，分仓阶段占 75%，交接使用剩余 25%。', '',
             '这些是短预算启发式结果，不是全局最优值；距离使用 Zhou 坐标单位。'
             '可行性来自模型自身评估器，另按输出路线独立复算距离。', '',
             '| 规模 | 模型 | 可行次数/总次数 | 可行解平均距离 | 平均实际秒数 | 有交接次数 |',
             '| --- | --- | ---: | ---: | ---: | ---: |']
    for scale in ('small','large'):
        for model in (3,4,7):
            subset=[r for r in rows if r['scale']==scale and r['model']==model]
            valid=[r for r in subset if r['feasible']]
            mean=statistics.mean(r['objective'] for r in valid) if valid else float('nan')
            lines.append(f"| {scale} | M{model} | {len(valid)}/{len(subset)} | {mean:.4f} | {statistics.mean(r['wall_time'] for r in subset):.3f} | {sum(r['transshipment_count']>0 for r in valid)} |")
    lines += ['', '上表平均距离可能来自不同可行子集；优劣以以下共同可行的配对结果为准。', '',
              '| 规模 | 比较 | 配对次数 | 平均距离改善率 | 后者胜/平/负 |',
              '| --- | --- | ---: | ---: | --- |']
    lookup={(r['scale'],r['instance'],r['seed'],r['model']):r for r in good}
    for scale in ('small','large'):
        for a,b in ((3,4),(3,7),(4,7)):
            pairs=[(r,lookup[(scale,r['instance'],r['seed'],b)]) for r in good
                   if r['scale']==scale and r['model']==a and (scale,r['instance'],r['seed'],b) in lookup]
            gains=[100*(x['objective']-y['objective'])/x['objective'] for x,y in pairs]
            wins=sum(x['objective']-y['objective']>1e-6 for x,y in pairs)
            losses=sum(y['objective']-x['objective']>1e-6 for x,y in pairs)
            lines.append(f"| {scale} | M{a} → M{b} | {len(pairs)} | {statistics.mean(gains) if gains else float('nan'):.3f}% | {wins}/{len(pairs)-wins-losses}/{losses} |")
    lines += ['', '改善率为每个共同可行（实例、seed）对的 `(前者−后者)/前者` 的算术平均；正数表示后者距离更短。', '',
              '## 逐实例结果', '', '均值 ± 样本标准差，仅使用可行 seed；括号内为可行次数。', '',
              '| 规模 | 实例 | M3 | M4 | M7 |', '| --- | --- | ---: | ---: | ---: |']
    aggregate=[]
    for scale,instance in sorted({(r['scale'],r['instance']) for r in rows}):
        cells=[]
        for model in (3,4,7):
            values=[r['objective'] for r in good if (r['scale'],r['instance'],r['model'])==(scale,instance,model)]
            mean=statistics.mean(values) if values else None
            sd=statistics.stdev(values) if len(values)>1 else None
            aggregate.append(dict(scale=scale,instance=instance,model=model,feasible_seeds=len(values),mean=mean,std=sd))
            cells.append('— (0)' if not values else f"{mean:.3f} ± {sd if sd is not None else 0:.3f} ({len(values)})")
        lines.append(f"| {scale} | {instance} | {' | '.join(cells)} |")
    lines += ['', '## 复核与失败记录', '']
    errors=[r for r in rows if not r['feasible']]
    for r in errors:
        lines.append(f"- {r['scale']} / {r['instance']} / M{r['model']} / seed={r['seed']}: {r['status']} {r.get('error','')}")
    lines.append(f"- 最大距离复算绝对误差：{max((abs(r['route_cost_delta']) for r in good),default=0):.3g}。")
    lines.append('- 限时未找到可行解不等同于数学不可行。原始运行设置、代码 SHA256、路线和候选池统计保存在同目录。')
    (directory/'report.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    for name,data in (('runs.csv',rows),('instance_summary.csv',aggregate)):
        fields=list(dict.fromkeys(k for r in data for k in r))
        with (directory/name).open('w',newline='',encoding='utf-8-sig') as f:
            writer=csv.DictWriter(f,fieldnames=fields);writer.writeheader();writer.writerows(data)


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--output', required=True)
    args=parser.parse_args()
    directory=ROOT/args.output
    directory.mkdir(parents=True,exist_ok=True)
    raw=directory/'runs.jsonl'
    rows=[json.loads(line) for line in raw.read_text().splitlines()] if raw.exists() else []
    done={(r['scale'],r['instance'],r['seed'],r['model']) for r in rows}
    sources=[Path(__file__),ROOT/'experiments/runs/run_model12346_alns_scaling.py',*sorted((ROOT/'models/routing/alns').rglob('*.py'))]
    metadata={'scenario':'standard_same_day','seeds':[42,43,44],'budgets':{'small':1,'large':3},'iteration_limit':1000000,
              'source_sha256':{str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in sources}}
    meta_path=directory/'settings.json'
    if meta_path.exists() and json.loads(meta_path.read_text()) != metadata:
        raise RuntimeError('Settings/code changed: use a new output directory')
    meta_path.write_text(json.dumps(metadata,indent=2))
    for scale,budget in (('small',1.0),('large',3.0)):
        fleet=json.loads((ROOT/f'config/routing/validation/zhou_{scale}.json').read_text())
        for index,path in enumerate(sorted((ROOT/f'data/zhou_later_pickup/{scale}/standard_same_day').glob('*.csv'))):
            for seed in (42,43,44):
                models=[3,4,7]
                shift=(index+seed)%3
                for model in models[shift:]+models[:shift]:
                    if (scale,path.stem,seed,model) in done:
                        continue
                    problem=load_problem(path,fleet)
                    started=time.perf_counter()
                    row=dict(scale=scale,instance=path.stem,model=model,seed=seed,time_budget=budget)
                    try:
                        r1,r2,assignment,info=solve_one(model,problem,max_time=budget,iterations=1000000,seed=seed)
                        elapsed=time.perf_counter()-started
                        cost=route_cost(problem,r1,r2)
                        objective=info.get('objective')
                        delta=cost-objective if cost is not None and objective is not None else None
                        feasible=bool(info.get('feasible')) and delta is not None and abs(delta)<1e-6
                        row.update(feasible=feasible,status=info.get('status'),objective=objective if feasible else None,
                                   wall_time=elapsed,route_cost_delta=delta,transshipment_count=info.get('transshipment_count',0),
                                   transshipment_improvement=info.get('transshipment_improvement',0),
                                   stage1_best_objective=info.get('stage1_best_objective'),
                                   free_assignment_objective=info.get('free_assignment_objective'),
                                   assignment_candidates_evaluated=info.get('assignment_candidates_evaluated'),
                                   iterations=info.get('iterations'),assignment=assignment,e1_routes=r1,e2_routes=r2)
                    except Exception as exc:
                        row.update(feasible=False,status='ERROR',objective=None,wall_time=time.perf_counter()-started,
                                   route_cost_delta=None,transshipment_count=0,error=repr(exc))
                    rows.append(row)
                    with raw.open('a') as f: f.write(json.dumps(row,ensure_ascii=False)+'\n')
                    print(f"{len(rows)}/360 {scale} {path.stem} M{model} seed={seed}: {row['objective']} ({row['wall_time']:.2f}s)",flush=True)
    summarize(rows,directory)

if __name__=='__main__':
    main()
