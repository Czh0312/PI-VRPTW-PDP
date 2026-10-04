"""Independent whole-cycle audits, relaxed-column replay and gap acceptance."""
import hashlib
import json
import math
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(HERE/'diagnostic_60s/source_snapshot'))
from configuration.loader import load_routing_case
from models.routing.common.cycle import load_instance
from models.routing.common.cycle.audit import audit_solution
from models.routing.solvers.exact.multibatch_bounds import distance_certificate
from validation_columns import replay_column, trip_cost, close
from validation_e1 import verify_e1


def read(path):
    return json.loads(Path(path).read_text())


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


artifacts, certificates, audit_count, columns, witnesses = {}, [], 0, 0, 0


def hash_manifest(directory):
    manifest = read(directory/'manifest.json')
    for name, digest in manifest['artifact_sha256'].items():
        assert sha(directory/name) == digest, (directory, name)
    return manifest


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
    e1_path = directory/(job['id']+'_pricing_models/e1_inventory.mps')
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
            files = list((directory/(job['id']+'_pricing_models')).glob(f'site_{index}_best_pricing*.mps'))
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


if __name__ == '__main__':
    plan = read(HERE/'plan.json')
    original = HERE/'diagnostic_60s'
    hash_manifest(original)
    rows = read(original/'summary.json')
    latest = []
    for i, row in enumerate(rows):
        job = row['job']
        p = load_instance(*load_routing_case(job['case']))
        initial = read(job['initial_result'])
        before = full_audit(p, initial)
        first = read(original/(job['id']+'.json'))
        audited = full_audit(p, first['result'])
        assert audited['objective'] <= before['objective']+1e-6
        def paths(result):
            return [(t['satellite_id'], t['vehicle_id'], t['trip_index'],
                [(e['order_id'], e['kind']) for e in t['events']]) for t in result['e2_trips']]
        assert paths(first['result']) == paths(initial)
        close(audited['distances']['e2'], before['distances']['e2'])
        assert first['result']['info']['best_bound'] is None and first['result']['info']['mip_gap'] is None
        lower = verify_certificate(p, first, original)
        warm = read(HERE/'warm_60s_plus_polish_5s'/(job['id']+'.json'))
        searched = full_audit(p, warm['alns_result'])
        best = full_audit(p, warm['result'])
        assert best['objective'] <= searched['objective']+1e-6 <= audited['objective']+2e-6
        close(warm['inherited_global_lower_bound'], lower)
        result_path = HERE/'warm_60s_plus_polish_5s'/(job['id']+'.json')
        bound_path = original/(job['id']+'.json')
        strict = HERE/'strict_pricing_100s'
        if i >= 2 and strict.exists():
            strict_rows = read(strict/'summary.json')
            sr = strict_rows[i-2]
            if sr['failure'] is None:
                payload = read(strict/(sr['job']['id']+'.json'))
                sa = full_audit(p, payload['result'])
                close(sa['objective'], best['objective'])
                new_lower = verify_certificate(p, payload, strict)
                if new_lower > lower:
                    lower = new_lower
                    bound_path = strict/(sr['job']['id']+'.json')
        old_lower = first['certificate']['distance_certificate']['bound']
        upper, old_upper = best['objective'], before['objective']
        assert old_lower <= lower <= upper+1e-6
        config = plan['diagnostic_cases'][i]
        gap = 100*(upper-lower)/abs(upper)
        latest.append(dict(customers=config['customers'], pool=config['pool_label'], pool_size=config['pool'],
            case_id=config['case_id'], old_upper=old_upper, old_lower=old_lower,
            old_gap_percent=100*(old_upper-old_lower)/abs(old_upper), upper=upper, lower=lower,
            gap_percent=gap, meets_five_percent=gap <= 5,
            route_improvement_percent=100*(old_upper-upper)/abs(old_upper),
            new_upper_old_lower_gap_percent=100*(upper-old_lower)/abs(upper),
            old_upper_new_lower_gap_percent=100*(old_upper-lower)/abs(old_upper),
            result_path=str(result_path), certificate_path=str(bound_path)))
        print(latest[-1], flush=True)
    if (HERE/'strict_pricing_100s').exists():
        hash_manifest(HERE/'strict_pricing_100s')
    joint = read(HERE/'joint_50_tight_120s/result.json')
    p = load_instance(*load_routing_case(rows[1]['job']['case']))
    close(full_audit(p, joint['result'])['objective'], latest[1]['upper'])
    assert not joint['meets_five_percent']
    failed = read(HERE/'large_certificate_100s/summary.json')
    assert all(r['failure'] == 'EXIT_1' and 'bound' not in r for r in failed)
    hash_manifest(HERE/'large_certificate_100s')
    warm_manifest = read(HERE/'warm_60s_plus_polish_5s/manifest.json')
    for name, digest in warm_manifest['source_sha256'].items():
        assert sha(HERE/'diagnostic_60s/source_snapshot'/name) == digest
    core = read(ROOT/'output/experiments/model5_alns_round6_20261001/current_m5_sha256.json')['sha256']
    unchanged = {name: sha(ROOT/name) == digest for name, digest in core.items()}
    assert all(unchanged.values())
    for file in HERE.rglob('*.json'):
        if file.name not in ('validation.json', 'joint_pool_validation.json'):
            artifacts[str(file.relative_to(HERE))] = sha(file)
    report = dict(target_gap_percent=5, met_cases=sum(r['meets_five_percent'] for r in latest),
        diagnostic_cases=4, full_target_achieved=False, cases=latest,
        whole_cycle_audits=audit_count, replayed_columns=columns, checked_distance_witnesses=witnesses,
        certificates=certificates, original_failed_pricing_runs=2,
        default_alns_unchanged=unchanged, artifact_sha256=artifacts,
        scope=plan['scope'], caveat='Observed diagnostics, extra hybrid runtime, no significance or generalization claim. Restricted subproblem bounds never certify full M5.')
    (HERE/'validation.json').write_text(json.dumps(report,ensure_ascii=False,indent=2,allow_nan=False)+'\n')
    print(dict(met=report['met_cases'], audits=audit_count, columns=columns, witnesses=witnesses))
