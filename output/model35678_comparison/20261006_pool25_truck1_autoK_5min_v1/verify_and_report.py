"""Replay all saved routes, verify frozen bytes, and report the new objective.

Case path rebasing permits verification after cloning into another directory.
Original cases and solution files are never rewritten by this verifier.
"""
from __future__ import annotations

from collections import defaultdict
import csv
import hashlib
import json
import math
from pathlib import Path
import sys
import tempfile

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[2]
PRIOR = OUT.parent/'20261006_distance_pool_5min_v1'
MODELS = (3, 5, 6, 7, 8)
ZERO_COMPONENTS = ('e1_work', 'e2_work', 'e1_vehicles', 'e2_vehicles', 'e1_trips', 'e2_trips')


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def write(path, data):
    Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False)+'\n', encoding='utf-8')


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def rebased(value, original_root):
    if isinstance(value, dict):
        return {key: rebased(item, original_root) for key, item in value.items()}
    if isinstance(value, list):
        return [rebased(item, original_root) for item in value]
    if isinstance(value, str):
        normalized = value.replace('\\', '/')
        if normalized.startswith(original_root+'/'):
            return str(ROOT/normalized[len(original_root)+1:])
    return value


def main():
    plan, rows, summary = [read(OUT/name) for name in ('experiment.json', 'rows.json', 'summary.json')]
    assert len(rows) == len(plan['jobs']) == 30
    assert len({row['id'] for row in rows}) == 30
    assert {row['id'] for row in rows} == {job['id'] for job in plan['jobs']}
    assert sha(OUT/'run_study.py') == plan['runner_sha256']
    assert sha(OUT/'inputs/common_economic.json') == plan['common_economic_sha256']
    for name, digest in plan['source_hashes'].items():
        assert sha(OUT/'source_snapshot'/name.replace('\\', '/')) == digest, name
    for record in plan['input_hashes'].values():
        assert sha(OUT/record['snapshot']) == record.get('snapshot_sha256', record['sha256'])
    sys.path.insert(0, str(OUT/'source_snapshot'))
    from configuration.loader import load_routing_case
    from models.routing.common.cycle.instance import load_instance
    from models.routing.common.cycle.audit import audit_solution
    from models.cost.comparison import evaluate_comparison_cost
    from models.cost.joint_batch import JointCostProfile
    profile = JointCostProfile(read(OUT/'inputs/common_economic.json'))
    assert profile.hash == plan['economic_profile_hash']
    assert all(profile.rates[key] == 0 for key in plan['zero_cost_fields'])
    original_root = plan['jobs'][0]['case_path'].replace('\\', '/').split('/output/model35678_comparison/')[0]
    comparisons, diagnostics, count_candidates, replay_checks = [], [], [], []
    old_study = Path(rebased(plan['previous_repriced_study'],original_root))
    assert sha(old_study/'experiment.json')==plan['previous_experiment_sha256']
    assert read(old_study/'experiment.json')['source_hashes']==plan['source_hashes']
    old_rows = read(old_study/'rows.json')
    old_profile = JointCostProfile(read(old_study/'inputs/common_economic.json'))
    alternate = JointCostProfile(read(OUT/'inputs/alternate_economic.json'))
    assert alternate.hash == plan['alternate_profile_hash']
    alternative_rows = []
    jobs = {job['id']: job for job in plan['jobs']}
    for row in sorted(rows, key=lambda item: item['id']):
        job = jobs[row['id']]
        target = OUT/'results'/row['id']
        if not row['valid']:
            replay_checks.append(dict(id=row['id'], valid=False, status=row['status'], error=row.get('error')))
            continue
        case_path = OUT/'cases'/Path(job['case_path'].replace('\\', '/')).name
        config = rebased(read(case_path), original_root)
        with tempfile.TemporaryDirectory(prefix='m35678_replay_') as tmp:
            temporary_case = Path(tmp)/'case.json'
            write(temporary_case, config)
            config, fleet = load_routing_case(temporary_case, use_pool_config=False)
            problem = load_instance(config, fleet)
        result, saved_audit, saved_cost = [read(target/name) for name in
            ('result.json', 'independent_audit.json', 'comparison_cost.json')]
        audit = audit_solution(problem, result)
        assert audit['valid'] and audit['periodic_replay']['valid'], (row['id'], audit['errors'])
        assert audit['periodic_replay']['cycles'] == 2
        assert saved_audit['valid'] and saved_audit['periodic_replay']['valid']
        cost = evaluate_comparison_cost(problem, result, config['economic_config'], audit=audit)
        assert cost['economic_profile_hash'] == saved_cost['economic_profile_hash'] == profile.hash
        assert cost['components'].keys() == saved_cost['components'].keys()
        assert all(math.isclose(value, saved_cost['components'][name], abs_tol=1e-6, rel_tol=1e-9)
                   for name, value in cost['components'].items())
        assert all(cost['components'][name] == 0 for name in ZERO_COMPONENTS)
        assert math.isclose(cost['total'], row['cost'], abs_tol=1e-6)
        assert math.isclose(cost['total'], saved_cost['total'], abs_tol=1e-6)
        if row['model'] in (6, 7, 8):
            assert problem.batch_count_candidates==(1,2,3)
            assert {c['batch_count'] for c in result['count_search']}=={1,2,3}
            assert result['info']['time_budget']==job['budget']==300
            assert result['info']['batch_count_candidates']==[1,2,3]
            assert math.isclose(cost['total'], result['info']['objective'], abs_tol=1e-5, rel_tol=1e-7)
            assert row['pool'] == audit['minimum_pool_for_schedule']
            valid_candidates = [item for item in result['count_search'] if item.get('valid')]
            selected_candidate = next(item for item in valid_candidates if item['batch_count'] == row['k'])
            assert selected_candidate['container_pool_size'] == row['pool']
            assert math.isclose(selected_candidate['objective'], row['cost'], abs_tol=1e-6)
            assert math.isclose(min(item['objective'] for item in valid_candidates), row['cost'], abs_tol=1e-6)
        returned = sum(release['quantity'] for release in audit['depot_releases'])
        picked = sum(event['quantity'] for trip in result['e1_trips'] for event in trip['events'] if event['kind'] == 'p')
        assert returned == picked == audit['container_balance']['returned_to_depot'] == problem.total
        assert audit['container_balance']['issued'] == problem.total
        assert math.isclose(cost['components']['cleaning'], returned*profile.rates['cleaning_cost_per_return'], abs_tol=1e-8)
        pool = cost['container_pool_size']
        assert math.isclose(cost['components']['container_pool'], pool*25, abs_tol=1e-8)
        weighted_distance = sum(audit['distances'][layer]*profile.rates['distance_scale_to_km']*profile.rates[layer+'_distance_cost_per_km'] for layer in ('e1','e2'))
        assert math.isclose(cost['total'], weighted_distance+25*pool+sum(profile.constant_cost(problem.total).values()), abs_tol=1e-6)
        alt_cost = alternate.evaluate(audit, pool)
        alt_cost.update(container_pool_size=pool, model=row['model'], cost_is_reoptimized=False,
                        primary_profile_hash=profile.hash, primary_total=cost['total'])
        assert math.isclose(alt_cost['total']-cost['total'], 0.6*problem.total, abs_tol=1e-6)
        write(target/'alternate_clean08_cost.json', alt_cost)
        alternative_rows.append(dict(id=row['id'], family=row['family'], model=row['model'],
            primary_clean02_total=cost['total'], alternate_clean08_total=alt_cost['total'],
            cleaning_02=cost['components']['cleaning'], cleaning_08=alt_cost['components']['cleaning'],
            total_difference=alt_cost['total']-cost['total'], Q=problem.total,
            cost_is_reoptimized=False))
        calendar = result['operating_policy']
        assert calendar['cycle_start'] == problem.operating_policy.cycle_start
        assert calendar['cycle_end'] == problem.operating_policy.cycle_end
        assert all(release['clean_ready'] <= calendar['cycle_end'] for release in audit['depot_releases'])
        assert audit['terminal_state']['clean'] == pool
        assert all(quantity == 0 for state, quantity in audit['terminal_state'].items() if state != 'clean')
        demands = {batch['id']: batch['container_demand'] for batch in result['batches']}
        actual_batches = sum(quantity > 0 for quantity in demands.values())
        assert actual_batches == row['delivery_batches']
        returned_by_batch = defaultdict(int)
        for trip in result['e1_trips']:
            returned_by_batch[trip['batch_id']] += sum(event['quantity'] for event in trip['events'] if event['kind'] == 'p')
        diagnostics.append(dict(id=row['id'], family=row['family'], model=row['model'],
            configured_or_optimized_pool=pool, minimum_pool=audit['minimum_pool_for_schedule'],
            total_box_uses=problem.total, cycle_batch_count=len(calendar['batches']),
            actual_delivery_batches=actual_batches, demand_by_batch=demands,
            returned_by_batch={batch['id']: returned_by_batch[batch['id']] for batch in result['batches']},
            cleaning_cost=cost['components']['cleaning'], calendar=calendar['batches'],
            last_depot_unload=max(release['unload_complete'] for release in audit['depot_releases']),
            last_clean_ready=max(release['clean_ready'] for release in audit['depot_releases']),
            cycle_start=calendar['cycle_start'], cycle_end=calendar['cycle_end'],
            terminal_state=audit['terminal_state'], periodic_replay=audit['periodic_replay']))
        for candidate in result.get('count_search', []):
            count_candidates.append(dict(id=row['id'], family=row['family'], model=row['model'],
                demand=problem.total, selected_cycle_k=row['k'], **candidate))
        old = next(item for item in old_rows if item['id'] == row['id'])
        old_audit_path = old_study/'results'/old['id']/'independent_audit.json'
        old_audit = read(old_audit_path)
        assert old_audit['valid'] and old_audit['periodic_replay']['valid']
        old_original = old_profile.evaluate(old_audit, old['pool'])
        assert math.isclose(old_original['total'], old['cost'], abs_tol=1e-6)
        old_price = profile.evaluate(old_audit, old['pool'])
        assert math.isclose(old_price['total']-old_original['total'],
                            (profile.rates['e1_distance_cost_per_km']-old_profile.rates['e1_distance_cost_per_km'])*old_audit['distances']['e1']+10*old['pool']+0.2*old_audit['container_balance']['issued'],abs_tol=1e-6)
        comparisons.append(dict(id=row['id'], family=row['family'], model=row['model'],
            old_route_new_prices=old_price['total'], fresh_search_cost=row['cost'],
            reduction_percent=(old_price['total']-row['cost'])/old_price['total']*100,
            old_pool=old['pool'], old_profile_cost=old['cost'],
            old_audit_sha256=sha(old_audit_path), old_audit_path=str(old_audit_path), new_pool=pool))
        replay_checks.append(dict(id=row['id'], valid=True, repeated_cycles=2,
            saved_audit_valid=saved_audit['valid'], cost_matches_objective=row['model'] in (6, 7, 8),
            selected_candidate_matches=row['model'] in (6, 7, 8),
            zero_components=list(ZERO_COMPONENTS), returned_box_uses=returned,
            formula_verified='D1 + 0.056470588D2 + 25N + 0.8Q',
            automatic_count_selection_verified=row['model'] in (6,7,8),
            count_search_budgets=[dict(k=c['batch_count'],seconds=c['budget']) for c in result.get('count_search',[])]))
    assert len(replay_checks) == 30
    write(OUT/'verification.json', dict(checked=30, valid=sum(item['valid'] for item in replay_checks),
        frozen_hashes_valid=True, economic_profile_hash=profile.hash, checks=replay_checks))
    write(OUT/'pool_cycle_cleaning_diagnostics.json', diagnostics)
    write(OUT/'count_candidates.json', count_candidates)
    write(OUT/'same_price_search_comparison.json', comparisons)
    write(OUT/'alternate_cleaning_comparison.json', dict(primary_profile_hash=profile.hash,
        alternate_profile_hash=alternate.hash, rows=alternative_rows,
        separately_searched=False, complete_cycle_difference='0.6Q'))
    if comparisons:
        with (OUT/'same_price_search_comparison.csv').open('w', encoding='utf-8-sig', newline='') as handle:
            writer = csv.DictWriter(handle, fieldnames=list(comparisons[0]))
            writer.writeheader()
            writer.writerows(comparisons)
    write_report(plan, rows, summary, comparisons, diagnostics, count_candidates)
    plot_costs(summary)
    print(f'VERIFIED {sum(item["valid"] for item in replay_checks)}/30 routes, costs, frozen bytes and two-cycle replay')


def write_report(plan, rows, summary, comparisons, diagnostics, count_candidates):
    from report_content import render_report
    render_report(OUT, plan, rows, summary, comparisons, diagnostics, count_candidates)


def plot_costs(summary):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    import numpy as np
    plt.rcParams.update({'font.family': 'DejaVu Sans', 'font.size': 10,
        'axes.spines.top': False, 'axes.spines.right': False})
    groups = [('Container pool', '#3b6ea8', ['container_pool']),
              ('Truck distance', '#e99d3c', ['e1_distance']),
              ('Tricycle distance', '#5aa27a', ['e2_distance']),
              ('Cleaning / tracking / wear', '#a0a7b0', ['cleaning', 'tracking', 'container_wear'])]
    fig, axes = plt.subplots(2, 3, figsize=(16, 10))
    for ax, index in zip(axes.flat, [0, 2, 4, 1, 3, 5]):
        case = summary['cases'][index]
        selected, x, bottoms = case['selected'], np.arange(5), np.zeros(5)
        for label, color, keys in groups:
            values = [sum(selected[str(model)]['cost_components'][key] for key in keys)
                      if selected[str(model)] else 0 for model in MODELS]
            ax.bar(x, values, bottom=bottoms, color=color, label=label, width=.65)
            bottoms += np.array(values)
        for position, total in enumerate(bottoms):
            ax.annotate(f'{total:,.1f}' if total else 'No solution', (position, total),
                xytext=(0, 5), textcoords='offset points', ha='center', fontsize=9)
        title = case['family'].replace('zhou_', '').replace('_subset50', '')
        ax.set_title(f'{title}\n{case["customers"]} customers; Q={case["demand"]}')
        ax.set_xticks(x, [f'M{model}' for model in MODELS])
        ax.set_ylabel('Synthetic cost units per cycle')
        ax.set_ylim(0, max(bottoms)*1.18 if max(bottoms) else 1)
        ax.grid(axis='y', alpha=.18)
        ax.set_axisbelow(True)
    handles, labels = axes[0, 0].get_legend_handles_labels()
    fig.legend(handles, labels, loc='lower center', bbox_to_anchor=(.5, .055), ncol=4, frameon=False)
    fig.suptitle('Automatic K selection: pool25, truck1, cleaning0.2', fontsize=16)
    fig.text(.5, .015, 'Six large cases; 300 seconds; seed 42. M3/M5 distance objective; M6-M8 new cost objective.',
        ha='center', fontsize=10, color='#555555')
    fig.tight_layout(rect=(0, .12, 1, .95))
    fig.savefig(OUT/'cost_components.png', dpi=180)
    fig.savefig(OUT/'cost_components.svg')
    plt.close(fig)


if __name__ == '__main__':
    main()
