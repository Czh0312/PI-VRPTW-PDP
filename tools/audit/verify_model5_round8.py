"""Independent physical projections and complete-domain checks for round8."""
from copy import deepcopy
from datetime import datetime
import argparse
import hashlib
import json
import math
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT/'output/experiments/model5_hybrid_round8_20261005'
sys.path.insert(0, str(OUT/'source_snapshot'))
sys.path.insert(0, str(OUT/'probes/checkers'))
import validation_core as check
import validation_pool as pool
import validation_conjunction as joint
if (OUT/'source_snapshot/configuration/loader.py').is_file():
    from configuration.loader import load_routing_case
else:
    from tools.configuration.loader import load_routing_case
from models.common.cycle import load_instance
from models.routing.model_5.exact.bounds.distance_certificate import distance_certificate
from models.routing.model_5.exact.bounds.route_cg.pickup_pricing import route_context


def read(path):
    return json.loads(Path(path).read_text())


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)+'\n')


def frozen():
    plan = read(OUT/'plan.json')
    for name, digest in plan['sha256'].items():
        assert sha(OUT/name) == digest, name
    for name, digest in plan['external_sha256'].items():
        assert sha(name) == digest, name
    return plan


def physical_pools(p, parent, extra, models, counts):
    last = None
    for directory in sorted(parent.glob('pass_*')):
        initial, result = read(directory/'initial_result.json'), read(directory/'output_result.json')
        check.checked_audit(p, initial); check.checked_audit(p, result); counts['audits'] += 2
        assert result['info']['objective'] <= initial['info']['objective']+1e-6
        assert result['info']['best_bound'] is None and not result['info']['proven_optimal']
        last = result
        if not (directory/'columns.json').exists():
            assert result['info']['route_pool_status'] == 'TIME_LIMIT_BUILD'
            check.close(result['info']['objective'], initial['info']['objective'])
            continue
        slots, columns = read(directory/'slots.json'), read(directory/'columns.json')
        pool.validate_slots(p, initial, slots, extra)
        assert len(slots) == len(columns)
        for slot, rows in zip(slots, columns):
            if slot['events']:
                assert [tuple(k) for k in rows[0]['tasks']] == [(e['order_id'], e['kind']) for e in slot['events']]
            for column in rows:
                check.replay.replay_column(p, slot['satellite_id'], column); counts['columns'] += 1
        models.append(pool.project_pool(p, initial, result, slots, columns, directory/'route_pool.mps'))
    return last


def context_for(p, site, shortest, row=None):
    context = check.independent_context(p, site, shortest)
    if not row:
        return context
    conditions = row.get('supply_conditions', [dict(batch=row.get('batch'), threshold=row.get('threshold'))])
    release = dict(context['release'])
    for condition in conditions:
        bid = condition['batch']; release[bid] = max(release[bid], condition['threshold']+p.operating_policy.e2_dispatch_prep_time)
    nodes = []
    for node in context['nodes']:
        order = node['order']
        earliest = max(order.delivery_window[0], release[order.batch]+shortest[site, order.customer])
        if node['key'][1] == 'p':
            earliest = max(order.pickup_window[0], earliest+order.service_time+order.min_use_time)
        nodes.append({**node, 'window': (earliest, node['window'][1])})
    return {**context, 'release': release, 'nodes': nodes, 'lookup': {n['key']: n for n in nodes}}


def leaf(p, row, context, path):
    if 'best_witness' not in row:
        assert row['status'] == 'TIME_LIMIT_BUILD' and row['bound'] == 0
        assert row['iterations'] == [] and row['columns'] == [] and not row['lp_converged']
        return
    check.check_site(p, row, context)
    joint.verify_pricing_models(p, row, context, path)


def components(ref, baseline):
    e1 = math.fsum(max(0, r['bound']-1e-7*max(1, r['bound']))
                  for r in baseline['components'] if r['echelon'] == 'E1')
    check.close(ref['e1_bound'], max(e1, ref['e1_certificate']['bound']))
    distances = {r['satellite']: max(0, r['bound']-1e-7*max(1, r['bound']))
                 for r in baseline['components'] if r['echelon'] == 'E2'}
    rows = {r['satellite']: r for r in ref['sites']}
    assert set(rows) == set(distances) == {c['satellite'] for c in ref['components']}
    for c in ref['components']:
        check.close(c['distance_bound'], distances[c['satellite']])
        check.close(c['route_cover_bound'], rows[c['satellite']]['bound'])
        check.close(c['bound'], max(c['distance_bound'], c['route_cover_bound']))


def inventory_guard(cert, path, models):
    """Recheck copied original matrices and the conservative accepted bound."""
    import gurobipy as gp
    verification = cert['bound_verification']
    if cert['raw_bound'] is None:
        assert verification['accepted_raw_bound'] is None
        return
    check.close(cert['raw_bound'], min(cert['reported_raw_bound'], verification['replay_raw_bound']))
    original = gp.read(str(path))
    replay_path = path.with_name(path.name.replace('.mps', '_verification.mps'))
    replay = gp.read(str(replay_path))
    try:
        assert (original.NumVars, original.NumConstrs, original.NumGenConstrs) == (
            replay.NumVars, replay.NumConstrs, replay.NumGenConstrs)
        check.close(original.ObjCon, replay.ObjCon)
        for a, b in zip(original.getVars(), replay.getVars()):
            assert a.VarName == b.VarName and a.VType == b.VType
            check.close(a.LB, b.LB); check.close(a.UB, b.UB); check.close(a.Obj, b.Obj)
        for a, b in zip(original.getConstrs(), replay.getConstrs()):
            assert a.Sense == b.Sense
            check.close(a.RHS, b.RHS); check.same_expr(original.getRow(a), replay.getRow(b))
        for a, b in zip(original.getGenConstrs(), replay.getGenConstrs()):
            av, al, ae, ass, ar = original.getGenConstrIndicator(a)
            bv, bl, be, bs, br = replay.getGenConstrIndicator(b)
            assert av.VarName == bv.VarName and al == bl and ass == bs
            check.close(ar, br); check.same_expr(ae, be)
        replay.Params.OutputFlag = 0; replay.Params.Threads = 1; replay.Params.Seed = 43
        replay.Params.Cuts = 0; replay.Params.DualReductions = 0
        replay.Params.IntFeasTol = replay.Params.FeasibilityTol = 1e-9
        replay.Params.MIPGap = replay.Params.MIPGapAbs = 0; replay.Params.TimeLimit = 30
        replay.optimize()
        assert replay.Status in (gp.GRB.OPTIMAL, gp.GRB.TIME_LIMIT)
        assert replay.ObjBound >= cert['raw_bound']-1e-5
        models.append(dict(path=str(replay_path), unchanged_complete_matrix=True,
                           reoptimized_bound=replay.ObjBound))
    finally:
        original.dispose(); replay.dispose()


def verify(name):
    plan = frozen()
    payload = read(OUT/(name+'.json'))
    models, counts = [], dict(audits=0, columns=0)
    p = load_instance(*load_routing_case(OUT/'inputs/case.json', use_pool_config=False))
    shortest = check.floyd(p)
    if payload['job']['type'] == 'warm':
        first, last = payload['initial'], payload['result']
        check.checked_audit(p, first); audit = check.checked_audit(p, last); counts['audits'] += 2
        check.close(first['info']['objective'], read(OUT/'inputs/warm_result.json')['info']['objective'])
        assert audit['objective'] <= first['info']['objective']+1e-6
        physical_pools(p, OUT/(name+'_models'), 1, models, counts)
        check.close(payload['historical_verified_floor'], plan['warm_floor'])
        check.close(payload['combined_gap'], (audit['objective']-plan['warm_floor'])/audit['objective'])
        # This floor keeps its original verified history, not a new cold certificate.
        proof_scope = 'WARM_PRIMAL_VERIFIED_HISTORICAL_FLOOR_UNCHANGED'
        summary = dict(upper=audit['objective'], lower=plan['warm_floor'], gap_percent=100*payload['combined_gap'])
    else:
        out = payload['experiment']; first, last = out['initial_result'], out['result']
        a, b = check.checked_audit(p, first), check.checked_audit(p, last); counts['audits'] += 2
        assert b['objective'] <= a['objective']+1e-6
        assert payload['direct_public_entry'] and payload['service_not_monkeypatched']
        assert out['stages'][0]['stage'] == 'alns' and out['stages'][0]['runtime'] <= 15.5
        dest = OUT/(name+'_models'); extra = payload['job']['options']['extra_slots_per_vehicle']
        for directory in sorted(dest.glob('primal_*')):
            physical_pools(p, directory, extra, models, counts)
        ref = out['reference_certificate']; baseline = distance_certificate(p)
        assert ref['distance_certificate'] == baseline
        models.append(check.verify_e1(p, last, ref['e1_certificate'], dest/'reference/e1_inventory.mps'))
        for index, row in enumerate(ref['sites']):
            ctx = context_for(p, row['satellite'], shortest)
            assert ctx == route_context(p, row['satellite'])
            selected_subset = next((r for r in ref.get('subset_runs', [])
                if r['satellite'] == row['satellite'] and r['certificate'] == row), None)
            if selected_subset:
                check.check_site(p, row, ctx)
            else:
                leaf(p, row, ctx, dest/'reference'/f'site_{index}_best_pricing.mps')
        for number, row in enumerate(ref.get('subset_runs', [])):
            assert not row['restricted_objective_used_as_bound']
            leaf(p, row['certificate'], context_for(p, row['satellite'], shortest),
                 dest/f'subsets_{number}_pricing.mps')
        components(ref, baseline)
        check.close(ref['raw_bound'], ref['e1_bound']+math.fsum(c['bound'] for c in ref['components']))
        check.close(ref['bound'], max(baseline['bound'], max(0, ref['raw_bound']-1e-7*max(1, abs(ref['raw_bound'])))))
        lower, cond = ref['bound'], out['conditional_certificate']
        if cond:
            for number, row in enumerate(cond['conditions'], 1):
                leaf(p, row['certificate'], context_for(p, row['site'], shortest, row),
                     dest/'conditional'/f'condition_{number}_pricing.mps')
            for number, cert in enumerate(cond['masters']):
                if cert['raw_bound'] is not None:
                    check.close(cert['bound'], max(lower, max(0, cert['raw_bound']-cert['margin'])))
                    path = dest/'conditional'/f'master_{number}.mps.gz'
                    inventory_guard(cert, path, models)
                    models.append(joint.check_joint_conjunction(p, last, ref, cert, path,
                                                               dest/'reference/e1_inventory.mps'))
                else:
                    check.close(cert['bound'], lower)
                lower = max(lower, cert['bound'])
            check.close(cond['bound'], lower)
        adaptive = out.get('adaptive_certificate')
        if adaptive:
            refined = adaptive['reference_certificate']; components(refined, baseline)
            check.close(refined['raw_bound'], refined['e1_bound']+math.fsum(c['bound'] for c in refined['components']))
            for number, row in enumerate(adaptive['sites']):
                leaf(p, row['certificate'], context_for(p, row['satellite'], shortest),
                     dest/'adaptive'/f'site_{number}_pricing.mps')
            for row in refined['sites']:
                check.check_site(p, row, context_for(p, row['satellite'], shortest))
            lower = max(lower, refined['bound'])
            for number, row in enumerate(adaptive['masters']):
                cert = row['certificate']; frame = deepcopy(refined)
                for c in frame['components']:
                    c['bound'] = row['site_bounds'][c['satellite']]
                if cert['raw_bound'] is not None:
                    path = dest/'adaptive'/f'master_{number}.mps.gz'
                    inventory_guard(cert, path, models)
                    models.append(joint.check_joint_conjunction(p, last, frame, cert, path,
                                                               dest/'reference/e1_inventory.mps'))
                else:
                    check.close(cert['bound'], lower)
                lower = max(lower, cert['bound'])
            physical_pools(p, dest/'adaptive/primal', extra, models, counts)
            check.close(adaptive['bound'], lower)
        check.close(out['bound'], lower); check.close(out['upper_bound'], b['objective'])
        check.close(out['gap'], max(0, b['objective']-lower)/abs(b['objective']))
        assert out['target_reached'] == (out['gap'] <= payload['job']['options']['target_gap'])
        assert lower <= b['objective']+1e-6 and not out['proven_optimal'] and not out['proven_infeasible']
        summary = dict(upper=b['objective'], lower=lower, gap_percent=100*out['gap'], target_reached=out['target_reached'])
        proof_scope = 'NATIVE_PHYSICAL_AND_COMPLETE_DOMAIN_CERTIFICATES'
    frozen()
    assert payload['within_registered_budget'], 'Keep over-budget run as failure, no silent tolerance change'
    answer = dict(status='VERIFIED', proof_scope=proof_scope, summary=summary,
        pipeline_wall_seconds=payload['pipeline_wall_seconds'], counts=counts,
        models=models+joint.models, input_and_source_hashes_unchanged=True,
        validator_sha256=sha(Path(__file__)), result_sha256=sha(OUT/(name+'.json')),
        checked_at=datetime.now().astimezone().isoformat())
    write(OUT/(name+'_validation.json'), answer)
    print('VERIFIED', {k:v for k,v in answer.items() if k != 'models'}, flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('name', choices=['warm_selected_columns','warm_all_columns','native_seed50'])
    args = parser.parse_args()
    registration = OUT/'verification_plan.json'
    if registration.exists():
        assert read(registration)['validator_sha256'] == sha(Path(__file__))
    else:
        write(registration, dict(registered_before_verification=True, validator_sha256=sha(Path(__file__)),
            rules='Original full-domain matrix checks, all-column replays, independent physical and two-cycle audit, full-schedule MPS projections, raw-bound replay checks, fixed .5second reporting tolerance. Preserve failed original verification; no change to thresholds.',
            registered_at=datetime.now().astimezone().isoformat()))
    verify(args.name)
