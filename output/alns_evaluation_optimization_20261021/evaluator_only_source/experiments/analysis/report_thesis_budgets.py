"""Family-aware E1b descriptive summaries; primary failures stay in denominators."""
import argparse
from collections import defaultdict
from pathlib import Path
from statistics import mean

from experiments.analysis.analyze_thesis_stages import primary_rows, paired_budgets, table
from experiments.runs import run_thesis_completion as driver
from experiments.runs.recover_thesis_infrastructure import recovery_valid


def report(study):
    study = Path(study).resolve()
    manifest, rows = primary_rows(study)
    if any(r['status'] in ('NOT_RUN', 'RUNNING') for r in rows):
        raise ValueError('Final descriptive report requires all initial attempts')
    pairs = paired_budgets(rows)
    families = {r['case']: r['family'] for r in rows}
    grouped = defaultdict(list)
    for pair in pairs:
        grouped[pair['model'], pair['short_budget'], pair['long_budget']].append(pair)
    summary, family_rows = [], []
    for (model, short, long), members in sorted(grouped.items()):
        valid = [r for r in members if r['both_valid']]
        summary.append(dict(model=model, short_budget=short, long_budget=long,
            planned=len(members), valid=len(valid), missing=len(members)-len(valid),
            better=sum(r['short_minus_long'] > 1e-6 for r in valid),
            worse=sum(r['short_minus_long'] < -1e-6 for r in valid),
            tied=sum(abs(r['short_minus_long']) <= 1e-6 for r in valid),
            mean_pair_improvement_pct=100*mean(r['relative_improvement'] for r in valid)))
        by_family = defaultdict(lambda: defaultdict(list))
        for r in members:
            by_family[families[r['case']]][r['case']].append(r)
        for family, cases in sorted(by_family.items()):
            case_means = [mean(r['relative_improvement'] for r in rs if r['both_valid'])
                          for rs in cases.values() if any(r['both_valid'] for r in rs)]
            family_rows.append(dict(family=family, model=model, short_budget=short,
                long_budget=long, cases=len(cases), planned=sum(map(len, cases.values())),
                valid=sum(r['both_valid'] for rs in cases.values() for r in rs),
                mean_case_improvement_pct=100*mean(case_means) if case_means else None))
    table(study/'tables/budget_pair_summary.csv', summary)
    table(study/'tables/budget_family_summary.csv', family_rows)
    utilization = []
    for model in sorted({r['model'] for r in rows}):
        for budget in sorted({r['budget'] for r in rows}):
            rs = [r for r in rows if r['model'] == model and r['budget'] == budget]
            valid = [r for r in rs if r['valid']]
            utilization.append(dict(model=model, budget=budget, planned=len(rs), valid=len(valid),
                mean_success_wall_seconds=mean(r['solve_wall_seconds'] for r in valid),
                mean_success_utilization=mean(r['solve_wall_seconds']/budget for r in valid),
                failed=len(rs)-len(valid)))
    table(study/'tables/budget_usage_summary.csv', utilization)
    recovery_note = '基础设施补跑尚未通过复核。'
    review_path = study/'infrastructure_recovery/review.json'
    if review_path.exists():
        review = driver.read(review_path)
        job = next(j for j in manifest['jobs'] if j['id'] == review['job'])
        second = study/'runs'/job['id']/'attempt_002'
        if not review['ready'] or not recovery_valid(driver, second, job):
            raise ValueError('Recovery evidence failed independent validation')
        if (driver.sha(second/'completion.json') != review['recovery_completion_sha256'] or
                driver.sha(study/'runs'/job['id']/'attempt_001/row.json') != review['first_failure_row_sha256']):
            raise ValueError('Recovery evidence changed')
        recovered = driver.read(second/'row.json')
        supplementary = [recovered if r['id'] == job['id'] else r for r in rows]
        sensitivity = [r for r in paired_budgets(supplementary)
                       if r['case'] == job['case'] and r['model'] == job['model'] and r['seed'] == job['seed']]
        table(study/'infrastructure_recovery/budget_sensitivity.csv', sensitivity)
        recovery_note = (f'基础设施补跑审计有效，费用 {recovered["objective"]:.6f}；'
            '受影响种子的预算配对另存 infrastructure_recovery/budget_sensitivity.csv。'
            '补跑时间与并发环境不同（单个活跃调用），仅作补充敏感性检查，主表保持首次尝试。')

    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    fig, axes = plt.subplots(1, 3, figsize=(11, 3.8), constrained_layout=True)
    for ax, model in zip(axes, (6, 7, 8)):
        for family in sorted(set(families.values())):
            rs = [r for r in family_rows if r['model'] == model and r['family'] == family]
            ax.plot(range(len(rs)), [r['mean_case_improvement_pct'] for r in rs], marker='o', label=family)
        ax.axhline(0, color='gray', lw=.8)
        ax.set(title=f'M{model}', xticks=range(3), xticklabels=['60→300', '60→900', '300→900'],
               xlabel='Call budget (seconds)')
    axes[0].set_ylabel('Mean relative cost decrease (%)')
    axes[-1].legend(fontsize=6, loc='best')
    fig.savefig(study/'figures/budget_family_comparison.png', dpi=160)
    fig.savefig(study/'figures/budget_family_comparison.pdf')
    plt.close(fig)
    lines = ['# E1b 正式多预算结果', '',
        f'首次尝试 {len(rows)}/{len(rows)} 已结束，{sum(r["valid"] for r in rows)} 次独立审计有效。',
        '一条 Windows 状态文件替换错误作为原始失败保留；补跑及其双周期审计独立列于 infrastructure_recovery，不替换本报告分母。', '',
        '冻结版本 4ed30cd11；六例、M6/M7/M8、种子 42–46、60/300/900 秒、共同 K={1,2,3}。',
        '每个预算是独立冷启动运行；长预算不是从短预算已保存解继续。相同名义种子不保证相同搜索轨迹。', '',
        '| 模型 | 秒预算 | 有效配对/计划 | 改善/恶化/持平 | 配对相对改善均值 |',
        '| --- | --- | --- | --- | --- |']
    for r in summary:
        lines.append(f'| M{r["model"]} | {r["short_budget"]}→{r["long_budget"]} | {r["valid"]}/{r["planned"]} | {r["better"]}/{r["worse"]}/{r["tied"]} | {r["mean_pair_improvement_pct"]:.3f}% |')
    lines += ['', '相对改善为 (短预算费用−长预算费用)/短预算费用。正数表示较低费用，不是相对于最优值的误差。',
        '900 秒相对 60 秒的平均费用改善约为 M6 6.31%、M7 6.22%、M8 5.81%；300→900 秒的边际改善较小，且三个模型均存在恶化配对。不能写成增加预算保证单次改善。',
        '', recovery_note, '', '统计范围与局限：', '',
        '- 逐例均值、样本标准差、最好/最差值、有效数见 per_instance_statistics.csv；配对原始值见 paired_budgets.csv。',
        '- budget_family_summary.csv 先对同一例的有效种子求均值，再对同源实例求均值；嵌套子集和种子不当作独立经验样本。不作显著性或普遍优势声明。',
        '- 正式六例没有配套已核对的精确最优值，不把共同可行参考差异称最优 gap；E0/E1c 的小例精确参照与误差另表报告。',
        '- budget_usage_summary.csv 单列有效调用实际耗时与失败数；超过名义预算的少量外层初始化/收尾时间不是额外搜索预算。',
        '- anytime 图为外层确认的全 K 当前最好费用阶梯，仅在候选返回时观测；不将其当作每次内部移动的实时收敛轨迹。',
        '- 独立结果审计含连续双周期重放，原始 audit.json 绑定 result.json 哈希；可行不等于最优证明。',
        '- 本报告解释预算响应；M6/M7/M8 的离散时钟与允许域须按各自冻结定义解释，不能仅凭限时费用排序证明理论包含关系。',
        '', '论文可用结论：在本次六实例、五种子、固定候选批次数域的实验中，扩大预算降低了平均可行解费用，但改善随实例族变化，且独立限时调用中存在长预算较差的结果。结果支持报告预算敏感性及随机波动，不支持对任意实例的单调改善或全域最优性保证。', '']
    (study/'findings.md').write_text('\n'.join(lines), encoding='utf-8')
    print(dict(primary=len(rows), valid=sum(r['valid'] for r in rows), families=len(set(families.values()))))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--study', required=True)
    report(parser.parse_args().study)
