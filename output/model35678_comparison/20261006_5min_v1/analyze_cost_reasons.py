"""Audit cost attribution and derived schedule references; retain original runs."""
from copy import deepcopy
from dataclasses import replace
import json
import math
from pathlib import Path
import sys

OUT = Path(__file__).resolve().parent
DEST = OUT / 'cost_analysis'
sys.path.insert(0, str(OUT / 'source_snapshot'))
from configuration.loader import load_routing_case
from models.routing.common.cycle.instance import load_instance, MultiBatchInstance
from models.routing.common.cycle.audit import audit_solution
from models.routing.variants.model_6.alns.solver import _delay_departures
from models.cost.comparison import evaluate_comparison_cost


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)+'\n', encoding='utf-8')


def grouped(cost):
    c = cost['components']
    return dict(pool=c['container_pool'], distance=c['e1_distance']+c['e2_distance'],
        work=c['e1_work']+c['e2_work'], vehicles=c['e1_vehicles']+c['e2_vehicles'],
        trips=c['e1_trips']+c['e2_trips'],
        use=c['cleaning']+c['tracking']+c['container_wear'])


def save_reference(problem, result, economics, folder):
    audit = audit_solution(problem, result)
    assert audit['valid'] and audit['periodic_replay']['valid'], audit.get('errors')
    cost = evaluate_comparison_cost(problem, result, economics, audit=audit)
    assert math.isclose(sum(cost['components'].values()), cost['total'], abs_tol=1e-7)
    write(folder/'result.json', result)
    write(folder/'audit.json', audit)
    write(folder/'cost.json', cost)
    return audit, cost


def advance_single_cutoff(problem, normalized):
    """Retain every E2 event/end and E1 path, advance only E1 recovery clock."""
    original = problem.operating_policy.batches[0]
    returned = [t['return_complete'] for t in normalized['e2_trips']
                if any(e['kind']=='p' for e in t['events'])]
    cutoff = max(returned)
    assert original.start < cutoff <= original.cutoff < original.end
    delta = original.cutoff-cutoff
    new_policy = replace(problem.operating_policy, batches=(replace(original, cutoff=cutoff),))
    revised_problem = MultiBatchInstance(problem.network, problem.orders, new_policy,
                                         model=3, _physical_source=problem)
    result = deepcopy(normalized)
    result['operating_policy'] = new_policy.as_dict()
    for batch in result['batches']:
        batch['cutoff'] = cutoff
    for trip in result['e1_trips']:
        pickups = [e for e in trip['events'] if e['kind']=='p']
        if pickups:
            if not any(e['kind']=='d' for e in trip['events']):
                trip['departure'] -= delta
            for event in pickups:
                event['start'] -= delta
            trip['return_complete'] -= delta
    # These are derived from the old recovery clock; recompute through audit.
    for key in ('cutoff_snapshots', 'depot_releases', 'audit'):
        result.pop(key, None)
    return revised_problem, result, cutoff


def main():
    plan, summary = read(OUT/'experiment.json'), read(OUT/'summary.json')
    by_id = {}
    for job in plan['jobs']:
        folder = OUT/'results'/job['id']
        result = read(folder/'result.json')
        original_cost = read(folder/'comparison_cost.json')
        config, fleet = load_routing_case(job['case_path'], use_pool_config=False)
        problem = load_instance(config, fleet)
        normalized = deepcopy(result)
        _delay_departures(problem, normalized)
        audit, cost = save_reference(problem, normalized, config['economic_config'],
                                     DEST/'references'/job['id']/'delayed_departures')
        assert cost['economic_profile_hash'] == plan['economic_profile_hash']
        assert cost['total'] <= original_cost['total']+1e-7
        assert normalized['e1_trips'] == result['e1_trips']
        for old, new in zip(result['e2_trips'], normalized['e2_trips']):
            assert {k:v for k,v in old.items() if k!='departure'} == {k:v for k,v in new.items() if k!='departure'}
        if job['model'] in (6,7,8):
            assert math.isclose(cost['total'], original_cost['total'], abs_tol=1e-6)
        item = dict(**job, original_cost=original_cost, delayed_departure_cost=cost,
                    delayed_departure_saving=original_cost['total']-cost['total'],
                    original_departures=[t['departure'] for t in result['e2_trips']],
                    normalized_departures=[t['departure'] for t in normalized['e2_trips']])
        if job['model']==3:
            revised_problem, revised, cutoff = advance_single_cutoff(problem, normalized)
            revised_audit, revised_cost = save_reference(revised_problem, revised, config['economic_config'],
                DEST/'references'/job['id']/'delayed_departures_and_advanced_cutoff')
            assert revised_cost['total'] <= cost['total']+1e-7
            assert revised['e2_trips'] == normalized['e2_trips']
            item.update(advanced_cutoff_cost=revised_cost, new_cutoff_minutes=revised_problem.operating_policy.minutes(cutoff),
                        cutoff_only_saving=cost['total']-revised_cost['total'])
        by_id[job['id']] = item
    cases = []
    for case in summary['cases']:
        selected = case['selected']
        winning = min((selected[str(m)] for m in (6,7,8)), key=lambda row:row['cost'])
        m3 = by_id[selected['3']['id']]
        m5 = by_id[selected['5']['id']]
        win = by_id[winning['id']]
        a, b, c = grouped(m3['original_cost']), grouped(m5['original_cost']), grouped(win['original_cost'])
        record = dict(case=case['family'],customers=case['customers'],joint_winner=winning['model'],
            original_m3=m3['original_cost']['total'],original_m5=m5['original_cost']['total'],joint_cost=win['original_cost']['total'],
            savings_vs_original_m3=m3['original_cost']['total']-win['original_cost']['total'],
            savings_vs_original_m5=m5['original_cost']['total']-win['original_cost']['total'],
            component_savings_vs_m3={k:a[k]-c[k] for k in a},component_savings_vs_m5={k:b[k]-c[k] for k in b},
            m3_delayed_departures=m3['delayed_departure_cost']['total'],
            m5_delayed_departures=m5['delayed_departure_cost']['total'],
            m3_advanced_cutoff=m3['advanced_cutoff_cost']['total'],
            m3_departure_saving=m3['delayed_departure_saving'],m5_departure_saving=m5['delayed_departure_saving'],
            m3_cutoff_only_saving=m3['cutoff_only_saving'],m3_new_cutoff_minutes=m3['new_cutoff_minutes'],
            joint_saving_after_m3_departure_normalization=m3['delayed_departure_cost']['total']-win['original_cost']['total'],
            joint_saving_after_m3_departure_and_cutoff_changes=m3['advanced_cutoff_cost']['total']-win['original_cost']['total'])
        joint, norm3, cutoff3 = record['joint_cost'], record['m3_delayed_departures'], record['m3_advanced_cutoff']
        norm5_min = m5['delayed_departure_cost']['schedule_minimum_pool_reference']['total']
        record.update(original_reduction_percent=100*(record['original_m3']-joint)/record['original_m3'],
            after_departure_normalization_reduction_percent=100*(norm3-joint)/norm3,
            after_departure_and_cutoff_reduction_percent=100*(cutoff3-joint)/cutoff3,
            departure_share_of_original_saving_percent=100*record['m3_departure_saving']/record['savings_vs_original_m3'],
            m5_normalized_minimum_pool_cost=norm5_min,
            joint_saving_vs_m5_normalized_minimum_pool=norm5_min-joint,
            joint_reduction_vs_m5_normalized_minimum_pool_percent=100*(norm5_min-joint)/norm5_min)
        cases.append(record)
    write(DEST/'analysis.json', dict(scope='Read-only attribution of registered results plus independently audited derived references.',
        economics_hash=plan['economic_profile_hash'], original_results_unchanged=True,
        delay_rule='Move initial pre-service route waiting to the satellite, retaining all paths, customer event times, return times, pool, calendar, trip and vehicle identities.',
        cutoff_rule='M3-only counterfactual: additionally set the sole cutoff to last E2 empty return and shift E1 collection events/returns earlier by the cutoff difference. This changes the M3 calendar assumption; it is not the original fixed-cutoff M3 result.',
        warning='Reference costs are not results of a new optimization search and do not establish model optima or isolate causal algorithm effects.',
        cases=cases))
    print(json.dumps(cases, ensure_ascii=False, indent=2))
    print('VERIFIED 30 departure-normalized references and 6 M3 cutoff counterfactuals; all two-cycle audits valid.')


if __name__=='__main__':
    main()
