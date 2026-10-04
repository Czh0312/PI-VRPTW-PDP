"""Paired distance and seed-dispersion summaries, retaining feasibility failures."""
import argparse
from collections import defaultdict
import csv
import json
from pathlib import Path
import statistics


def compare(before, after):
    def key(row):
        return row['case_id'], row['pool'], row['budget'], row['seed']
    left, right = {key(r):r for r in before}, {key(r):r for r in after}
    if len(left)!=len(before) or len(right)!=len(after) or left.keys()!=right.keys():
        raise ValueError('Comparison requires identical unique registered jobs')
    paired, groups = [], defaultdict(list)
    for k,a in left.items():
        b = right[k]
        common = a['valid'] and b['valid']
        delta = (b['objective']/a['objective']-1)*100 if common and a['objective'] else None
        row = {field:a[field] for field in ('case_id','family','split','customers','batches','pool','pool_label','budget','seed')}
        row.update(before_valid=a['valid'],after_valid=b['valid'],before=a.get('objective'),
                   after=b.get('objective'),change_percent=delta)
        paired.append(row)
        groups[k[:3]].append(row)
    summaries = []
    for rows in groups.values():
        row = {k:v for k,v in rows[0].items() if k in ('case_id','family','split','customers','batches','pool','pool_label','budget')}
        row['runs'] = len(rows)
        for version in ('before','after'):
            values = [r[version] for r in rows if r[version+'_valid']]
            mean = statistics.mean(values) if values else None
            sd = statistics.stdev(values) if len(values)>1 else 0.0 if values else None
            row.update({version+'_valid':len(values),version+'_mean':mean,
                        version+'_best':min(values) if values else None,
                        version+'_worst':max(values) if values else None,
                        version+'_sd':sd,version+'_cv_percent':100*sd/mean if mean else None})
        common = [r['change_percent'] for r in rows if r['change_percent'] is not None]
        row['paired_change_percent'] = statistics.mean(common) if common else None
        summaries.append(row)
    changes = [r['change_percent'] for r in paired if r['change_percent'] is not None]
    complete = [r for r in summaries if r['before_valid']==r['after_valid']==r['runs']]
    overview = dict(jobs=len(paired),before_valid=sum(r['before_valid'] for r in paired),
        after_valid=sum(r['after_valid'] for r in paired),common_valid=len(changes),
        improved=sum(v < -1e-6 for v in changes),tied=sum(abs(v)<=1e-6 for v in changes),
        worse=sum(v > 1e-6 for v in changes),paired_mean_change_percent=statistics.mean(changes) if changes else None,
        groups=len(summaries),dispersion_groups=len(complete),
        before_mean_cv_percent=statistics.mean(r['before_cv_percent'] for r in complete) if complete else None,
        after_mean_cv_percent=statistics.mean(r['after_cv_percent'] for r in complete) if complete else None,
        cv_improved_groups=sum(r['after_cv_percent']<r['before_cv_percent']-1e-6 for r in complete),
        worst_improved_groups=sum(r['after_worst']<r['before_worst']-1e-8 for r in complete))
    for split in ('development','held_out'):
        values = [r['change_percent'] for r in paired if r['split']==split and r['change_percent'] is not None]
        overview[split+'_paired_change_percent'] = statistics.mean(values) if values else None
    return dict(overview=overview,groups=summaries,paired=paired)


def report(folder,data,before,after):
    o=data['overview']
    seeds = sorted({r['seed'] for r in data['paired']})
    split_families = {split: sorted({r['family'] for r in data['paired'] if r['split']==split})
                      for split in ('development','held_out')}
    lines=['# M5 ALNS：同预算质量与稳定性对照','',
        '## Material Passport','',
        '- Origin Skill: academic-research-suite / experiment-agent',
        '- Origin Mode: run + validate',
        '- Verification Status: ANALYZED。实际执行、独立审计、源码/输入哈希和统计复算已核验；不把限时搜索结果当作最优性证明。','',
        f'比较 `{before}` 与 `{after}`。固定 50/100 顾客、B2/B3、全池/紧池，随机种子 {seeds}。'
        '全部场景运行 5 秒；100 顾客 B3 另运行 15 秒。两版迭代上限均为 100000，'
        '防止旧的 5000 次上限提前截断长预算。每个作业使用独立进程，串行执行。','',
        '紧池 N=max(最大单批需求, ceil(0.5Q))；全池 N=Q。固定输入在执行前冻结。'
        '注册运行不是独立母实例数量；50 顾客子集与完整 100 顾客之间存在嵌套。','',
        '开发集：'+', '.join(split_families['development'])+'。','',
        '最终保留集：'+', '.join(split_families['held_out'])+'。','',
        '## 实际结果','',
        f'- 审计且两周期重放通过：{o["before_valid"]}/{o["jobs"]} → {o["after_valid"]}/{o["jobs"]}。',
        f'- 相同场景/池/预算/种子的距离：{o["improved"]} 次改善、{o["tied"]} 次持平、{o["worse"]} 次退步。',
        f'- 配对距离相对变化均值：{o["paired_mean_change_percent"]:.2f}%；负值为改善。',
        f'- 开发集 {o["development_paired_change_percent"]:.2f}%；保留集 {o["held_out_paired_change_percent"]:.2f}%。',
        f'- 逐场景种子变异系数 CV 的均值：{o["before_mean_cv_percent"]:.2f}% → {o["after_mean_cv_percent"]:.2f}%。',
        f'- {o["cv_improved_groups"]}/{o["dispersion_groups"]} 组 CV 下降；{o["worst_improved_groups"]}/{o["dispersion_groups"]} 组最差种子距离改善。','',
        f'CV=样本标准差/均值，按同一场景、箱池、预算的 {len(seeds)} 个种子计算。'
        '少量种子只能描述本轮波动，不足以建立稳定的尾部概率或显著性结论。'
        '平均质量、最差结果和 CV 分别报告，不相互替代。','',
        '| 母例/顾客 | 批次 | 池 N | 秒 | 距离均值：前 → 后 | 配对变化 | 最差距离：前 → 后 | CV：前 → 后 |',
        '|---|---:|---:|---:|---:|---:|---:|---:|']
    for r in data['groups']:
        label=family_label(r)
        lines.append(f'| {label} | {r["batches"]} | {r["pool"]} | {r["budget"]:g} | '
            f'{r["before_mean"]:.2f} → {r["after_mean"]:.2f} | {r["paired_change_percent"]:.2f}% | '
            f'{r["before_worst"]:.2f} → {r["after_worst"]:.2f} | '
            f'{r["before_cv_percent"]:.2f}% → {r["after_cv_percent"]:.2f}% |')
    lines+=['','距离单位为 Zhou 坐标单位。没有大例路线最优值或可靠 gap，不能把相对基线改善称为接近全局最优。','',
        '## 统计与复现自检','',
        '11/11 项已检查：','',
        '1. Simpson：提供分母例、规模、批次、池和预算的完整分组表，整体平均不替代逐组结论。',
        '2. 生态谬误：不从总体均值断言每个场景或每个种子都改善。',
        '3. 选择偏差：仅覆盖注册计划列出的母例；不得外推所有路网、业务配置或更大规模。',
        '4. 碰撞变量偏差：不按求解后的目标值筛选测试场景；失败记录保留。',
        '5. 基率忽视：分母为全部注册作业，单独列出可行数。',
        '6. 均值回归：相同输入/种子/预算配对；开发调参结果与保留集分开报告。',
        '7. 幸存者偏差：不删除超时、无解、审计失败；距离比较只用共同有效对，并保留全部可行计数。',
        '8. 多重寻找：不进行显著性筛选；保留所有中间 pilot 及最终全部种子。',
        '9. 分析路径：开发过程属于探索；固定注册最终任务，不从多轮结果逐个挑最优拼表。',
        '10. 因果措辞：这是同预算工程对照，墙钟时间受机器负载影响；不据此声明普遍速度或机制因果规律。',
        '11. 反向因果：算法版本先冻结再执行最终作业；不使用最终保留集结果继续调参。','',
        '新版包含辅助 seed=0 的共同初始搜索阶段，仍在同一预算内；'
        '用户指定种子用于后续搜索。因此 CV 描述完整算法的运行间波动，不是独立随机初解的差异。'
        '时间上限附近的一次候选计算及最终审计可能稍微超预算；实际耗时保存在原始记录。','',
        '## 证据','',
        '- [注册计划和输入哈希](benchmark_manifest.json)',
        f'- [基线逐次结果]({before}/summary.json) · [最终逐次结果]({after}/summary.json)',
        '- [全部配对记录](paired.csv) · [分组统计](groups.csv) · [机器可读比较](comparison.json)',
        '- [回归测试](pytest.log) · [测试 XML](pytest.xml)',
        f'- [基线源码与输入校验]({before}/verification.json) · [最终源码与输入校验]({after}/verification.json)',
        '- 每版目录含源码快照、每次完整时间表、独立审计、两周期重放及日志。']
    (folder/'report.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')


def family_label(row):
    name = row['family'].removeprefix('zhou_Set2_100-').replace('_subset50','')
    return f'{name}/{row["customers"]}'


def plot(folder,data):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    groups=data['groups']
    labels=[f'{family_label(r)} | B{r["batches"]} | N={r["pool"]} | {r["budget"]:g}s' for r in groups]
    fig,(a,b)=plt.subplots(1,2,figsize=(14,max(10,len(groups)*.39)),sharey=True,gridspec_kw={'width_ratios':[1.15,1]})
    y=list(range(len(groups)))
    values=[r['paired_change_percent'] for r in groups]
    a.barh(y,values,color=['#177e89' if v<=0 else '#c05240' for v in values],height=.65)
    a.axvline(0,color='#525b66',linewidth=.8)
    a.set_yticks(y,labels,fontsize=9)
    a.invert_yaxis()
    a.set_xlabel('Paired distance change (%) — lower is better')
    a.set_title('Solution quality',loc='left',fontweight='bold')
    for pos,r in enumerate(groups):
        b.plot([r['before_cv_percent'],r['after_cv_percent']],[pos,pos],color='#bac3cb',linewidth=1.5)
    b.scatter([r['before_cv_percent'] for r in groups],y,label='Before',color='#89939d',s=28)
    b.scatter([r['after_cv_percent'] for r in groups],y,label='After',color='#177e89',s=28)
    b.set_xlabel('Seed coefficient of variation (%) — lower is better')
    b.set_title('Run-to-run dispersion',loc='left',fontweight='bold')
    b.legend(frameon=False,loc='lower right')
    for ax in (a,b):
        ax.grid(axis='x',color='#e5e9ed',linewidth=.6)
        ax.set_axisbelow(True)
        for spine in ('top','right','left'):ax.spines[spine].set_visible(False)
    fig.suptitle('M5 ALNS | Equal-budget comparison',x=.04,ha='left',fontsize=18,fontweight='bold')
    fig.text(.04,.925,f'Mother / customers | batch count | pool | seconds · {data["overview"]["jobs"]} paired runs',fontsize=10,color='#56616d')
    fig.tight_layout(rect=[0,.02,1,.90])
    fig.savefig(folder/'quality_and_dispersion.png',dpi=180,facecolor='white')
    plt.close(fig)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',required=True,type=Path)
    parser.add_argument('--before',default='before')
    parser.add_argument('--after',default='after')
    parser.add_argument('--plot',action='store_true')
    args=parser.parse_args()
    load=lambda phase:json.loads((args.output/phase/'summary.json').read_text())
    data=compare(load(args.before),load(args.after))
    (args.output/'comparison.json').write_text(json.dumps(data,ensure_ascii=False,indent=2))
    for name in ('groups','paired'):
        with (args.output/(name+'.csv')).open('w',newline='',encoding='utf-8-sig') as f:
            writer=csv.DictWriter(f,fieldnames=list(data[name][0]))
            writer.writeheader();writer.writerows(data[name])
    print(json.dumps(data['overview'],ensure_ascii=False,indent=2))
    if all(r['before_valid']==r['after_valid']==r['runs'] for r in data['groups']):
        report(args.output,data,args.before,args.after)
        if args.plot:plot(args.output,data)


if __name__=='__main__':
    main()
