"""Summarize the new complete M3 before/after timing experiment."""
from __future__ import annotations

from collections import Counter
import hashlib
import json
from pathlib import Path
import statistics
import sys
import xml.etree.ElementTree as ET

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[2]


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def save(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
    rows, plan = read(OUT / 'rows.json'), read(OUT / 'plan.json')
    assert len(rows) == 18 and len(plan['jobs']) == 18
    for relative, expected in plan['hashes'].items():
        assert sha(ROOT / relative) == expected, relative
    pairs = []
    for i in range(1, 10):
        case = f'S{i:02}'
        old = next(row for row in rows if row['case'] == case and row['version'] == 'before')
        new = next(row for row in rows if row['case'] == case and row['version'] == 'after')
        same_optimum = old['optimal'] and new['optimal'] and abs(old['objective']-new['objective']) <= 1e-6
        pairs.append(dict(case=case, before_status=old['status'], after_status=new['status'],
            before_seconds=old['total_seconds'], after_seconds=new['total_seconds'],
            before_proof_seconds=old['proof_seconds'], after_proof_seconds=new['proof_seconds'],
            same_optimum=same_optimum, objective=new['objective'],
            time_ratio_before_over_after=old['total_seconds']/new['total_seconds'] if same_optimum else None,
            seconds_saved=old['total_seconds']-new['total_seconds'] if same_optimum else None))
    before = [row for row in rows if row['version'] == 'before']
    after = [row for row in rows if row['version'] == 'after']
    complete = all(pair['same_optimum'] for pair in pairs)
    old_sum = sum(row['total_seconds'] for row in before)
    new_sum = sum(row['total_seconds'] for row in after)
    stats = dict(cases=9, complete_pairs=len(pairs), all_pairs_same_proven_optimum=complete,
        before_optimal=sum(row['optimal'] for row in before), after_optimal=sum(row['optimal'] for row in after),
        before_total_seconds=old_sum, after_total_seconds=new_sum,
        total_time_ratio=old_sum/new_sum if complete else None,
        total_time_reduction_percent=100*(1-new_sum/old_sum) if complete else None,
        before_mean_seconds=statistics.mean(row['total_seconds'] for row in before),
        after_mean_seconds=statistics.mean(row['total_seconds'] for row in after),
        before_median_seconds=statistics.median(row['total_seconds'] for row in before),
        after_median_seconds=statistics.median(row['total_seconds'] for row in after),
        before_total_solver_work=sum(sum(p['work'] for p in row['native_phases']) for row in before),
        after_total_solver_work=sum(sum(p['work'] for p in row['native_phases']) for row in after),
        after_min_seconds=min(row['total_seconds'] for row in after),
        after_max_seconds=max(row['total_seconds'] for row in after),
        faster_cases=sum(pair['same_optimum'] and pair['before_seconds'] > pair['after_seconds'] for pair in pairs),
        slower_cases=sum(pair['same_optimum'] and pair['before_seconds'] < pair['after_seconds'] for pair in pairs),
        seed=42, threads=1, formulation='expanded', isolated_process_per_call=True)
    save(OUT / 'paired_comparison.json', pairs)
    save(OUT / 'statistics.json', stats)
    suites = ET.parse(OUT / 'validation.xml').getroot().findall('testsuite')
    counts = {key: sum(int(s.attrib.get(key, 0)) for s in suites)
              for key in ('tests','failures','errors','skipped')}
    save(OUT / 'validation.json', dict(selected_scope=[str((OUT / 'test_evidence.py').relative_to(ROOT))],
        counts=counts, before_optimal=stats['before_optimal'], after_optimal=stats['after_optimal'],
        full_suite_run=False, model_3_only=True,
        underlying_algorithm_regression_reference='output/experiments/m3_small_acceleration_20261029_v1/validation.xml',
        source_inputs_verified=True))

    if complete:
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
        fig, ax = plt.subplots(figsize=(12, 4.8), constrained_layout=True)
        x = list(range(9))
        old_bars = ax.bar([p-.2 for p in x], [p['before_seconds'] for p in pairs], .38,
                          color='#8f9aac', label='Before optimization')
        new_bars = ax.bar([p+.2 for p in x], [p['after_seconds'] for p in pairs], .38,
                          color='#187c78', label='After optimization')
        ax.set_yscale('log')
        ax.set_ylim(min(min(p['before_seconds'], p['after_seconds']) for p in pairs)/2,
                    max(max(p['before_seconds'], p['after_seconds']) for p in pairs)*2)
        ax.set_xticks(x, [p['case'] for p in pairs])
        ax.set_ylabel('Seconds to proven optimum (log scale)')
        ax.set_title('Model 3: all nine 7-customer cases | seed 42 | one thread')
        ax.grid(axis='y', which='major', alpha=.2)
        ax.set_axisbelow(True)
        for bars in (old_bars, new_bars):
            for bar in bars:
                value = bar.get_height()
                ax.text(bar.get_x()+bar.get_width()/2, value*1.08, f'{value:.2f}',
                        ha='center', va='bottom', fontsize=8)
        ax.legend(frameon=False)
        fig.savefig(OUT / 'comparison.png', dpi=240)
        fig.savefig(OUT / 'comparison.svg')
        plt.close(fig)

    lines = ['# 模型3全部小规模：优化前后精确求解速度', '',
        '2026-10-29。仅模型3，当前统一数据集 small/S01–S09，均7单。'
        '本轮重新运行18次独立进程求解，逐例比较优化前后；没有拼接旧耗时。', '',
        f"原入口证最优 {stats['before_optimal']}/9，新入口证最优 {stats['after_optimal']}/9。"
        f"新入口耗时 {stats['after_min_seconds']:.3f}–{stats['after_max_seconds']:.3f} 秒，"
        f"中位数 {stats['after_median_seconds']:.3f} 秒。", '',
        '| 算例 | 优化前/秒 | 优化后/秒 | 耗时比（前÷后） | 最优成本一致 |',
        '|---|---:|---:|---:|---|']
    for pair in pairs:
        old = f"{pair['before_seconds']:.3f}" + ('' if pair['before_status'] == 'OPTIMAL' else f" ({pair['before_status']})")
        new = f"{pair['after_seconds']:.3f}" + ('' if pair['after_status'] == 'OPTIMAL' else f" ({pair['after_status']})")
        ratio = f"{pair['time_ratio_before_over_after']:.2f}×" if pair['time_ratio_before_over_after'] is not None else '未能比较完整证明时间'
        lines.append(f"| {pair['case']} | {old} | {new} | {ratio} | {'是' if pair['same_optimum'] else '未完成'} |")
    lines += ['', f"合计：优化前 {old_sum:.3f} 秒，优化后 {new_sum:.3f} 秒。"]
    if complete:
        lines += [f"九例全部取得相同最优值；总耗时减少 {stats['total_time_reduction_percent']:.2f}%，"
                  f"合计耗时比 {stats['total_time_ratio']:.2f}×。"
                  f"逐例有 {stats['faster_cases']} 例更快、{stats['slower_cases']} 例更慢。", '',
                  'S03只相差约0.00024秒，可视为基本持平；单轮微小波动不解释成稳定加速。', '',
                  f"![逐例耗时对照]({(OUT / 'comparison.png').as_posix()})", '']
    lines += [f"全部Gurobi阶段的work合计：优化前 {stats['before_total_solver_work']:.6f}，"
              f"优化后 {stats['after_total_solver_work']:.6f}。优化后包含新下界、副本初解和完整主模型三个阶段；"
              'work不含Python枚举准备，表中墙钟时间已计入这部分。', '']
    lines += ['**比较口径。** 固定同一订单、网络、车辆、费用、固定日历和箱池；'
        '同为单线程、种子42、expanded表达、MIPGap=0与MIPGapAbs=0，每次最多300秒。'
        '每次使用新Python进程，按算例交替先后顺序，串行运行。表中时间从加载算例开始，'
        '包含完整建模、输入型车次枚举/下界、新内部初解、完整主模型优化、解提取和独立审计。'
        'Python启动、运行器环境预检和归档时间不混入求解时间，进程总耗时另存。', '',
        '**优化前的含义。** route_strengthening=False，不加入新增的车次下界、不供应初解，'
        '直接冷启动求解原完整MILP。测试逐例核对该主模型的Gurobi矩阵指纹与历史原模型一致。'
        '优化后使用默认 route_strengthening=True，只从当前输入生成加强下界及内部初解；'
        '完整主模型仍有全部原路径、车辆、趟位和整数时间选择。两边都不读取历史答案或初解。', '',
        '**精确性。** 最优证明要求完整主模型的原生OPTIMAL，加上独立业务、成本和两周期回放审计通过。'
        '上下界及两边最优成本在1e-6绝对容差内核对。证明完成时刻单独记为proof_seconds，'
        '没有用首次可行解、初解构造完成或松弛最优的时间替代完整证明时间。', '',
        '**适用范围。** 单种子、单轮同机配对，耗时受系统负载影响。主要收益需要按逐例表判断，'
        '原本容易的算例可能因准备成本更慢；不宣称每例均加速。未运行模型1/2/4/5/6/7/8。', '',
        '**记录脚本修订。** 首轮读取未更新的Gurobi指纹属性时失败一次，尚未执行该次放松优化。'
        '修正观察器为先model.update()再读取元数据后，恢复未完成任务；已完成的S01旧入口记录原样保留。'
        '生产求解器、数学约束及业务输入没有在本轮修改。初始/修订计划与源码包、失败日志及修订原因全部保留于'
        'plan_initial.json、study_initial.py、source_inputs.zip、source_inputs_v2.zip、failed_attempts/、driver_correction.json。', '',
        f"**本轮独立证据测试。** 仅选择本目录 test_evidence.py，{counts['tests']-counts['failures']-counts['errors']-counts['skipped']} passed、"
        f"{counts['failures']} failed、{counts['errors']} errors、{counts['skipped']} skipped。"
        '没有重跑全仓库测试，也不把18次优化调用计作18项pytest测试。项目预检READY，Gurobi11.0.3实际整数求解通过。', '',
        '文件：runs.csv/rows.json 逐次计时；paired_comparison.json/statistics.json 对照与汇总；'
        'comparison.png/comparison.svg 图；plan.json/source_inputs_v2.zip 修订后冻结条件；'
        'runs/每次目录包含原生分阶段日志、原业务解、独立审计、指纹与完成哈希；'
        'validation.xml/validation.json 为本轮实际测试；evidence_manifest.json 为结果校验。', '']
    (OUT / 'report.md').write_text('\n'.join(lines), encoding='utf-8')
    files = {p.relative_to(OUT).as_posix(): sha(p) for p in OUT.rglob('*')
             if p.is_file() and '__pycache__' not in p.parts and p.name != 'evidence_manifest.json'}
    save(OUT / 'evidence_manifest.json', dict(files=files))
    print(json.dumps(stats, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
