"""Apply the decision rule written before the complete M6 paired check."""
import json
import statistics as stats
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT/'output/experiments/solver_optimization_20260913'


def main():
    rows = {}
    for ratio in (80,90):
        rows[ratio] = []
        for seed in (42,43):
            dest = OUT/f'm6_ratio{ratio}_seed{seed}'
            assert (dest/'COMPLETE').exists(), f'Incomplete block: {dest.name}'
            rows[ratio] += [json.loads(s) for s in (dest/'runs.jsonl').read_text(encoding='utf-8').splitlines()]
    old = {(r['instance'],r['seed']):r for r in rows[80]}
    paired,unpaired = [],[]
    for r in rows[90]:
        b = old[(r['instance'],r['seed'])]
        assert r['budget']==b['budget'] and r['scenario']==b['scenario']
        if not (b['valid'] and r['valid']):
            unpaired.append(dict(instance=r['instance'],seed=r['seed'],valid80=b['valid'],valid90=r['valid']))
            continue
        paired.append(dict(instance=r['instance'],seed=r['seed'],objective80=b['objective'],
            objective90=r['objective'],gain_percent=(b['objective']-r['objective'])/b['objective']*100,
            wall_time80=b['wall_time'],wall_time90=r['wall_time']))
    gains = [p['gain_percent'] for p in paired]
    wins = sum(p['objective90']<p['objective80']-1e-6 for p in paired)
    losses = sum(p['objective90']>p['objective80']+1e-6 for p in paired)
    mean_gain = stats.mean(gains)
    choice = .9 if mean_gain>0 and wins>losses and min(gains)>=-10 else .8
    result = dict(valid_pairs=len(paired),mean_gain_percent=mean_gain,
        median_gain_percent=stats.median(gains),worst_gain_percent=min(gains),
        wins=wins,ties=len(paired)-wins-losses,losses=losses,
        chosen_ratio=choice,unpaired=unpaired,pairs=paired)
    (OUT/'m6_full_comparison.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    policy = json.loads((OUT/'final_policy.json').read_text(encoding='utf-8'))
    policy.update(status='selected_requires_source_check',m6_stage1_ratio=choice,
                  m6_full_comparison={k:v for k,v in result.items() if k not in ('pairs','unpaired')},
                  selected_at=datetime.now().astimezone().isoformat(timespec='seconds'))
    (OUT/'final_policy.json').write_text(json.dumps(policy,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in result.items() if k!='pairs'},ensure_ascii=False,indent=2))


if __name__=='__main__':
    main()
