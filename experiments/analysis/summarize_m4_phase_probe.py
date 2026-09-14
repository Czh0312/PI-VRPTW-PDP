"""Summarize the preselected two-phase strategy on three additional seeds."""
import hashlib
import json
import statistics as stats
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / 'output/experiments/m4_phase_20260908'


def main():
    rows = [json.loads(line) for line in (OUT/'validation.jsonl').read_text(encoding='utf-8').splitlines()]
    manifest = json.loads((OUT/'validation.manifest.json').read_text(encoding='utf-8'))
    expected = {(i,s,f) for i in manifest['instances'] for s in manifest['seeds'] for f in manifest['fractions']}
    assert len(rows) == len(expected) == 132
    assert {(r['instance'],r['seed'],r['fraction']) for r in rows} == expected
    assert all(r['audit']['valid'] for r in rows)
    for p,h in manifest['source_sha256'].items():
        source = ROOT/p
        if source.name == 'model_4.py' and (OUT/'validated_model4_source.py').exists():
            source = OUT/'validated_model4_source.py'
        assert hashlib.sha256(source.read_bytes()).hexdigest()==h
    summaries = {}
    for f in manifest['fractions']:
        subset = [r for r in rows if r['fraction']==f]
        summaries[f] = dict(runs=len(subset),hits=sum(r['hit'] for r in subset),
            mean_gap_pct=stats.mean(r['gap_pct'] for r in subset),
            max_gap_pct=max(r['gap_pct'] for r in subset),
            mean_seconds=stats.mean(r['wall_time'] for r in subset))
    a = {(r['instance'],r['seed']):r for r in rows if r['fraction']==1}
    b = {(r['instance'],r['seed']):r for r in rows if r['fraction']==0.75}
    gains = [100*(a[k]['objective']-b[k]['objective'])/a[k]['objective'] for k in a]
    result = dict(models=summaries,paired=dict(wins=sum(g>1e-6 for g in gains),
        ties=sum(abs(g)<=1e-6 for g in gains),losses=sum(g< -1e-6 for g in gains),
        mean_improvement_pct=stats.mean(gains)),audited=132,source_hashes_unchanged=True)
    (OUT/'summary.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    lines = ['# M4 分阶段搜索验证', '', '22 个标准当日小规模实例，seed=11/23/67，共 132 次。单阶段与 75%＋25% 两阶段各 66 次，全部独立从头运行，每次 5 秒，不使用精确目标停止。',
        '每个实例/种子交替运行两个配置；使用同一模型代码的 search_phase_fraction=1.0 作为单阶段对照。', '',
        '| 配置 | 命中/66 | 平均最优值偏差% | 最大偏差% | 平均秒 |', '| --- | ---: | ---: | ---: | ---: |']
    for f,s in summaries.items():
        lines.append(f"| {f} | {s['hits']}/66 | {s['mean_gap_pct']:.4f} | {s['max_gap_pct']:.4f} | {s['mean_seconds']:.4f} |")
    p=result['paired']
    lines += ['',f"两阶段相对单阶段：{p['wins']} 胜 / {p['ties']} 平 / {p['losses']} 负，平均距离改善 {p['mean_improvement_pct']:.4f}%。",
        '', '| 实例 | 单阶段平均偏差% | 两阶段平均偏差% |', '| --- | ---: | ---: |']
    for name in manifest['instances']:
        x=[r['gap_pct'] for r in rows if r['instance']==name and r['fraction']==1]
        y=[r['gap_pct'] for r in rows if r['instance']==name and r['fraction']==.75]
        lines.append(f'| {name} | {stats.mean(x):.4f} | {stats.mean(y):.4f} |')
    lines += ['', '## 范围与校验', '', '132 次路线全部通过独立覆盖、容量、时间窗、同步与距离审计；验证前后求解器源码哈希一致。',
        '模型与目标函数未改变，没有使用 M7 求解结果作为输入；收益来自搜索过程的时间分配和重新学习。',
        '正式结果未显示总体优势，因此最终恢复单阶段默认值 search_phase_fraction=1.0；0.75 仅作为实验选项保留。M7 原阶段流程未修改。验证时源码快照保存在 validated_model4_source.py；验证运行均显式指定配置，不受默认值调整影响。',
        'probe.jsonl 为四难例探索记录，结束时文件哈希检查未通过（运行期间编辑并撤回了诊断汇总代码），不纳入正式效果统计。正式结论仅使用独立完成、哈希检查通过的 validation.jsonl。',
        '这是三个额外种子上的经验结果，不保证所有实例均改善，也不是全局最优保证。']
    (OUT/'report.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    print(json.dumps(result,indent=2))


if __name__=='__main__':
    main()
