"""Compare complete candidate policies without selecting individual best seeds."""
import json
import statistics as stats
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT/'output/experiments/solver_optimization_20260913'


def read(folder):
    path = OUT/folder/'runs.jsonl'
    return [json.loads(s) for s in path.read_text(encoding='utf-8').splitlines()] if path.exists() else []


def main():
    specs = [
        ('m2_seed_route','m2_seed_route_control','m2_seed_route_candidate',6),
        ('m6_stage_ratio','m6_stage_ratio_control','m6_stage_ratio_candidate',6),
        ('m7_stage_ratio','m7_stage_ratio_control','m7_stage_ratio_candidate',6),
        ('m4_seed20_small','final_small_alns','m4_seed20_small',12),
        ('m7_seed20_small','final_small_alns','m7_seed20_small',12),
        ('m7_stage90_small','final_small_alns','m7_stage90_small',12),
        ('m7_seed20_stage90_small','final_small_alns','m7_seed20_stage90_small',12),
    ]
    results = []
    for name,base,candidate,expected in specs:
        old = {(r['instance'],r['model'],r['seed']):r for r in read(base)}
        pairs = []
        invalid = []
        for c in read(candidate):
            key = (c['instance'],c['model'],c['seed'])
            if key not in old:
                continue
            b = old[key]
            if not b['valid'] or not c['valid']:
                invalid.append(key)
                continue
            assert b['budget']==c['budget'] and b['scenario']==c['scenario']
            pairs.append(dict(instance=key[0],model=key[1],seed=key[2],old=b['objective'],
                new=c['objective'],gain_percent=(b['objective']-c['objective'])/b['objective']*100,
                old_time=b['wall_time'],new_time=c['wall_time']))
        gains = [p['gain_percent'] for p in pairs]
        r = dict(name=name,baseline=base,candidate=candidate,expected_pairs=expected,
            valid_pairs=len(pairs),invalid_pairs=invalid,
            complete=len(pairs)+len(invalid)==expected,
            mean_gain_percent=stats.mean(gains) if gains else None,
            median_gain_percent=stats.median(gains) if gains else None,
            wins=sum(p['new']<p['old']-1e-6 for p in pairs),
            ties=sum(abs(p['new']-p['old'])<=1e-6 for p in pairs),
            losses=sum(p['new']>p['old']+1e-6 for p in pairs),pairs=pairs)
        results.append(r)
        print(json.dumps({k:v for k,v in r.items() if k!='pairs'},ensure_ascii=False))
    (OUT/'candidate_comparison.json').write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding='utf-8')


if __name__=='__main__':
    main()
