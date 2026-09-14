"""Build a traceable report from the frozen-source serial campaign."""
import json
import math
import re
import statistics as stats
from datetime import datetime
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT/'output/experiments/solver_optimization_20260913'
MODELS = (1,2,3,4,6,7)


def read(path):
    return [json.loads(s) for s in path.read_text(encoding='utf-8').splitlines() if s.strip()] if path.exists() else []


def finite(v):
    return isinstance(v,(int,float)) and math.isfinite(v) and abs(v)<1e90


def fmt(v, digits=3):
    return f'{v:.{digits}f}' if finite(v) else '—'


def mean(v):
    return stats.mean(v) if v else None


def run_key(r):
    return r['instance'], r['model'], r['seed']


def quality_pairs(base, current):
    old = {run_key(r):r for r in base}
    pairs = [(old[run_key(r)],r) for r in current if run_key(r) in old
             and old[run_key(r)]['valid'] and r['valid']]
    assert all(b['budget']==c['budget'] and b['scenario']==c['scenario'] for b,c in pairs)
    return dict(valid_pairs=len(pairs),
        mean_gain_percent=mean([(b['objective']-c['objective'])/b['objective']*100 for b,c in pairs]),
        wins=sum(c['objective']<b['objective']-1e-6 for b,c in pairs),
        ties=sum(abs(c['objective']-b['objective'])<=1e-6 for b,c in pairs),
        losses=sum(c['objective']>b['objective']+1e-6 for b,c in pairs))


def main():
    progress = json.loads((OUT/'campaign_progress.json').read_text(encoding='utf-8'))
    post_path = OUT/'post_progress.json'
    post_status = json.loads(post_path.read_text()).get('status') if post_path.exists() else 'pending'
    free_seed_path = OUT/'free_seed_progress.json'
    free_seed_status = json.loads(free_seed_path.read_text()).get('status') if free_seed_path.exists() else 'pending'
    adopted_path = OUT/'adopted_progress.json'
    adopted_status = json.loads(adopted_path.read_text()).get('status') if adopted_path.exists() else 'pending'
    policy_path = OUT/'final_policy.json'
    policy = json.loads(policy_path.read_text(encoding='utf-8')) if policy_path.exists() else {'status':'pending'}
    complete = (progress['status']=='complete' and post_status=='complete' and free_seed_status=='complete'
                and adopted_status=='complete' and policy['status']=='complete')
    stages = {p.parent.name:read(p) for p in OUT.glob('*/runs.jsonl')
              if p.parent.name.startswith(('control_','final_','proof_extension_','adopted_','m6_ratio'))}
    for name, rows in stages.items():
        stages[name] = [{**r,'record_source':name+'/runs.jsonl'} for r in rows]
    timing_repeats = []
    for name,rows in stages.items():
        for index,r in enumerate(rows):
            if r['wall_time'] <= max(60,10*r['budget']):
                continue
            repeat = OUT/'timing_repeat'/name/f"{r['instance']}_M{r['model']}_S{r['seed']}"/'runs.jsonl'
            rs = read(repeat)
            replacement = next((s for s in rs if s['wall_time']<=max(60,10*s['budget'])),None)
            timing_repeats.append(dict(stage=name,instance=r['instance'],model=r['model'],seed=r['seed'],
                original_wall_time=r['wall_time'],replacement_source=str(repeat.relative_to(OUT)),
                resolved=replacement is not None))
            if replacement:
                rows[index] = {**replacement,'record_source':str(repeat.relative_to(OUT)),
                               'timing_replacement_source':str(repeat.relative_to(OUT))}
            else:
                rows[index] = {**r,'timing_unresolved':True}
    historic = [ROOT/'output/results/final_freeze_20260830/exact/small_exact_20260830_220305.jsonl',
                ROOT/'output/results/m7_optimization_20260901/proof_auto_strategy_40_20260901_202801.jsonl']
    references = {}
    for p in historic:
        for r in read(p):
            if r.get('proven_optimal') and finite(r.get('objective')):
                key = (r['instance'],r.get('model',7))
                references[key] = dict(objective=r['objective'],source=str(p.relative_to(ROOT)))
    failures = [dict(kind='unresolved_timing_anomaly',**r) for r in timing_repeats if not r['resolved']]
    exact_groups = defaultdict(list)
    for name,rows in stages.items():
        if name in ('final_exact','adopted_exact_m7','adopted_m7_proof90') or name.startswith('proof_extension_'):
            for r in rows:
                r = {**r,'result_source':r.get('timing_replacement_source',name+'/runs.jsonl')}
                exact_groups[(r['instance'],r['model'])].append(r)
                old = references.get((r['instance'],r['model']))
                if old and r['valid'] and (r['objective'] < old['objective']-1e-5 or
                    (r['proven_optimal'] and abs(r['objective']-old['objective'])>1e-5)):
                    failures.append(dict(kind='historical_optimum_conflict',run=r['result_source'],
                                         instance=r['instance'],model=r['model']))
    latest = {}
    selected_records = []
    for key,rs in exact_groups.items():
        feasible = [r for r in rs if r['valid']]
        selected = min(feasible,key=lambda r:r['objective']) if feasible else rs[-1]
        proofs = [r for r in feasible if r['proven_optimal']]
        if proofs:
            adopted_proofs = [r for r in proofs if r['record_source'].startswith('adopted_')]
            selected = min(adopted_proofs or proofs,key=lambda r:r['wall_time'])
            references[key] = dict(objective=selected['objective'],source=selected['result_source'])
        bounds = [r.get('best_bound') for r in rs if finite(r.get('best_bound'))]
        bound = max(bounds) if bounds else None
        latest[key] = dict(instance=key[0],model=key[1],objective=selected['objective'],
            valid=selected['valid'],proven_optimal=bool(proofs),best_bound=bound,
            gap_percent=(max(0,(selected['objective']-bound)/abs(selected['objective']))*100
                         if selected['valid'] and bound is not None else None),
            selected_source=selected['result_source'],selected_run_wall_time=selected['wall_time'],
            attempted_budgets=[r['budget'] for r in rs],
            total_experiment_wall_time=sum(r['wall_time'] for r in rs))
        if selected['valid'] and bound is not None and bound>selected['objective']+1e-5:
            failures.append(dict(kind='lower_bound_above_incumbent',instance=key[0],model=key[1]))
        selected_records.append({**selected,'aggregate_result':latest[key]})
    source_overrides = []
    def overlay(base_name, names):
        merged = {run_key(r):r for r in stages.get(base_name,[])}
        for name in names:
            if not (OUT/name/'COMPLETE').exists():
                continue
            rows = stages.get(name,read(OUT/name/'runs.jsonl'))
            count = 0
            for r in rows:
                k = run_key(r)
                if k in merged:
                    assert merged[k]['budget']==r['budget'] and merged[k]['scenario']==r['scenario']
                    merged[k] = {**r,'record_source':name+'/runs.jsonl'}
                    count += 1
            source_overrides.append(dict(base=base_name,replacement=name,rows=count))
        return list(merged.values())
    # Policies are substituted as complete batches, never by best individual seed.
    small = overlay('final_small_alns',['adopted_small_m4','adopted_small_m7'])
    m6_ratio = policy.get('m6_stage1_ratio')
    m6_source = f'm6_ratio{round(m6_ratio*100)}_seed42' if m6_ratio is not None else 'adopted_large_m6'
    large = overlay('final_large_alns',['adopted_large_m2',m6_source,'adopted_large_m7'])
    full_adoption = {m:quality_pairs([r for r in stages.get('final_large_alns',[]) if r['model']==m],
                                  [r for r in large if r['model']==m]) for m in (2,6,7)}
    small_adoption = {m:quality_pairs([r for r in stages.get('final_small_alns',[]) if r['model']==m],
                                   [r for r in small if r['model']==m]) for m in (4,7)}
    exact15 = overlay('final_exact',['adopted_exact_m7'])
    exact_summary = {m:dict(runs=len(rs),valid=sum(r['valid'] for r in rs),
        proven15=sum(r['proven_optimal'] for r in rs),mean_wall_time=mean([r['wall_time'] for r in rs]),
        proven_final=sum(s['proven_optimal'] for k,s in latest.items() if k[1]==m))
        for m in MODELS for rs in [[r for r in exact15 if r['model']==m]]}
    comparisons = {}
    for suffix in ('exact','small','large'):
        base = stages.get('control_baseline_'+suffix,[])
        current = stages.get('control_current_'+suffix,[])
        if suffix=='exact':
            current = overlay('control_current_exact',['adopted_exact_m7'])
        old = {(r['instance'],r['model'],r['seed']):r for r in base}
        comparisons[suffix] = {}
        for m in MODELS:
            pairs = [(old[(r['instance'],m,r['seed'])],r) for r in current
                     if r['model']==m and (r['instance'],m,r['seed']) in old
                     and not r.get('timing_unresolved')
                     and not old[(r['instance'],m,r['seed'])].get('timing_unresolved')]
            valid = [(b,c) for b,c in pairs if b['valid'] and c['valid']]
            speeds = [(b,c) for b,c in valid if b['proven_optimal'] and c['proven_optimal']]
            comparisons[suffix][m] = dict(pairs=len(pairs),
                baseline_valid=sum(b['valid'] for b,c in pairs),current_valid=sum(c['valid'] for b,c in pairs),
                baseline_proven=sum(b['proven_optimal'] for b,c in pairs),current_proven=sum(c['proven_optimal'] for b,c in pairs),
                baseline_mean_wall_time=mean([b['wall_time'] for b,c in pairs]),
                current_mean_wall_time=mean([c['wall_time'] for b,c in pairs]),
                paired_quality_count=len(valid),
                mean_improvement_percent=mean([(b['objective']-c['objective'])/b['objective']*100 for b,c in valid]),
                wins=sum(c['objective']<b['objective']-1e-6 for b,c in valid),
                ties=sum(abs(c['objective']-b['objective'])<=1e-6 for b,c in valid),
                losses=sum(c['objective']>b['objective']+1e-6 for b,c in valid),
                iteration_ratio=mean([c['info']['iterations']/b['info']['iterations'] for b,c in valid
                                      if b['info'].get('iterations',0)>0 and c['info'].get('iterations') is not None]),
                common_proven_count=len(speeds),
                common_proven_time_ratio=(sum(b['wall_time'] for b,c in speeds)/sum(c['wall_time'] for b,c in speeds) if speeds else None))
    small_summary = {}
    for m in MODELS:
        rs = [r for r in small if r['model']==m]
        gaps = [(r,(r['objective']/references[(r['instance'],m)]['objective']-1)*100)
                for r in rs if r['valid'] and (r['instance'],m) in references]
        small_summary[m] = dict(runs=len(rs),valid=sum(r['valid'] for r in rs),reference_runs=len(gaps),
            optimal_hits=sum(abs(g)<1e-5 for r,g in gaps),mean_gap_percent=mean([max(0,g) for r,g in gaps]),
            max_gap_percent=max([max(0,g) for r,g in gaps],default=None),
            mean_wall_time=mean([r['wall_time'] for r in rs]))
        for r,g in gaps:
            if g < -1e-5:
                failures.append(dict(kind='alns_below_proven_optimum',instance=r['instance'],model=m,seed=r['seed']))
    for name,rs in stages.items():
        for r in rs:
            if r.get('error'):
                failures.append(dict(kind='exception',stage=name,instance=r['instance'],model=r['model'],error=r['error']))
            if name.startswith(('final_','proof_extension_','adopted_','m6_ratio')) and r['objective'] is not None and not r['valid']:
                failures.append(dict(kind='invalid_solution',stage=name,instance=r['instance'],model=r['model']))
    if complete:
        for name,n in [('final_exact',132),('final_small_alns',396),('final_large_alns',108),
                       ('control_baseline_exact',24),('control_current_exact',24),
                       ('control_baseline_small',72),('control_current_small',72),
                       ('control_baseline_large',36),('control_current_large',36),
                       ('adopted_exact_m7',22),('adopted_small_m4',66),('adopted_small_m7',66),
                       ('adopted_large_m2',18),('adopted_large_m6',18),('adopted_large_m7',18),
                       *[(f'm6_ratio{ratio}_seed{seed}',18) for ratio in (80,90) for seed in (42,43)]]:
            if len(stages.get(name,[]))!=n:
                failures.append(dict(kind='incomplete_stage',stage=name,expected=n,actual=len(stages.get(name,[]))))
        expected_proof90 = sum(not r['proven_optimal'] for r in stages['adopted_exact_m7'])
        if len(stages.get('adopted_m7_proof90',[]))!=expected_proof90:
            failures.append(dict(kind='incomplete_m7_extension',expected=expected_proof90))
    generated_at = datetime.now().astimezone().isoformat(timespec='seconds')
    test_path = next((OUT/n for n in ('release_pytest.log','adopted_pytest.log','final_pytest.log') if (OUT/n).exists()),None)
    test_log = test_path.read_text(encoding='utf-8') if test_path else ''
    test_passes = re.findall(r'(\d+) passed',test_log)
    test_count = int(test_passes[-1]) if test_passes else None
    summary = dict(complete=complete,generated_at=generated_at,stage_counts={k:len(v) for k,v in stages.items()},
        comparisons=comparisons,latest_exact=list(latest.values()),small_alns=small_summary,
        references=[dict(instance=k[0],model=k[1],**v) for k,v in sorted(references.items())],
        validation_failures=failures,timing_repeats=timing_repeats,test_count=test_count,
        post_validation_status=post_status,free_seed_validation_status=free_seed_status,
        adopted_validation_status=adopted_status,final_policy=policy,source_overrides=source_overrides,
        exact_summary=exact_summary,large_final_policy_vs_cached=full_adoption,
        small_final_policy_vs_cached=small_adoption,
        large_valid=sum(r['valid'] for r in large),large_runs=len(large),
        test_log=str(test_path.relative_to(OUT)) if test_path else None)
    (OUT/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
    for name, rows in [('latest_small_alns',small),('latest_large_alns',large),('latest_exact_runs',selected_records)]:
        (OUT/(name+'.jsonl')).write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in sorted(rows,key=run_key)),encoding='utf-8')
    lines = ['# 六模型求解器优化与最新结果（2026-09-13 启动批次）','',
        ('本轮正式验证已完成。' if complete else '**运行中：本表是进度快照，尚非最终结论。**'),'',
        f'表格生成时间：{generated_at}。本次全量覆盖 `standard_same_day` 情景；其他情景不能直接沿用这里的质量统计。','',
        '详细原理、等价性和修改取舍见 [求解器分析](../../../docs/solver_optimization_20260913.md)。',
        '全部目标值为总路线距离，越小越好。不同模型的业务机制不同，不能把跨模型距离差直接解释为算法改进。','',
        '## 实验口径','',
        '- 正式对照：4 个困难小实例；精确入口各 15 秒、4 线程、Seed 42；ALNS 各 3 秒、Seed 42/43/44。',
        '- 大规模对照：100/200 客户的 3 个代表实例，各 5 秒、Seed 42/43。',
        '- 最新全量：22 个小实例 × 6 模型；精确先运行 15 秒，未证明者独立重跑 90 秒；ALNS 三种子各 5 秒。18 个大实例的六模型各 5 秒、Seed 42。',
        '- 全部串行，PYTHONHASHSEED=0；不向 ALNS 提供已知最优目标，最多 100 万次迭代。算法初始化计入墙钟时间；短时间预算可能有少量不可中断步骤的超时。',
        '- 第一轮配对 ALNS 固定 M6 阶段一比例 0.8、M7 为 0.75，用来隔离缓存等实现改动。随后单独筛选默认参数并全量复核；两轮改善率不能相加。CLI 默认迭代上限及 M7 比例覆盖错误的修复不计入配对收益。',
        '- 基线为修改前本地代码快照（含用户原有未提交修改）；第一版冻结在 [final_source](final_source)，参数复核版在 [adopted_source](adopted_source)，最终选择见 [final_policy.json](final_policy.json)。每批 manifest 保存代码及输入 SHA-256、环境、预算和参数。',
        '- 最新 ALNS 由行为未改变的第一版批次与已完成的参数复核批次组成；整批替换，不按单个种子挑较好值。每条最新记录的 record_source 可定位原始路线。',
        '- 历史已证明最优值只用于结果交叉核验及 gap 参考，不作为本次求解热启动。90 秒结果是独立重跑，不能与 15 秒结果混为同预算加速对照。','',
        f"- 严重异常计时重跑：{sum(r['resolved'] for r in timing_repeats)}/{len(timing_repeats)} 条（原记录保留；定位和替代来源见 summary.json 的 timing_repeats）。仅重跑超过 max(60 秒, 10×预算) 的明显中断记录，不筛除普通失败或普通超时。",'',
        '## 精确求解：同预算对照','',
        '| 模型 | 配对数 | 可行解 旧→新 | 证明最优 旧→新 | 平均墙钟秒 旧→新 | 共同证明样本的耗时比 旧/新 |',
        '|---|---:|---:|---:|---:|---:|']
    for m,s in comparisons['exact'].items():
        lines.append(f"| M{m} | {s['pairs']} | {s['baseline_valid']}→{s['current_valid']} | {s['baseline_proven']}→{s['current_proven']} | {fmt(s['baseline_mean_wall_time'])}→{fmt(s['current_mean_wall_time'])} | {fmt(s['common_proven_time_ratio'],2)} ({s['common_proven_count']}例) |")
    lines += ['', '耗时均值包含限时未证明样本；只有两边均证明最优的配对子集才报告解同一问题的耗时比。','',
              '精确对照的 M7 列使用最终修订的15秒批次中相同4例，其他列保留第一版正式对照；源映射见 summary.json。M1 原入口未改，不将它的计时波动解释为算法加速。','',
              '例如Set1_12-2-4：原M4在15秒预算下没有可行解，新全量批次以383.522证明最优；原M7返回462.356、下界346.156，最终M7以383.522证明最优。M6原上界441.304已经正确，优化主要把298.694的下界推进到上界，属于证明速度改善。','',
              '## ALNS 第一轮：同预算配对质量','',
              '此表比较原基线与缓存等实现改动后的第一版，尚不包含后续参数选择。改善率 = (旧目标−新目标)/旧目标；正值表示改善。胜/平/负按每个实例和种子配对，迭代比只衡量吞吐量。','',
              '| 规模 | 模型 | 配对有效数 | 平均改善率 % | 胜/平/负 | 平均迭代比 新/旧 |',
              '|---|---|---:|---:|---:|---:|']
    for scale in ('small','large'):
        for m,s in comparisons[scale].items():
            lines.append(f"| {'小' if scale=='small' else '大'} | M{m} | {s['paired_quality_count']} | {fmt(s['mean_improvement_percent'])} | {s['wins']}/{s['ties']}/{s['losses']} | {fmt(s['iteration_ratio'],2)} |")
    lines += ['', '## ALNS 第二轮：参数筛选与全量复核','',
        '下面比较最终选定设置与已使用缓存的第一版，使用完整覆盖批次；大规模18例为Seed 42，小规模22例各3种子。没有精确参考的大规模只报告相对目标变化。M6另外使用全部18例、两个种子、正反区块顺序比较0.8/0.9，默认值按该完整复核选择。','',
        '| 规模 | 模型 | 有效配对数 | 最终设置平均改善率 % | 胜/平/负 |',
        '|---|---|---:|---:|---:|']
    for scale,groups in [('小',small_adoption),('大',full_adoption)]:
        for m,s in groups.items():
            lines.append(f"| {scale} | M{m} | {s['valid_pairs']} | {fmt(s['mean_gain_percent'])} | {s['wins']}/{s['ties']}/{s['losses']} |")
    if m6_ratio==.8:
        lines += ['', 'M6最终保留与第一版相同的0.8比例，因此该行是同参数重新运行的变化，不作为新增参数收益。其0.9候选是否采用，由下面独立的完整配对复核决定。']
    split = policy.get('m6_full_comparison')
    if split:
        lines += ['', f"M6完整配对复核：0.9相对0.8的平均改善率为{fmt(split['mean_gain_percent'])}%，"
                  f"胜/平/负为{split['wins']}/{split['ties']}/{split['losses']}，"
                  f"最差配对变化为{fmt(split['worst_gain_percent'])}%；最终选用{m6_ratio}。"
                  '两种设置共同不可行的2条记录不进入目标比值，但保留在[完整比较](m6_full_comparison.json)中。']
    lines += ['', '最终默认参数：','',
        '| 模型 | 自动策略（显式参数可覆盖） |',
        '|---|---|',
        '| M2 | 客户数>20且预算≥5秒：初始固定分仓路线预搜索占总时间20%；其他情况关闭。 |',
        '| M4 | 10–20客户且预算≥5秒：候选初始分仓共享最多min(1秒,20%总预算)的预搜索；其他情况关闭。 |',
        f"| M6 | 阶段一比例 {m6_ratio if m6_ratio is not None else '待最终配对复核'}；其余时间用于交接及补搜。 |",
        '| M7 | 10–20客户且预算≥5秒：同M4初始分仓预搜索；客户数≥10且预算≥5秒：阶段一比例0.9，其余为0.75。 |','',
        '预搜索、交接和补搜共用总时间与迭代额度。M2随机固定分仓混合移动和连续段反转仍默认关闭。初始候选预搜索受候选数量及全局剩余预算约束，20%不是可无限重复追加的预算。','',
        '[候选筛选明细](candidate_comparison.json)保留全部已完成候选，包括未采用的策略；筛选期代表实例上的收益不替代上面的全量复核。','',
        '## 精确全量覆盖','',
        '| 模型 | 15秒有效解 | 15秒证明最优 | 15秒批次平均墙钟秒 | 追加后累计已证明 |',
        '|---|---:|---:|---:|---:|']
    for m,s in exact_summary.items():
        lines.append(f"| M{m} | {s['valid']}/{s['runs']} | {s['proven15']}/{s['runs']} | {fmt(s['mean_wall_time'])} | {s['proven_final']}/{s['runs']} |")
    lines += ['', '## 最新小规模精确结果','',
        '✓ 表示本轮已证明最优；未证明的单元格为「可行上界 / 有效下界」。表中综合本轮全部有效证明和上下界，不是单次15秒结果。完整预算、来源及耗时见 [summary.json](summary.json)，所选原始路线及累计结果见 [latest_exact_runs.jsonl](latest_exact_runs.jsonl)。','',
        '| 实例 | M1 | M2 | M3 | M4 | M6 | M7 |','|---|---:|---:|---:|---:|---:|---:|']
    for instance in sorted({k[0] for k in latest}):
        cells = []
        for m in MODELS:
            s = latest.get((instance,m))
            cells.append('—' if not s else (fmt(s['objective'])+' ✓' if s['proven_optimal'] else fmt(s['objective'])+' / '+fmt(s['best_bound'])))
        lines.append('| '+instance+' | '+' | '.join(cells)+' |')
    lines += ['', '仍未证明的实例：','',
        '| 实例 | 模型 | 最佳可行上界 | 有效下界 | 剩余gap % | 所选路线来源 |',
        '|---|---|---:|---:|---:|---|']
    for k,s in sorted(latest.items()):
        if not s['proven_optimal']:
            lines.append(f"| {k[0]} | M{k[1]} | {fmt(s['objective'])} | {fmt(s['best_bound'])} | {fmt(s['gap_percent'])} | [{s['selected_source']}]({s['selected_source']}) |")
    if adopted_status=='complete':
        lines += ['', '最终90秒批次未新增M7最优证明。五个剩余实例分别还有1、1、3、1、2个已筛出的完整M6分仓子问题未闭合；当前主要瓶颈是交接载荷与同步约束下的子问题下界，而非简单遗漏分仓。完整分仓审计见adopted_m7_proof90原始记录中的partition_summaries。']
    lines += ['', '## 最新小规模 ALNS：三种子统计','',
        'gap 仅使用该模型已证明的最优值；没有严格参考值的 M7 样本不纳入 gap 分母。命中最优是事后比较，ALNS 本身不提供最优证明。','',
        '| 模型 | 有效解/运行数 | 有严格参考的运行数 | 命中最优 | 平均 gap % | 最大 gap % | 平均墙钟秒 |',
        '|---|---:|---:|---:|---:|---:|---:|']
    for m,s in small_summary.items():
        lines.append(f"| M{m} | {s['valid']}/{s['runs']} | {s['reference_runs']} | {s['optimal_hits']} | {fmt(s['mean_gap_percent'],4)} | {fmt(s['max_gap_percent'],4)} | {fmt(s['mean_wall_time'])} |")
    lines += ['', '下表为各实例三种子的平均目标值；最小值和所有路线可从原始记录复核。','',
              '| 实例 | M1 | M2 | M3 | M4 | M6 | M7 |','|---|---:|---:|---:|---:|---:|---:|']
    for instance in sorted({r['instance'] for r in small}):
        lines.append('| '+instance+' | '+' | '.join(fmt(mean([r['objective'] for r in small if r['instance']==instance and r['model']==m and r['valid']])) for m in MODELS)+' |')
    lines += ['', '## 最新大规模 ALNS','',
              '本表为 Seed 42 的 5 秒结果，用于全量覆盖检查，不代表三种子均值，也没有精确最优 gap。破折号表示未返回有效可行解。','',
              '| 实例 | M1 | M2 | M3 | M4 | M6 | M7 |','|---|---:|---:|---:|---:|---:|---:|']
    for instance in sorted({r['instance'] for r in large}):
        cells = [next((fmt(r['objective']) if r['valid'] else '—' for r in large if r['instance']==instance and r['model']==m),'—') for m in MODELS]
        lines.append('| '+instance+' | '+' | '.join(cells)+' |')
    lines += ['', 'Set2_200-10-3 的固定分仓中，C93 经 S3 的最早直接到达下界为 79.2873，晚于配送截止 75；因此 M1/M3/M6 原问题不可行。自由分仓模型可改变服务仓。这不能归因于 ALNS 搜索不足。[逐段计算证据](infeasibility_witness.json)。','',
        '## 验证与使用限制','',
        f'- 最新回归：{test_count} 项通过（包含旧/新完整精确模型的多随机实例目标值一致性、零时长子环、正 MIPGap 不冒充精确证明、未闭合分仓下界保留、缓存失效和固定分仓移动隔离）。',
        '- 无接力路线从输出重新审计覆盖、分仓、重量/体积、时间窗、先送后取、两级同步与距离。有接力路线使用模型评估器检查及独立距离重算，未声称完成独立的全交接审计。',
        '- 最终补充的证书保护同时检查距离与行驶时间，且包括尚未预分仓的客户。22 个小实例全部通过新条件，因此不改变本批次的松弛适用范围；回归测试按最终实现重新运行。[检查记录](metric_guard_verification.json)。',
        '- 正式 ALNS 质量收益以配对表为准；部分种子可能退步，不能把缓存加速等同于所有实例都变好。',
        '- M2随机固定分仓混合、路线段反转仍默认关闭；M2初始路线预搜索与M4/M7初始分仓预搜索按上表条件启用。M6时间比例有全量双种子复核，结果与选择均保留。',
        '- 尚未闭合的 M7 上下界如实保留；本轮停止意味着稳定改动与覆盖验证完成，不意味着所有 NP-hard 实例都已解决。',
        f"- 自动汇总一致性异常：{len(failures)} 项。",'',
        '## 原始记录与复现','',
        '- [最新精确路线与累计证书](latest_exact_runs.jsonl) · [最新小规模 ALNS](latest_small_alns.jsonl) · [最新大规模 ALNS](latest_large_alns.jsonl)',
        '- [第一版小规模精确](final_exact/runs.jsonl) · [第一版小规模 ALNS](final_small_alns/runs.jsonl) · [第一版大规模 ALNS](final_large_alns/runs.jsonl)',
        '- [基线/当前精确](control_baseline_exact/runs.jsonl) · [当前精确](control_current_exact/runs.jsonl)',
        '- [小规模基线](control_baseline_small/runs.jsonl) · [小规模当前](control_current_small/runs.jsonl)',
        '- [大规模基线](control_baseline_large/runs.jsonl) · [大规模当前](control_current_large/runs.jsonl)',
        '- [机器可读总表](summary.json) · [第一轮进度](campaign_progress.json) · [参数复核进度](adopted_progress.json) · [M6追加配对进度](m6_ratio_progress.json)','',
        '```powershell',
        'python experiments/runs/run_optimization_campaign_20260913.py',
        'python experiments/runs/repeat_timing_anomalies_20260913.py',
        'python experiments/runs/validate_adopted_optimization_20260914.py',
        'python experiments/runs/validate_m6_stage_all_20260914.py',
        'python experiments/runs/report_optimization_20260913.py',
        '```','',
        '批次脚本按 COMPLETE 标记断点续跑；改变源代码或配置须使用新的输出目录。源快照与历史记录保留，当前工作区后续修改不会改变旧批次。M6最终默认选择由 final_policy.json 记录，不能把探索0.9的批次误认作最终设置。']
    if policy.get('final_source'):
        lines += ['',f"最终工作区代码快照：[release_source]({policy['final_source']})；源文件哈希和测试日志见final_policy.json。"]
    for name in sorted(stages):
        if name.startswith('proof_extension_') or name=='adopted_m7_proof90':
            lines.append(f'- [90 秒追加批次 {name}]({name}/runs.jsonl)')
    (OUT/'report.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    print(json.dumps(dict(complete=complete,counts=summary['stage_counts'],failures=failures,
                         comparisons=comparisons,small_alns=small_summary),ensure_ascii=False,indent=2))
    if complete and failures:
        raise SystemExit('Validation failures: inspect summary.json')


if __name__=='__main__':
    main()
