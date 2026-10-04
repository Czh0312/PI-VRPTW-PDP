"""Reuse the previous independently verified audit and certificate checks."""
import hashlib
import math
from pathlib import Path
import json
import sys
HERE=Path(__file__).resolve().parent
OLD=HERE.parent/'model5_gap_target_20261001'
sys.path.insert(0,str(OLD))
from models.routing.common.cycle.audit import audit_solution
from models.routing.solvers.exact.multibatch_bounds import distance_certificate
from validation_columns import replay_column,trip_cost,close
from validation_e1 import verify_e1
artifacts,certificates,audit_count,columns,witnesses={},[],0,0,0
def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def full_audit(problem, result):
    global audit_count
    result = json.loads(json.dumps(result))
    audited = audit_solution(problem, result)
    assert audited['valid'] and audited['periodic_replay']['valid'], audited['issues']
    close(audited['objective'], result['info']['objective'])
    assert not result['info'].get('proven_optimal')
    audit_count += 1
    return audited

def verify_certificate(problem, payload, directory):
    global columns, witnesses
    cert, job = payload['certificate'], payload['job']
    assert not cert['proven_optimal'] and not cert['proven_infeasible']
    assert cert['scope'] == 'M5_relaxed_E2_route_cover_plus_E1_certificate'
    baseline = distance_certificate(problem)
    assert cert['distance_certificate'] == baseline
    distance_e1 = math.fsum(max(0, r['bound']-1e-7*max(1, r['bound']))
        for r in baseline['components'] if r['echelon'] == 'E1')
    close(distance_e1, cert['distance_e1_bound'])
    e1_path = directory/job['id']/'pricing/e1_inventory.mps'
    e1_check = verify_e1(problem, payload['result'], cert['e1_certificate'], e1_path)
    close(cert['e1_bound'], max(distance_e1, cert['e1_certificate']['bound']))
    artifacts[str(e1_path.relative_to(HERE))] = sha(e1_path)
    for index, site in enumerate(cert['sites']):
        site_upper = math.fsum(trip_cost(problem, site['satellite'],
            [(e['order_id'], e['kind']) for e in t['events']])
            for t in payload['result']['e2_trips'] if t['satellite_id'] == site['satellite'])
        assert site['bound'] <= site_upper+1e-6
        orders = [o for o in problem.orders if o.satellite == site['satellite']]
        K = min(2*len(orders), problem.fleet.e2_per_sat_count*problem.operating_policy.e2_max_trips_per_vehicle)
        candidates = [0.0]
        for column in site['columns']:
            replay_column(problem, site['satellite'], column)
            columns += 1
        for w in site['iterations']:
            if w['method'] == 'insertion':
                assert w['candidate_bound'] is None
                continue
            pi = {tuple(k): v for k, v in w['duals']}
            assert set(pi) == {(o.id, kind) for o in orders for kind in ('d', 'p')}
            price, nu = w['pricing'], w['fleet_dual']
            assert nu <= 0 and w['maximum_trips'] == K
            feasible = [g for g in price['groups'] if not g['infeasible']]
            close(price['lower_bound'], min((g['lower_bound'] for g in feasible), default=0))
            assert {g['category'] for g in price['groups']} == {None}|{o.batch for o in orders}
            for g in feasible:
                eligible = [(o.id, k) for o in orders for k in ('d', 'p')
                    if k == 'p' or o.batch == g['category']]
                analytic = -math.fsum(max(0, pi[k]) for k in eligible)-nu
                if g.get('margin') == 0 and g.get('runtime') == 0:
                    close(g['raw_lower_bound'], analytic)
                    close(g['lower_bound'], analytic)
                elif g.get('raw_lower_bound') is not None:
                    margin = 1e-7*max(1, math.fsum(abs(pi[k]) for k in eligible), abs(nu))
                    close(g['margin'], margin)
                    close(g['lower_bound'], max(analytic, g['raw_lower_bound']-margin))
                else:
                    close(g['lower_bound'], analytic)
            if price['reduced_cost'] is not None:
                replay_column(problem, site['satellite'], w['new_column'])
                column = w['new_column']
                close(price['reduced_cost'], (0 if w['phase'] == 'feasibility' else column['cost'])
                    -math.fsum(pi[tuple(k)] for k in column['tasks'])-nu)
                assert price['lower_bound'] <= price['reduced_cost']+1e-5
            if w['phase'] == 'distance':
                value = math.fsum(pi.values())+K*nu+K*min(0, price['lower_bound'])
                close(value, w['candidate_bound'])
                candidates.append(value)
                witnesses += 1
        close(site['bound'], max(candidates))
        if site.get('best_witness') is not None:
            files = list((directory/job['id']/'pricing').glob(f'site_{index}_best_pricing*.mps'))
            assert files
            for file in files:
                artifacts[str(file.relative_to(HERE))] = sha(file)
    for component in cert['components']:
        old = next(r for r in baseline['components'] if r['echelon'] == 'E2' and r['satellite'] == component['satellite'])
        close(component['distance_bound'], max(0, old['bound']-1e-7*max(1, old['bound'])))
        close(component['bound'], max(component['distance_bound'], component['route_cover_bound']))
    raw = cert['e1_bound']+math.fsum(c['bound'] for c in cert['components'])
    close(raw, cert['raw_bound'])
    close(cert['bound'], max(baseline['bound'], max(0, raw-1e-7*max(1, raw))))
    close(payload['bound'], cert['bound'])
    certificates.append(dict(job=job['id'], directory=str(directory), e1_check=e1_check,
        bound=cert['bound'], status=cert['status']))
    return cert['bound']

def verify_focused_site(problem,row,payload,export_dir):
    global columns,witnesses
    for index, site in enumerate([row]):
            site_upper = math.fsum(trip_cost(problem, site['satellite'],
                [(e['order_id'], e['kind']) for e in t['events']])
                for t in payload['result']['e2_trips'] if t['satellite_id'] == site['satellite'])
            assert site['bound'] <= site_upper+1e-6
            orders = [o for o in problem.orders if o.satellite == site['satellite']]
            K = min(2*len(orders), problem.fleet.e2_per_sat_count*problem.operating_policy.e2_max_trips_per_vehicle)
            candidates = [0.0]
            for column in site['columns']:
                replay_column(problem, site['satellite'], column)
                columns += 1
            for w in site['iterations']:
                if w['method'] == 'insertion':
                    assert w['candidate_bound'] is None
                    continue
                pi = {tuple(k): v for k, v in w['duals']}
                assert set(pi) == {(o.id, kind) for o in orders for kind in ('d', 'p')}
                price, nu = w['pricing'], w['fleet_dual']
                assert nu <= 0 and w['maximum_trips'] == K
                feasible = [g for g in price['groups'] if not g['infeasible']]
                close(price['lower_bound'], min((g['lower_bound'] for g in feasible), default=0))
                assert {g['category'] for g in price['groups']} == {None}|{o.batch for o in orders}
                for g in feasible:
                    eligible = [(o.id, k) for o in orders for k in ('d', 'p')
                        if k == 'p' or o.batch == g['category']]
                    analytic = -math.fsum(max(0, pi[k]) for k in eligible)-nu
                    if g.get('margin') == 0 and g.get('runtime') == 0:
                        close(g['raw_lower_bound'], analytic)
                        close(g['lower_bound'], analytic)
                    elif g.get('raw_lower_bound') is not None:
                        margin = 1e-7*max(1, math.fsum(abs(pi[k]) for k in eligible), abs(nu))
                        close(g['margin'], margin)
                        close(g['lower_bound'], max(analytic, g['raw_lower_bound']-margin))
                    else:
                        close(g['lower_bound'], analytic)
                if price['reduced_cost'] is not None:
                    replay_column(problem, site['satellite'], w['new_column'])
                    column = w['new_column']
                    close(price['reduced_cost'], (0 if w['phase'] == 'feasibility' else column['cost'])
                        -math.fsum(pi[tuple(k)] for k in column['tasks'])-nu)
                    assert price['lower_bound'] <= price['reduced_cost']+1e-5
                if w['phase'] == 'distance':
                    value = math.fsum(pi.values())+K*nu+K*min(0, price['lower_bound'])
                    close(value, w['candidate_bound'])
                    candidates.append(value)
                    witnesses += 1
            close(site['bound'], max(candidates))
            if site.get('best_witness') is not None:
                files = list(export_dir.glob('best_pricing_group_*.mps'))
                assert files
                for file in files:
                    artifacts[str(file.relative_to(HERE))] = sha(file)

def signature(p,site):
    c=route_context(p,site)
    places=sorted({site}|{o.customer for o in p.orders if o.satellite==site})
    policy=p.operating_policy
    data=dict(site=site,release=c['release'],maximum_trips=c['maximum_trips'],
        nodes=[dict(key=n['key'],order=asdict(n['order']),weight=n['weight'],volume=n['volume'],window=n['window']) for n in c['nodes']],
        caps=[p.fleet.e2_weight_cap,p.fleet.e2_volume_cap],
        policy={k:getattr(policy,k) for k in ('cycle_start','cycle_end','e2_return_unload_time','e2_turnaround_time')},
        last_cutoff=policy.batches[-1].cutoff,
        arcs=[[a,b,p.distance(a,b),p.travel(a,b)] for a in places for b in places])
    return hashlib.sha256(json.dumps(data,sort_keys=True,separators=(',',':')).encode()).hexdigest()
