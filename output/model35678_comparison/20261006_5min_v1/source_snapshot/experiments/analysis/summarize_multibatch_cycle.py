"""Summarize all runs, including failed/infeasible/unsolved cases."""
from __future__ import annotations
import argparse
import csv
from pathlib import Path


def summarize(directory):
    directory = Path(directory)
    with (directory/'runs.csv').open(encoding='utf-8', newline='') as f:
        rows = list(csv.DictReader(f))
    references = {}
    for row in rows:
        if row['proven_optimal'].lower() == 'true' and row['valid'].lower() == 'true':
            references[row['case_variant']] = float(row['objective'])
    for row in rows:
        ref = references.get(row['case_variant'])
        gap = None
        if ref is not None and row['valid'].lower() == 'true':
            value = float(row['objective'])
            gap = 100*(value-ref)/ref if ref else 0 if abs(value) < 1e-9 else None
            if abs(value-ref) <= 1e-7*max(1, abs(ref)):
                gap = 0.0
        row['optimal_gap_percent'] = '' if gap is None else f'{gap:.6f}'
    if rows:
        with (directory/'runs.csv').open('w', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
    valid_count = sum(r['valid'].lower() == 'true' for r in rows)
    lines = ['多批次循环系统验证结果', '', f'共 {len(rows)} 次运行，{valid_count} 个解通过独立审计。',
             '未找到解与已证明不可行分开记录；ALNS 不声明最优性。', '',
             '| 运行 | 状态 | 审计 | 距离 | 已证最优 | 相对最优 gap (%) |',
             '| --- | --- | --- | ---: | --- | ---: |']
    for row in rows:
        lines.append(f"| [{row['run']}]({row['run']}/result.json) | {row['status']} | {row['valid']} | "
                     f"{row['objective']} | {row['proven_optimal']} | {row['optimal_gap_percent']} |")
    lines += ['', 'gap 仅在同一案例和同一容器池存在已证最优参考时计算。',
              '各运行目录保存实际时间表、两个周期的重放审计、输入与源码快照。',
              '机制示例属于情景数据；不能据此推出大规模性能或真实经营费用结论。']
    (directory/'report.md').write_text('\n'.join(lines)+'\n', encoding='utf-8')
    return rows


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory')
    summarize(parser.parse_args().directory)
