"""Summarize audited single-day runs without treating failures as zero costs."""
import argparse
import csv
from collections import defaultdict
from pathlib import Path
from statistics import mean, stdev


def summarize(directory):
    directory = Path(directory)
    with (directory / 'runs.csv').open(encoding='utf-8', newline='') as handle:
        rows = list(csv.DictReader(handle))
    groups = defaultdict(list)
    for row in rows:
        groups[row['backend'] or row['solver']].append(row)
    lines = ['# 单日基础版运行报告', '',
             '规则：一级整趟先送后取，各仓空箱收齐后一次取回，日终完成回总仓。', '',
             '目标仍为两级总距离；等待作为指标报告。统计均值只包含审计通过的解，失败另列，不能据此作不配对的优劣结论。', '',
             '| 求解方式 | 运行数 | 有效解 | 已证明最优 | 有效解平均距离 | 距离样本标准差 |',
             '| --- | ---: | ---: | ---: | ---: | ---: |']
    for key, group in sorted(groups.items()):
        valid = [r for r in group if r['valid'] == 'True']
        values = [float(r['objective']) for r in valid]
        avg = f'{mean(values):.6f}' if values else '—'
        std = f'{stdev(values):.6f}' if len(values) > 1 else '—'
        lines.append(f"| {key} | {len(group)} | {len(valid)} | {sum(r['proven_optimal'] == 'True' for r in valid)} | {avg} | {std} |")
    lines += ['', '跨不同实例的距离均值仅为运行概览；算法差距应在同一实例和参数下配对解释。', '',
              '| 运行 | 状态 | 审计通过 | 距离 | 一级等待 | 最终完成时间 |',
              '| --- | --- | --- | ---: | ---: | ---: |']
    for r in rows:
        lines.append(f"| {r['run']} | {r['status']} | {r['valid']} | {r['objective'] or '—'} | {r['e1_waiting'] or '—'} | {r['completion_time'] or '—'} |")
    failures = [r for r in rows if r['valid'] != 'True']
    if failures:
        lines += ['', '未获得有效解的运行：', '']
        lines += [f"- {r['run']}: {r['status']}; {r['error'] or '未返回可审计路线，不能据此推断数学不可行。'}" for r in failures]
    lines += ['', '每次运行的 JSON 保存完整路线、时间表、数量平衡和原始求解状态；manifest.json 保存配置、输入和源代码哈希。', '']
    (directory / 'report.md').write_text('\n'.join(lines), encoding='utf-8')
    return directory / 'report.md'


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory')
    summarize(parser.parse_args().directory)
