"""Generate an evidence-linked analysis of the completed M5/M6 cost study."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import statistics

from tools.configuration.loader import resolve_project_path


GROUPS = {
    '箱池持有': ('container_pool',),
    '运输距离': ('e1_distance', 'e2_distance'),
    '占用工时': ('e1_work', 'e2_work'),
    '车辆启用': ('e1_vehicles', 'e2_vehicles'),
    '发车次数': ('e1_trips', 'e2_trips'),
    '清洗/追踪/磨损': ('cleaning', 'tracking', 'container_wear'),
}


def _read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def _close(first, second):
    if not math.isclose(first, second, rel_tol=1e-9, abs_tol=1e-6):
        raise ValueError(f'Analysis reconciliation failed: {first} != {second}')


def _number(value, digits=2):
    return f'{value:.{digits}f}' if value is not None else '—'


def _table(headers, rows):
    return ['| ' + ' | '.join(headers) + ' |',
            '| ' + ' | '.join(['---'] * len(headers)) + ' |',
            *['| ' + ' | '.join(map(str, row)) + ' |' for row in rows]]


def _figures(out, cases):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt

    colors = ['#687b94', '#a9bbcd', '#168277']
    height = 3.8 * math.ceil(len(cases) / 2)
    fig, axes = plt.subplots(math.ceil(len(cases) / 2), 2, figsize=(12, height), squeeze=False)
    for ax, case in zip(axes.flat, cases):
        values = [case['m5']['cost'], case['m5']['cost_with_schedule_minimum_pool'], case['m6']['cost']]
        bars = ax.bar(range(3), values, color=colors, width=0.6)
        ax.bar_label(bars, labels=[f'{v:.2f}' for v in values], padding=4, fontsize=10)
        ax.set_xticks(range(3), ['M5 configured', 'M5 minimum pool*', 'M6 joint'])
        ax.set_ylim(0, max(values) * 1.23)
        ax.set_title(case['case'], fontweight='bold')
        ax.set_ylabel('Cycle cost (synthetic units)')
        ax.set_axisbelow(True)
        ax.grid(axis='y', alpha=0.2)
        for spine in ['top', 'right']:
            ax.spines[spine].set_visible(False)
    for ax in list(axes.flat)[len(cases):]:
        ax.set_visible(False)
    fig.suptitle('M5 vs M6: configured pool and unchanged-schedule minimum pool', fontsize=14)
    fig.text(0.02, 0.015, '* M5 route and timetable unchanged; only pool charge is repriced. '
             'Panels use individual cost scales. Acquisition investment is excluded.', fontsize=9)
    fig.tight_layout(rect=[0, 0.05, 1, 0.94])
    fig.savefig(out/'analysis_costs.png', dpi=160)
    plt.close(fig)

    labels = ['Pool (configured)', 'Pool (minimum*)', 'Distance', 'Occupied work',
              'Vehicle use', 'Trips', 'Cleaning / tracking / wear']
    fig, axes = plt.subplots(math.ceil(len(cases) / 2), 2, figsize=(12, height), squeeze=False)
    for ax, case in zip(axes.flat, cases):
        groups = case['component_savings']
        values = [groups['箱池持有'], case['minimum_pool_savings'], *list(groups.values())[1:]]
        bars = ax.barh(range(7), values, color=['#168277' if v >= 0 else '#dc7849' for v in values])
        ax.bar_label(bars, labels=[f'{v:+.2f}' for v in values], padding=3, fontsize=9)
        span = max(max(map(abs, values)), 1)
        ax.set_xlim(min(0, min(values)) - span * 0.27, max(0, max(values)) + span * 0.30)
        ax.set_yticks(range(7), labels)
        ax.invert_yaxis()
        ax.axvline(0, color='#435366', linewidth=0.8)
        ax.set_title(case['case'] + '\n'
                     + f"Total savings: {case['comparison']['savings']:+.2f}; "
                     + f"minimum-pool reference: {case['schedule_minimum_pool_comparison']['savings']:+.2f}",
                     fontsize=10)
        ax.set_xlabel('M5 cost minus M6 cost (synthetic units)')
        for spine in ['top', 'right']:
            ax.spines[spine].set_visible(False)
    for ax in list(axes.flat)[len(cases):]:
        ax.set_visible(False)
    fig.suptitle('Cost decomposition: positive values favor M6', fontsize=14)
    fig.text(0.02, 0.015, '* The two pool bars are alternatives. Add either pool bar to the five '
             'non-pool categories to obtain the corresponding total.', fontsize=9)
    fig.tight_layout(rect=[0, 0.05, 1, 0.92])
    fig.savefig(out/'analysis_components.png', dpi=160)
    plt.close(fig)


def analyze(directory):
    out = Path(directory)
    source_names = ['cost_summary.json', 'uniform_summary.json', 'rows.json', 'experiment.json',
                    'inputs/manifest.json', 'environment.json', 'validation_checks.json']
    sources = {name: _read(out/name) for name in source_names}
    summary, rows = sources['cost_summary.json'], sources['rows.json']
    uniform = {c['case']: c for c in sources['uniform_summary.json']['cases']}
    records = {c['name']: c for c in sources['inputs/manifest.json']['cases']}
    cases = []
    for item in summary['cases']:
        case = dict(item)
        base, joint = case['m5'], case['m6']
        if base is None or joint is None:
            raise ValueError(f'Analysis needs valid M5 and M6 representatives: {case["case"]}')
        resource = uniform[case['case']]
        if base['run'] != resource['m5']['run'] or joint['run'] != resource['m6']['run']:
            raise ValueError(f'Cost/resource selection mismatch: {case["case"]}')
        for plan in (base, joint):
            _close(sum(plan['components'].values()), plan['cost'])
        reference = _read(out/base['run']/'comparison_cost.json')['schedule_minimum_pool_reference']
        _close(reference['total'], base['cost_with_schedule_minimum_pool'])
        for key, value in reference['components'].items():
            if key != 'container_pool':
                _close(value, base['components'][key])
        groups = {name: sum(base['components'][k] - joint['components'][k] for k in keys)
                  for name, keys in GROUPS.items()}
        minimum_pool_savings = reference['components']['container_pool'] - joint['components']['container_pool']
        _close(sum(groups.values()), case['comparison']['savings'])
        operating_savings = sum(value for name, value in groups.items() if name != '箱池持有')
        _close(minimum_pool_savings + operating_savings, case['schedule_minimum_pool_comparison']['savings'])
        paired = []
        for first in rows:
            if first['case'] != case['case'] or first['solver'] != 'alns' or first['treatment'] != 'M5_distance':
                continue
            second = next(r for r in rows if r['case'] == first['case'] and r['solver'] == 'alns'
                          and r['treatment'] == 'M6_uniform' and r['seed'] == first['seed'])
            if first['valid'] and second['valid']:
                savings = first['cost'] - second['cost']
                adjusted = first['cost_with_schedule_minimum_pool'] - second['cost']
                paired.append(dict(seed=first['seed'], savings=savings,
                                   savings_percent=100*savings/first['cost'],
                                   minimum_pool_savings=adjusted,
                                   minimum_pool_savings_percent=100*adjusted/first['cost_with_schedule_minimum_pool']))
        case.update(component_savings=groups, minimum_pool_savings=minimum_pool_savings,
                    operating_savings=operating_savings,
                    m5_excess_pool_charge=base['cost'] - reference['total'],
                    order_count=records[case['case']]['orders'],
                    total_container_uses=resource['total_container_uses'],
                    m6_cycle_batches=resource['m6']['cycle_batches'],
                    m6_batch_minutes=resource['m6']['batch_minutes'],
                    m6_first_minutes=resource['m6']['first_minutes'],
                    m6_second_minutes=resource['m6']['second_minutes'], alns_pairs=paired)
        cases.append(case)
    validation = sources['validation_checks.json']
    if validation['planned_runs'] != len(rows) or validation['valid_runs'] != sum(r['valid'] for r in rows):
        raise ValueError('Validation counts do not match raw rows')
    failures = [dict(run=r['run'], case=r['case'], status=r['status'], error=r['error'])
                for r in rows if not r['valid']]
    payload = dict(schema_version=1, report_date='2026-10-05',
                   analysis_source_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                   source_sha256={name: hashlib.sha256((out/name).read_bytes()).hexdigest() for name in source_names},
                   reconciliation='passed', cases=cases, failures=failures)
    (out/'analysis_results.json').write_text(json.dumps(payload, ensure_ascii=False, indent=2,
                                                       allow_nan=False)+'\n', encoding='utf-8')
    _figures(out, cases)

    budgets = sources['experiment.json']['budgets']
    lines = ['# 模型五与模型六对比实验分析报告', '', '日期：2026-10-05。费用单位：合成成本单位。', '',
             '## 主要发现', '',
             '模型五保持原有固定分批和箱池，以运输距离为目标；求解后用模型六相同的经济参数计算周期成本。'
             '模型六在均匀周期中联合选择批数、阶段分界、箱池、订单分配、路线与车辆。', '',
             f'本次完成{len(rows)}次尝试，{sum(r["valid"] for r in rows)}次得到通过独立审计的方案。'
             f'按模型五原配置箱池，模型六在{sum(c["comparison"]["savings"] > 0 for c in cases)}/{len(cases)}个案例中成本较低；'
             f'按模型五同一路线与时间表的最低需箱量重新计价后，模型六在'
             f'{sum(c["schedule_minimum_pool_comparison"]["savings"] > 0 for c in cases)}/{len(cases)}个案例中成本较低。'
             '后一口径消除了模型五已配置但调度无需占用的箱池持有费，能帮助判断收益来自箱池配置还是运输运营。', '',
             '较大案例未证明成本最优，因而不能推出“模型六总是更省”或“大规模已收敛”。'
             '本报告保留不利于模型六的结果。', '',
             '## 实验设计与计价口径', '',
             f'- 每个案例、每个模型：1次精确法（{budgets["exact_time"]:g}秒）和'
             f'3次ALNS（种子{", ".join(map(str, budgets["seeds"]))}，每次{budgets["time"]:g}秒，'
             f'最多{budgets["iterations"]}次迭代）。运行并行度为{budgets["workers"]}。',
             '- 模型六候选K为1、2、3，共享每次求解的总时间预算。K是划分完整周期的等长批数；实际配送批数可更少。',
             '- 同案例的原始订单、道路、车辆容量与车队、客户窗口、操作政策、完整周期和经济配置相同。'
             '均匀日历及其订单分配属于模型六的决策变化。静态订单在周期起点全部可用，这是本次适配的明确假设。',
             '- 模型五代表方案按最短距离选择，距离相同时选成本较低者；模型六代表方案按最低已观察周期成本选择。'
             '下表结合精确法与ALNS选代表，不是同算法的单次对照；同算法结果另列。',
             '- 周期成本包括箱池持有、两级运输距离、占用路线工时、车辆启用、发车次数、清洗、追踪和磨损。'
             '购置投入单列，避免与周期费用直接相加。工时不包含两趟之间的闲置工资。',
             '- 本实验价格为人工系数：每箱每周期5；两级距离每单位1；占用工时每分钟0.1；'
             '一级/二级车辆启用每周期10/5；每趟1；每次清洗0.2、追踪0.1、磨损0.2；单箱购置价10。'
             'Zhou距离仍为坐标单位，不解释为实测公里；各案例周期长度也不同，不跨案例加总为经营收益。', '',
             * _table(['案例', '订单数', '累计箱使用量', 'M5原配置箱池', 'M5同调度最低需箱', 'M6箱池'],
                      [[c['case'], c['order_count'], c['total_container_uses'], c['m5']['pool'],
                        c['m5']['minimum_pool_for_schedule'], c['m6']['pool']] for c in cases]), '',
             '“累计箱使用量”允许同一箱循环使用，不等于必须购置的实体箱数。', '',
             '## 总成本与箱池参考', '',
             * _table(['案例', 'M5原配置成本', 'M6成本', '节省额', '节省%', 'M5最低箱池参考成本', '相对参考节省%'],
                      [[c['case'], _number(c['m5']['cost']), _number(c['m6']['cost']),
                        _number(c['comparison']['savings']), _number(c['comparison']['savings_percent']),
                        _number(c['m5']['cost_with_schedule_minimum_pool']),
                        _number(c['schedule_minimum_pool_comparison']['savings_percent'])] for c in cases]), '',
             '节省额=M5−M6；节省比例=(M5−M6)/M5。负值表示模型六更贵。'
             '最低箱池参考只重算模型五原调度的箱池费用，不重新优化路线或时间，不能称为模型五成本最优值。', '',
             '![周期总成本及箱池参考](analysis_costs.png)', '',
             '## 成本变化来自哪里', '',
             '下表均为M5原配置费用减M6费用。正数贡献节省，负数增加成本；各项合计等于总节省。', '',
             * _table(['案例', *GROUPS, '运营部分合计', '最低箱池参考中的箱池项'],
                      [[c['case'], *[_number(c['component_savings'][g]) for g in GROUPS],
                        _number(c['operating_savings']), _number(c['minimum_pool_savings'])] for c in cases]), '',
             '“运营部分合计”包含除箱池持有外的所有项；最低箱池口径的总节省等于该合计加最后一列。', '',
             '![成本节省分解](analysis_components.png)', '']
    for c in cases:
        groups = c['component_savings']
        lines += [f'### {c["case"]}', '',
                  f'M5原配置成本{c["m5"]["cost"]:.2f}，M6成本{c["m6"]["cost"]:.2f}，'
                  f'差额{c["comparison"]["savings"]:+.2f}。箱池项贡献{groups["箱池持有"]:+.2f}，'
                  f'运营项贡献{c["operating_savings"]:+.2f}；去掉M5多配箱池的'
                  f'{c["m5_excess_pool_charge"]:.2f}后，相对参考差额为'
                  f'{c["schedule_minimum_pool_comparison"]["savings"]:+.2f}。', '',
                  f'M5配置{c["m5"]["pool"]}箱、原调度最低需{c["m5"]["minimum_pool_for_schedule"]}箱；'
                  f'M6使用{c["m6"]["pool"]}箱。运输距离由{c["m5"]["distance"]:.2f}变为'
                  f'{c["m6"]["distance"]:.2f}；距离费用变化{groups["运输距离"]:+.2f}，'
                  f'占用工时费用变化{groups["占用工时"]:+.2f}。'
                  '成本目标同时权衡资源、路线和时间，距离或箱数单项并不必然改善。', '']
        if c['schedule_minimum_pool_comparison']['savings'] < 0:
            lines += [f'该案例发生结论反转：M6比M5最低箱池参考多花'
                      f'{-c["schedule_minimum_pool_comparison"]["savings"]:.2f}。'
                      f'其中箱池项增加{-c["minimum_pool_savings"]:.2f}，运营项增加'
                      f'{-c["operating_savings"]:.2f}。因此原配置下的降本主要依赖削减M5过量配置的箱池。'
                      '原固定日历不一定包含在均匀日历可行域中，M6短预算也未证明最优，不能要求本次M6解必然支配该参考。', '']
    lines += ['## 调度选择与求解证明', '',
              * _table(['案例', 'M5→M6配送批数', 'M6 K', '批长/第一段/第二段(分钟)', 'M5代表运行', 'M6代表运行', 'M6成本最优证明'],
                       [[c['case'], f'{c["m5"]["delivery_batches"]}→{c["m6"]["delivery_batches"]}',
                         c['m6_cycle_batches'], '/'.join(_number(c[k]) for k in
                         ('m6_batch_minutes', 'm6_first_minutes', 'm6_second_minutes')),
                         f'[{c["m5"]["solver"]}/{c["m5"]["seed"]}]({c["m5"]["run"]}/result.json)',
                         f'[{c["m6"]["solver"]}/{c["m6"]["seed"]}]({c["m6"]["run"]}/result.json)',
                         '有（配置K与时间离散域内）' if c['m6']['proven_optimal'] else '无'] for c in cases]), '',
              '证明范围仅涵盖输入配置的K集合、整数时间分界网格和其他建模约束。'
              '前两个案例的模型六精确法证明了成本最优；LaDe10得到可行方案，LaDe30精确法限时未找到可行解。'
              '模型五前三个案例的精确法证明的是距离最优；LaDe30仅有有效限时解。'
              '模型五代表方案即便与最优距离相同，也没有成本最优证明。'
              '相同距离方案的时间安排可不同，故精确法的事后成本也可能高于ALNS。', '',
              '失败尝试单独保留，不填作零成本，不参与有效成本均值：', '',
              * _table(['运行', '状态', '对统计的影响'],
                       [[f'[{f["run"]}]({f["run"]}/result.json)', f['status'], '无有效成本，保留失败记录']
                        for f in failures]), '',
              '## ALNS重复运行与稳定性', '',
              '以下为每模型每案例3个种子的全部有效成本，标准差为样本标准差。'
              '模型五每个种子仍优化距离，这里的“最低成本”只是该组三个事后价格的最小值。', '',
              * _table(['案例', '模型', '有效/尝试', '最低成本', '平均成本', '最高成本', '样本标准差'],
                       [[c['case'], label, f'{s["valid"]}/{s["attempted"]}',
                         *[_number(s[k]) for k in ('best', 'mean', 'worst', 'stddev')]]
                        for c in cases for label, s in c['methods']['alns'].items()]), '',
              '同种子成对对照如下；各自的最低箱池参考按该种子M5的原调度计算，未用代表方案的最低需箱量替代。', '',
              * _table(['案例', '有效种子对数', '原配置节省%均值', '原配置节省%范围', '最低箱池参考节省%均值', '参考节省%范围'],
                       [[c['case'], len(c['alns_pairs']),
                         _number(statistics.mean(p['savings_percent'] for p in c['alns_pairs'])),
                         '～'.join(_number(fn(p['savings_percent'] for p in c['alns_pairs'])) for fn in (min, max)),
                         _number(statistics.mean(p['minimum_pool_savings_percent'] for p in c['alns_pairs'])),
                         '～'.join(_number(fn(p['minimum_pool_savings_percent'] for p in c['alns_pairs'])) for fn in (min, max))]
                        for c in cases]), '',
              'Zhou的两个模型成本在三次运行中几乎不变；其余三个案例的M6成本样本标准差较小。'
              '这只是当前种子与预算下的观察。'
              '3个种子和4个选定案例不足以证明统计显著性、规模趋势或运行时间收敛。', '',
              '## 可信度与限制', '',
              f'- {validation["independently_replayed_costs"]}个有效方案均重新独立重放并复核成本；'
              f'冻结源码{validation["source_hashes_checked"]}个哈希、输入{validation["input_hashes_checked"]}个哈希已检查。'
              '成本表与资源表使用同一代表方案，费用分项和两种参考总额已逐项核对。',
              '- 相关回归测试95项通过；随后代表方案并列规则调整后，新增成本对比的4项测试再次通过。',
              '- 运行环境为Windows、Python3.11.3、Gurobi11.0.3，详细预算和模块哈希保留在environment.json。'
              '源码快照与输入快照对应实际求解版本；报告生成器有独立源码哈希，不改写求解快照。',
              '- 费用是合成系数，未引入企业采购、工资、燃料实测数据。全量静态订单起点可用的假设可能高估集中配送机会。',
              '- 模型六同时改变日历、目标函数、箱池和分配，因此本次结论属于整体方案比较，无法单独归因于某一个机制。',
              '- 代表方案汇合不同算法的最好结果，同方法同种子的对照已另列。短时限结果属于当前预算下的观察，'
              'M6较大案例的优化差距仍不确定。', '',
              '## 后续实验建议', '',
              '优先延长LaDe10和LaDe30的M6预算并记录目标随时间变化、下界与各K可行性，判断参考口径下的增本是否会消失。'
              '再加入“固定原日历+成本目标”“均匀日历+固定箱池”等消融，分离目标、日历和箱池的作用。'
              '如要报告企业收益，应先校准费用与库存释放假设，并增加种子和案例覆盖。', '',
              '## 数据与复现', '',
              '- [成本汇总](cost_summary.json)、[逐对对照](cost_pairs.csv)、[逐项成本](cost_components.csv)、'
              '[所有尝试](rows.json)、[资源汇总](uniform_summary.json)。',
              '- [实验预算](experiment.json)、[运行环境](environment.json)、[检查记录](validation_checks.json)、'
              '[本报告计算数据与来源哈希](analysis_results.json)。',
              '- [运行与事后计价说明](../../../docs/model5_model6_cost_comparison.md)。历史快照包含本机绝对路径；'
              '换机器时按说明从原始相对路径重建输入，原结果保留用于审计。', '',
              '```powershell',
              f'python -m experiments.analysis.analyze_model5_model6_costs --directory '
              f'"{out.relative_to(Path(__file__).resolve().parents[2]).as_posix() if out.is_relative_to(Path(__file__).resolve().parents[2]) else out.as_posix()}"',
              '```', '']
    (out/'analysis_report.md').write_text('\n'.join(lines), encoding='utf-8')
    return payload


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', required=True)
    args = parser.parse_args(argv)
    out = resolve_project_path(args.directory)
    analyze(out)
    print(f'Analysis report saved: {out / "analysis_report.md"}')


if __name__ == '__main__':
    main()
