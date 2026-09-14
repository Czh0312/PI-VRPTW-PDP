"""Summarize a finished paired budget probe without selecting runs post hoc."""
import argparse
import csv
import json
import random
import statistics
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument('directory', type=Path)
    args = parser.parse_args()
    directory = args.directory
    rows = [json.loads(line) for line in (directory / 'runs.jsonl').read_text(encoding='utf-8').splitlines()]
    settings = json.loads((directory / 'settings.json').read_text(encoding='utf-8'))
    lookup = {(r['instance'], r['seed'], r['variant']): r for r in rows}
    comparisons = [('m4', 'm7'), ('m4', 'lean'), ('m7', 'lean'), ('m4', 'm4_plus_relay')]
    lines = ['# M7 时间分配改进实验', '',
             f'标准同日回收大规模数据；{len(set(r["instance"] for r in rows))} 个实例；'
             f'随机种子 {settings["seeds"]}；主比较每次 {settings["seconds"]} 秒。', '',
             '- m4：当前 M4。',
             '- m7：当前统一实验入口的 M7，分仓阶段 75%，最多 4 个分仓候选。',
             '- lean：分仓阶段 95%，保留 1 个分仓候选，中继最多使用总预算的 5%。其余参数同 m7。',
             f'- m4_plus_relay：直接复用该次 M4 输出，额外给予最多 {settings["extra_relay"]} 秒中继搜索；仅用于机制验证，不能作为等时优势证据。', '',
             '所有运行串行执行，轮换算法顺序。可行性由各模型评估器检查，输出路线距离另行复算。', '',
             '| 方案 | 可行次数 | 平均实际耗时/秒 | 中继非零次数 |',
             '| --- | ---: | ---: | ---: |']
    for variant in ('m4', 'm7', 'lean', 'm4_plus_relay'):
        group = [r for r in rows if r['variant'] == variant]
        if not group:
            continue
        lines.append(f'| {variant} | {sum(r["feasible"] for r in group)}/{len(group)} | '
                     f'{statistics.mean(r["wall_time"] for r in group):.4f} | '
                     f'{sum(r["info"].get("transshipment_count", 0)>0 for r in group)} |')
    lines += ['', '改善率为每个共同可行实例/种子的 `(前者−后者)/前者` 的平均值，正值表示后者更好。', '',
              '| 比较 | 配对数 | 平均改善率 | 后者胜/平/负 | 按实例重采样的 95% bootstrap 区间 |',
              '| --- | ---: | ---: | --- | --- |']
    pair_rows = []
    for before, after in comparisons:
        pairs = [(r, lookup[r['instance'], r['seed'], after]) for r in rows
                 if r['variant'] == before and r['feasible']
                 and (r['instance'], r['seed'], after) in lookup
                 and lookup[r['instance'], r['seed'], after]['feasible']]
        if not pairs:
            continue
        groups = {}
        gains = []
        for a, b in pairs:
            gain = 100 * (a['objective'] - b['objective']) / a['objective']
            gains.append(gain)
            groups.setdefault(a['instance'], []).append(gain)
            pair_rows.append(dict(instance=a['instance'], seed=a['seed'], before=before,
                                  after=after, baseline=a['objective'], objective=b['objective'],
                                  improvement_percent=gain))
        # Resample entire instances, keeping their seed observations together.
        rng = random.Random(20260907)
        clusters = list(groups.values())
        estimates = []
        for _ in range(5000):
            sample = [g for cluster in rng.choices(clusters, k=len(clusters)) for g in cluster]
            estimates.append(statistics.mean(sample))
        estimates.sort()
        wins, losses = sum(g > 1e-7 for g in gains), sum(g < -1e-7 for g in gains)
        lines.append(f'| {before} → {after} | {len(pairs)} | {statistics.mean(gains):.4f}% | '
                     f'{wins}/{len(gains)-wins-losses}/{losses} | '
                     f'[{estimates[125]:.4f}%, {estimates[4874]:.4f}%] |')
    lean_pairs = [r for r in pair_rows if r['before'] == 'm4' and r['after'] == 'lean']
    if lean_pairs:
        by_instance = {}
        for r in lean_pairs:
            by_instance.setdefault(r['instance'], []).append(r['improvement_percent'])
        strongest = max(by_instance, key=lambda name: statistics.mean(by_instance[name]))
        other = [r['improvement_percent'] for r in lean_pairs if r['instance'] != strongest]
        lines += ['', '## 对少数大幅改善的敏感性', '',
                  f'lean 对 M4 的配对改善率中位数为 {statistics.median(r["improvement_percent"] for r in lean_pairs):.4f}%。']
        if other:
            lines += [f'剔除平均改善最大的实例 {strongest}（仅作敏感性分析，不替代主结果）后，'
                      f'其余配对平均改善率为 {statistics.mean(other):.4f}%。因此均值领先不能理解为稳定领先。']
    lines += ['', '## 解释边界', '',
              '- 筛选批次使用 seed=42 的 6 个实例；验证批次固定配置后使用 seed=43/44 的全部 18 个实例。验证包含筛选中的实例，因此是新随机种子验证，不是完全独立的新实例测试。',
              '- 95% 时间分配和单候选同时改变，本实验无法单独归因于其中一个参数。',
              '- 区间仅反映本批实例间差异；未覆盖机器负载、更多随机种子或其他场景的不确定性。',
              '- 相同种子不保证限时搜索逐步一致：冷却、重启、算子学习均受实际耗时影响。',
              '- 额外交接后处理包含回收任务交换等邻域，改善次数不一定等于记录到非零中继事件的次数。',
              '- 本次没有修改生产求解器的默认参数，也未重跑 10 秒或精确法基准。',
              '- 所有原始结果（含路线、分仓、耗时、候选统计）保存在 runs.jsonl；代码哈希保存在 settings.json。', '',
              f'最大输出路线距离复算绝对误差：{max(abs(r["route_cost_delta"]) for r in rows if r["route_cost_delta"] is not None):.3g}。']
    (directory / 'analysis.md').write_text('\n'.join(lines)+'\n', encoding='utf-8')
    with (directory / 'paired_results.csv').open('w', newline='', encoding='utf-8-sig') as handle:
        writer = csv.DictWriter(handle, fieldnames=list(pair_rows[0]))
        writer.writeheader()
        writer.writerows(pair_rows)


if __name__ == '__main__':
    main()
