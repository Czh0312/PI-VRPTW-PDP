"""Separate internal controls, intervention sensitivity, and adoption claims."""
from pathlib import Path
import json

OUT = Path(__file__).resolve().parent
rows = json.loads((OUT / 'summary.json').read_text())
valid = (len(rows) == 24 and all(not r['failed'] and r['valid'] and r['periodic'] and
         r['iterations'] == 500 and r['wall'] < 20 and r['trace'] for r in rows))
def same(a, b):
    return all(a[k] == b[k] for k in ('objective', 'iterations', 'e1', 'e2', 'assignment', 'trace'))
def decision(row):
    return tuple(row[k] for k in ('iteration', 'op', 'current_key', 'proposed_key', 'score', 'new_score', 'accepted'))
groups = []
if valid:
    lookup = {(r['job']['family'], r['job']['seed'], r['job']['clock_scale'], r['job']['source']):r for r in rows}
    for family, seed in sorted({(r['job']['family'], r['job']['seed']) for r in rows}):
        cross = {str(scale):same(lookup[family,seed,scale,'before_source'], lookup[family,seed,scale,'after_source'])
                 for scale in (.95,1.,1.05)}
        base = lookup[family,seed,1.,'before_source']
        low = lookup[family,seed,.95,'before_source']
        high = lookup[family,seed,1.05,'before_source']
        divergence = None
        for a,b in zip(base['trace'], high['trace']):
            if decision(a) != decision(b):
                divergence = dict(baseline=a, perturbed=b,
                    same_proposal_and_scores=all(a[k] == b[k] for k in ('iteration','op','current_key','proposed_key','score','new_score')),
                    acceptance_changed=a['accepted'] != b['accepted'])
                break
        groups.append(dict(family=family, seed=seed, source_equivalence=cross,
            low_scale_control_exact=same(base,low), high_scale_exact=same(base,high),
            baseline_objective=base['objective'], high_scale_objective=high['objective'],
            objective_change_percent=100*(high['objective']/base['objective']-1),
            first_decision_divergence=divergence))
report = dict(runs=len(rows), all_valid_and_fixed_budget=valid, groups=groups,
    internal_controls_passed=valid and all(g['low_scale_control_exact'] and all(g['source_equivalence'].values()) for g in groups),
    scope='Synthetic cooling-only intervention, no real-time quality comparison and no adoption. Does not identify prior equal-time cache regression cause.')
(OUT/'diagnostic_comparison.json').write_text(json.dumps(report,indent=2))
print(json.dumps({k:v for k,v in report.items() if k != 'groups'},indent=2))
for g in groups:
    print(g['family'],g['seed'],g['source_equivalence'],g['low_scale_control_exact'],g['high_scale_exact'],g['objective_change_percent'],
          (g['first_decision_divergence'] or {}).get('same_proposal_and_scores'),
          (g['first_decision_divergence'] or {}).get('acceptance_changed'))
