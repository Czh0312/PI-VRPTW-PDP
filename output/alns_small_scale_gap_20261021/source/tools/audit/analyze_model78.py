"""Descriptive paired analysis of the frozen calendar review; retain failures."""
from __future__ import annotations

import argparse
from collections import Counter
import csv
import json
from pathlib import Path
from statistics import median


def read(path):
    return json.loads(path.read_text())


def distribution(values):
    return dict(n=len(values), minimum=min(values), median=median(values), maximum=max(values)) if values else dict(n=0)


def analyze(root):
    plan = read(root/'plan.json')
    phases = {phase: read(root/phase/'summary.json') for phase in ('baseline', 'candidate')}
    expected = {job['id'] for job in plan['jobs']}
    overview, checks, pairs, gaps, by_case = [], [], [], [], []
    for phase, rows in phases.items():
        assert len(rows) == len(expected) and {r['id'] for r in rows} == expected
        for model in (6, 7, 8):
            for method in ('exact', 'alns'):
                group = [r for r in rows if r['model'] == model and r['method'] == method]
                overview.append(dict(phase=phase, model=model, method=method, runs=len(group),
                    statuses=dict(Counter(r['status'] for r in group)), valid=sum(r['valid'] for r in group),
                    optimal=sum(r.get('optimal', False) for r in group),
                    infeasible=sum(r.get('infeasible', False) for r in group),
                    unresolved=sum(not r['valid'] and not r.get('infeasible', False) for r in group),
                    wall_seconds=distribution([r['wall_time'] for r in group if 'wall_time' in r]),
                    budget_ratio=distribution([r['wall_time']/r['budget'] for r in group if 'wall_time' in r])))
        for case in plan['cases']:
            name = case['name']
            for model in (6, 7, 8):
                group = [r for r in rows if r['name'] == name and r['model'] == model]
                ex = next((r for r in group if r['method'] == 'exact'), None)
                heur = [r for r in group if r['method'] == 'alns']
                costs = [r['objective'] for r in heur if r['valid']]
                by_case.append(dict(phase=phase, name=name, model=model, source=case['family_id'].split('_')[0],
                    customers=case['customer_count'], variant=case['variant'],
                    alns_valid=len(costs), alns_runs=len(heur),
                    alns_best=min(costs) if costs else None, alns_median=median(costs) if costs else None,
                    alns_worst=max(costs) if costs else None,
                    exact_status=ex['status'] if ex else 'NOT_SCHEDULED',
                    exact_cost=ex.get('objective') if ex else None,
                    exact_bound=ex.get('best_bound') if ex else None,
                    exact_partition_count=ex.get('batch_count') if ex else None,
                    exact_delivery_count=ex.get('delivery_batch_count') if ex else None))
                if ex and ex.get('optimal'):
                    for r in heur:
                        if r['valid']:
                            gap = 100*(r['objective']-ex['objective'])/abs(ex['objective'])
                            assert gap >= -1e-5, (phase, r['id'], gap)
                            gaps.append(dict(phase=phase, name=name, model=model, seed=r['seed'], gap_percent=gap))
            # For a minimization problem, a larger domain's certified bound
            # cannot exceed any feasible upper bound in the smaller domain.
            for left, right in ((6, 7), (7, 8)):
                feasible = [r for r in rows if r['name'] == name and r['model'] == left and r['valid']]
                larger = next((r for r in rows if r['name'] == name and r['model'] == right and r['method'] == 'exact'), None)
                if feasible and larger:
                    upper = min(r['objective'] for r in feasible)
                    assert not larger.get('infeasible'), (phase, name, left, right)
                    lower = larger.get('best_bound')
                    assert lower is None or lower <= upper+1e-5, (phase, name, left, right, lower, upper)
                    if larger.get('optimal'):
                        assert larger['objective'] <= upper+1e-5, (phase, name, left, right)
                    checks.append(dict(phase=phase, name=name, smaller=left, larger=right,
                                       feasible_upper=upper, expanded_bound=lower, passed=True))
    before = {r['id']: r for r in phases['baseline']}
    for after in phases['candidate']:
        if after['method'] != 'alns':
            continue
        prior = before[after['id']]
        change = None
        if after['valid'] and prior['valid']:
            change = 100*(after['objective']-prior['objective'])/abs(prior['objective'])
            outcome = 'better' if change < -1e-5 else 'worse' if change > 1e-5 else 'tie'
        elif after['valid']:
            outcome = 'gained_feasible'
        elif prior['valid']:
            outcome = 'lost_feasible'
        else:
            outcome = 'neither_feasible'
        pairs.append(dict(id=after['id'], name=after['name'], model=after['model'], seed=after['seed'],
                          baseline_status=prior['status'], candidate_status=after['status'],
                          outcome=outcome, relative_cost_change_percent=change))
    comparison = []
    for model in (6, 7, 8):
        for source in ('all', 'lade', 'zhou'):
            group = [r for r in pairs if r['model'] == model and (source == 'all' or r['name'].startswith(source+'_'))]
            comparison.append(dict(model=model, source=source, outcomes=dict(Counter(r['outcome'] for r in group)),
                relative_cost_change_percent=distribution([r['relative_cost_change_percent'] for r in group
                                                           if r['relative_cost_change_percent'] is not None])))
    all_rows = [dict(phase=phase, **r) for phase, rows in phases.items() for r in rows]
    invalid_incumbents = [r['id'] for r in all_rows if r.get('objective') is not None and not r['valid']]
    assert not invalid_incumbents, invalid_incumbents
    common_gaps = []
    for model in (6, 7, 8):
        resolved = {phase: {r['name'] for r in by_case if r['phase'] == phase and
                           r['model'] == model and r['exact_status'] == 'OPTIMAL'} for phase in phases}
        common = resolved['baseline'] & resolved['candidate']
        for phase in phases:
            values = [r['gap_percent'] for r in gaps if r['phase'] == phase and
                      r['model'] == model and r['name'] in common]
            common_gaps.append(dict(phase=phase, model=model, common_optimal_cases=sorted(common),
                                    gap_percent=distribution(values)))
    result = dict(overview=overview, paired_alns=comparison, pairs=pairs, optimal_reference_gaps=gaps,
                  common_optimal_reference_gaps=common_gaps, nested_domain_checks=checks,
                  invalid_incumbents=invalid_incumbents, by_case=by_case)
    (root/'analysis.json').write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n')
    with (root/'by_case.csv').open('w', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(by_case[0]))
        writer.writeheader()
        writer.writerows(by_case)
    print(json.dumps(dict(overview=overview, paired_alns=comparison,
                         domain_checks=len(checks), invalid_incumbents=invalid_incumbents), indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    analyze(parser.parse_args().output.resolve())
