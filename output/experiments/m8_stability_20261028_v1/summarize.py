"""Summarize the complete frozen holdout, keeping timeouts censored."""
import csv
import hashlib
import json
from pathlib import Path
import statistics

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

HERE = Path(__file__).resolve().parent
CASES = ['S03','S08','S05']
PROFILES = ['previous','compact_decomposition']


def read(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def save(path,data):
    path.write_text(json.dumps(data,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')


def span(values):
    return dict(minimum=min(values),median=statistics.median(values),maximum=max(values),
                range=max(values)-min(values))


def write_csv(path,rows):
    fields = list(dict.fromkeys(key for row in rows for key in row))
    with path.open('w',encoding='utf-8-sig',newline='') as stream:
        writer = csv.DictWriter(stream,fields)
        writer.writeheader()
        writer.writerows(rows)


def percent_range(values):
    s = span([100*value for value in values])
    return f"{s['median']:.3f}%（{s['minimum']:.3f}–{s['maximum']:.3f}%）"


def main():
    rows = [read(path) for path in (HERE/'runs').rglob('summary.json')]
    holdout = [row for row in rows if row['phase']=='holdout']
    pilot = [row for row in rows if row['phase']=='pilot']
    assert len(holdout)==18 and len(pilot)==15
    assert all(row['input_and_sources_unchanged'] and row['business_audit_valid']
               and row['two_cycle_replay_valid'] for row in rows)
    grouped,paired = [],[]
    for case in CASES:
        for profile in PROFILES:
            group = sorted((r for r in holdout if r['case']==case and r['profile']==profile),key=lambda r:r['seed'])
            assert [r['seed'] for r in group]==[7,19,31]
            proven = [r for r in group if r['global_optimality_certified']]
            grouped.append(dict(case=case,profile=profile,seeds=[7,19,31],
                proof_successes=len(proven),trials=3,timeouts=3-len(proven),
                final_gap_over_upper=span([r['gap_over_upper'] for r in group]),
                full_trial_wall_seconds=span([r['total_wall_seconds'] for r in group]),
                proof_wall_seconds=span([r['total_wall_seconds'] for r in proven]) if proven else None,
                proof_algorithm_seconds=span([r['proof_seconds'] for r in proven]) if proven else None,
                native_work_units=span([r['mip_work'] for r in group]),
                common_U=group[0]['historical_U'],common_L=group[0]['common_L']))
        for seed in [7,19,31]:
            base = next(r for r in holdout if (r['case'],r['profile'],r['seed'])==(case,'previous',seed))
            new = next(r for r in holdout if (r['case'],r['profile'],r['seed'])==(case,'compact_decomposition',seed))
            assert base['historical_U']==new['historical_U'] and base['common_L']==new['common_L']
            paired.append(dict(case=case,seed=seed,previous_proven=base['global_optimality_certified'],
                candidate_proven=new['global_optimality_certified'],previous_gap_percent=100*base['gap_over_upper'],
                candidate_gap_percent=100*new['gap_over_upper'],
                gap_reduction_percent=100*(1-new['gap_over_upper']/base['gap_over_upper']) if base['gap_over_upper'] else None,
                previous_trial_wall_seconds=base['total_wall_seconds'],candidate_trial_wall_seconds=new['total_wall_seconds'],
                previous_proof_seconds=base['proof_seconds'],candidate_proof_seconds=new['proof_seconds'],
                candidate_global_lower_bound=new['global_lower_bound'],audited_upper_bound=new['audited_upper_bound']))
    save(HERE/'statistics.json',dict(holdout_runs=18,pilot_runs=15,statistics=grouped,paired=paired,
         selection='Candidate frozen before seeds 7,19,31; seed 42 exploratory only.',
         censored_timeouts='No finite optimality-proof time or speedup factor assigned to timed-out runs.',
         scope='Known audited U and prior valid L; original all-K problem; proof stage only.'))
    write_csv(HERE/'holdout.csv',holdout)
    write_csv(HERE/'pilot.csv',pilot)
    write_csv(HERE/'paired.csv',paired)

    colors = ['#2c7bb6','#d67600','#229c73']
    fig,axes = plt.subplots(1,3,figsize=(12,4.3),layout='constrained')
    for ax,case in zip(axes[:2],CASES[:2]):
        for seed,color in zip([7,19,31],colors):
            group = sorted((r for r in holdout if r['case']==case and r['seed']==seed),
                           key=lambda r:PROFILES.index(r['profile']))
            ax.plot([0,1],[100*r['gap_over_upper'] for r in group],'o-',color=color,label=f'Seed {seed}',alpha=.85)
        ax.set_xticks([0,1],['Previous','Candidate'])
        ax.set_title(f'{case}: 300 s budget')
        ax.set_ylabel('Final certified gap (%)')
        ax.set_ylim(bottom=0)
        ax.grid(axis='y',alpha=.25)
    axes[0].legend(frameon=False)
    control = axes[2]
    for seed,color in zip([7,19,31],colors):
        for x,profile in enumerate(PROFILES):
            row = next(r for r in holdout if (r['case'],r['profile'],r['seed'])==('S05',profile,seed))
            proven = row['global_optimality_certified']
            value = row['proof_seconds'] if proven else 120
            control.scatter(x+(seed-19)/170,value,color=color,marker='o' if proven else 'x',s=55)
    control.axhline(120,color='gray',linestyle='--',linewidth=1)
    control.text(.5,123,'x = not proven at 120 s',ha='center',fontsize=9)
    control.set_xticks([0,1],['Previous','Candidate'])
    control.set_title('S05: 120 s budget')
    control.set_ylabel('Proof algorithm time (s)')
    control.set_ylim(0,138)
    control.grid(axis='y',alpha=.25)
    fig.suptitle('M8 frozen 3-seed paired validation | known feasible U and valid L supplied',fontsize=12)
    fig.savefig(HERE/'holdout_comparison.png',dpi=180)
    plt.close(fig)

    lines = [
        '# 模型八求解效率稳定性验证',
        '',
        '结论：本轮候选方法改善了限定时间内的证明进度；是否证明最优和剩余界差距分别报告。'
        '三个种子只用于检验本次样本内的重复性，不能推出所有算例都能稳定快速完成。',
        '',
        '## 预先固定的对照',
        '',
        '选取未闭合的 S03、S08，以及已知可解的 S05 作为控制。探索阶段只使用种子 42；'
        '候选方法与执行顺序随后冻结，再运行种子 7、19、31 的配对试验，共 18 次。'
        '每次使用 1 个 Gurobi 线程，同时最多运行 2 个算例进程，前后顺序交替。',
        '',
        '**这是使用已有可行解上界 U 和已有有效下界 L 的证明阶段比较。**'
        '两组得到相同的 U/L；生成历史上界、历史下界和共享准备数据的时间不计入本轮预算，'
        '不能将以下数字当作从原始输入开始的总加速比。S05 没有把已知最优值作为下界输入。'
        '两组都没有给原完整模型提供历史路线 MIP start。',
        '',
        'S03、S08 的预算为 300 秒，S05 为 120 秒。基准预算覆盖 Gurobi 优化；候选预算还覆盖'
        '主问题和子问题的内部构建。完整墙钟时间另外保留在 CSV/JSON 中。预算到期仍未完成的试验是删失数据，'
        '不把它记为一个有限的最优证明时间，也不由此计算确定的证明加速倍数。',
        '',
        '| 算例 | 方法 | 证明最优 | 最终 gap：中位数（最小–最大） | 已证明运行的完整墙钟时间 |',
        '|---|---|---:|---:|---:|',
    ]
    for group in grouped:
        subset = [r for r in holdout if r['case']==group['case'] and r['profile']==group['profile']]
        wall = group['proof_wall_seconds']
        label = '原方法' if group['profile']=='previous' else '候选分解方法'
        time_text = f"{wall['minimum']:.2f}–{wall['maximum']:.2f} 秒" if wall else '未证明，不能给出'
        lines.append(f"| {group['case']} | {label} | {group['proof_successes']}/3 | "
                     f"{percent_range([r['gap_over_upper'] for r in subset])} | {time_text} |")
    hard_pairs = [r for r in paired if r['case'] in ('S03','S08')]
    improvements = sum(r['candidate_gap_percent']<r['previous_gap_percent'] for r in hard_pairs)
    hard_proofs = sum(r['candidate_proven'] for r in hard_pairs)
    lines += ['',f"六组难例配对中，{improvements}/6 组缩小了最终界差距；"
                  f"候选方法在其中 {hard_proofs}/6 次证明了最优。"
                  '因此本轮支持的是限时证明进度的重复改善，尚不能称为消除了最优证明时间的长尾。']
    lines += [
        '',
        'gap 按 `(经原模型审计的可行成本 U − 有效全局下界 L) / |U|` 计算；'
        '“证明最优”要求二者在 1e-6 绝对容差内闭合。候选方法的证书由全局主问题与完整子问题共同形成，'
        '没有将多个子问题的状态冒充为单次原完整模型的 `OPTIMAL`。',
        '',
        f'![三种子配对结果]({(HERE/"holdout_comparison.png").as_posix()})',
        '',
        '## 本次方法改变了什么',
        '',
        '1. 以已有可行成本 U 为界，在包含原始全部 K=1…7 的乐观松弛上重新证明可竞争的 K/N 范围。'
        'S08 从 K∈{1,2}、N∈[5,7] 缩至 K=2、N=5；S03 仍为 K∈{3,4}、N=4；S05 为 K=2、N=4。'
        '被排除的是不可能优于 U 的方案，不是宣称其业务不可行。',
        '2. 用完整任务覆盖的乐观成本下界筛除不可能达到 U 的车次任务集合。'
        'S08 的集合数从 3181 降至 259，S03 从 274 降至 258，S05 仍为 266。',
        '3. 主问题选择批次、回收批次和每条车次包含的任务。子问题保留原模型的车辆、实际访问顺序、'
        '库存、清洗、截流和整数时钟约束。只有子问题确证在原始 U 下无解，才排除整个离散组合；'
        '子问题超时只提供其有效下界，绝不按无解处理。',
        '4. 固定每条车次的任务后，按各站点非空车次总数去掉必定空着的单车后缀槽位。'
        '保留原车辆资源和全部时间选择；局部复制策略，不修改输入和缓存的原子问题。',
        '',
        '未采用为默认方案的探索也保留了结果：单独加全部任务集合约束、仅重算范围、仅加成本筛选，'
        '以及 warm start + MIPFocus=3，均未在本轮短预算中稳定改善最优证明。warm start 使已知可行解'
        '很快进入求解器，但这与更快证明全局最优是两件事。全部探索数据见 `pilot.csv`，不混入新种子的统计。',
        '',
        '## 验证与适用范围',
        '',
        '原始数据、成本口径、K 候选集合和生产求解源码均由哈希核对。最终可行方案重新计算成本，'
        '并进行原模型约束审计和连续两个周期的回放。证据测试另外重建加完所有有效割的主问题，'
        '重新求证记录的全局下界；同时重算范围证书及每个被筛选任务集合的成本下界。',
        '',
        '环境检查、选定测试范围和实际通过/失败/跳过数量见 `validation.json`；本次没有运行整个仓库测试集。',
        '',
        '候选代码仍位于本实验目录，没有替换生产默认求解入口。当前研究实现限于不超过 9 个单箱订单、最多 4 个站点、'
        '已有证书将 N 固定、且保留 K 的时钟精度不发生变化的情形。本次三个算例均为 7 个订单，'
        '结果不能外推到 20/50 订单。三种种子也不足以给出总体成功率或尾部运行时间的统计保证。',
        '',
        '可复核入口：`run_holdout.py`（冻结的执行计划）、`run_compact_trial.py`（单次候选）、'
        '`decomposition_compact.py`（算法）、`test_evidence.py`（独立证据复核）。'
        '每次原生状态、进度、来源哈希和最终方案保存在 `runs/` 下；完整数值见 `holdout.csv`、'
        '`paired.csv`、`statistics.json`。',
        '',
        'Gurobi 全局界的定义参见[官方 callback 文档]'
        '(https://docs.gurobi.com/projects/optimizer/en/current/reference/numericcodes/callbacks.html)。'
        '本机实际验证版本为 11.0.3，版本与许可证初始化以本次项目预检结果为准。',
        '',
    ]
    (HERE/'report.md').write_text('\n'.join(lines),encoding='utf-8')
    save(HERE/'analysis_manifest.json',dict(script_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        trial_summary_hashes={str(path.relative_to(HERE)):hashlib.sha256(path.read_bytes()).hexdigest()
                              for path in (HERE/'runs').rglob('summary.json')},
        frozen_holdout_plans={path.name:hashlib.sha256(path.read_bytes()).hexdigest()
                              for path in HERE.glob('holdout_plan_*.json')}))
    print(json.dumps(dict(holdout_runs=len(holdout),pilot_runs=len(pilot),statistics=grouped),ensure_ascii=False))


if __name__=='__main__':
    main()
