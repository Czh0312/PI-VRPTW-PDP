"""Plot logged global lower bounds against the final audited feasible costs."""
import csv
import json
from pathlib import Path
import re

HERE = Path(__file__).resolve().parent
CASES = ['S03', 'S08', 'S09']


def read(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def history(case):
    folder = HERE / case
    certificate = read(folder / 'solver_certificate.json')
    summary = read(folder / 'summary.json')
    upper = summary['best_audited_upper_bound']
    points = [(0., certificate['global_lower_bound'])]
    for line in (folder / 'gurobi.log').read_text(encoding='utf-8').splitlines():
        tokens = line.split()
        if len(tokens) < 7 or not re.fullmatch(r'\d+(?:\.\d+)?s', tokens[-1]):
            continue
        if not (tokens[0].isdigit() or tokens[0] in ('H', '*')):
            continue
        try:
            seconds, bound = float(tokens[-1][:-1]), float(tokens[-4])
        except ValueError:
            continue
        bound = max(certificate['global_lower_bound'], min(certificate['audited_upper_bound'], bound))
        points.append((seconds, bound))
    points.append((summary['mip_runtime'], summary['global_lower_bound']))
    return [(case, seconds, bound, upper, max(0., upper-bound)/abs(upper)) for seconds, bound in points]


def main():
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt

    plt.rcParams.update({'font.sans-serif': ['Microsoft YaHei', 'SimHei', 'DejaVu Sans'],
                         'axes.unicode_minus': False, 'font.size': 10})
    fig, axes = plt.subplots(3, 1, figsize=(8, 7), sharex=True, constrained_layout=True)
    records = []
    for axis, case in zip(axes, CASES):
        rows = history(case)
        records.extend(rows)
        axis.step([row[1]/60 for row in rows], [100*row[4] for row in rows],
                  where='post', color='#2864A0', linewidth=1.8)
        axis.axvline(10, color='#B8BEC6', linestyle='--', linewidth=1)
        axis.set_title(case, loc='left', fontsize=11, fontweight='bold')
        axis.set_ylabel('界差距（%）')
        axis.set_ylim(bottom=0)
        axis.grid(axis='y', color='#E4E7EB', linewidth=.7)
        for side in ('top', 'right'):
            axis.spines[side].set_visible(False)
        axis.annotate(f'{100*rows[-1][4]:.3f}%', (rows[-1][1]/60, 100*rows[-1][4]),
                      xytext=(-6, 7), textcoords='offset points', ha='right', color='#2864A0')
    axes[-1].set_xlabel('本轮优化时间（分钟）；虚线为此前 10 分钟预算\n'
                        '界差距统一以本轮最终审计成本为分母，仅展示下界推进')
    fig.suptitle('模型八：长预算内的全域最优性证明进度', fontsize=14)
    fig.savefig(HERE / 'convergence.png', dpi=180, facecolor='white')
    plt.close(fig)
    with (HERE / 'convergence.csv').open('w', newline='', encoding='utf-8-sig') as stream:
        writer = csv.writer(stream)
        writer.writerow(['case', 'optimization_seconds', 'global_lower_bound_from_log',
                         'final_audited_upper_bound', 'gap_over_final_audited_upper'])
        writer.writerows(records)
    print(json.dumps(dict(rows=len(records), image=str(HERE / 'convergence.png'),
                          note='Log bounds are display-rounded; exact final evidence is in summary.json.')))


if __name__ == '__main__':
    main()
