"""Independent audits, fresh certificate solves, and original-slot cut checks."""
import json

import gurobipy as gp
import pytest

from study import (HERE, ROOT, SYNC, PHASE_PATH, CASES, TOL, load, read, save, module,
                   verify, enumerate_trip_library, build_relaxation, add_trip_sync)
from models.common.cycle.audit import audit_solution
from models.cost.comparison import evaluate_comparison_cost


def folder(case):
    return HERE / 'runs' / case / 's42'


def master(p, library):
    model, ctx = build_relaxation(p, library)
    module(PHASE_PATH, 'validation_phase').augment(p, model, ctx)
    add_trip_sync(p, model, ctx, library)
    model.Params.Seed = 101
    model.Params.Threads = 1
    model.Params.TimeLimit = 60
    return model, ctx


def projection(p, result):
    audit = audit_solution(p, result)
    assert audit['valid'] and audit['periodic_replay']['valid']
    child = p.for_count(result['cycle_batch_count'])
    batches = [b.id for b in child.operating_policy.batches]
    position = {o.id: i for i, o in enumerate(p.orders)}
    first = {}
    for snapshot in audit['cutoff_snapshots']:
        for trip_id in snapshot['eligible_events']:
            first[trip_id] = min(first.get(trip_id, len(batches)), batches.index(snapshot['batch_id']))
    masks, recoveries = [], {}
    for trip in result['e2_trips']:
        mask = 0
        for event in trip['events']:
            bit = position[event['order_id']] + (len(position) if event['kind'] == 'p' else 0)
            mask |= 1 << bit
            if event['kind'] == 'p':
                recoveries[event['order_id']] = first[trip['trip_id']]
        masks.append(mask)
    return dict(K=len(batches), masks=masks, recovery=recoveries,
                delivery={oid: batches.index(bid) for oid, bid in result['order_batch_assignment'].items()})


def test_complete_registered_scope_and_frozen_information():
    plan = read(HERE / 'plan.json')
    verify(plan)
    assert [r['case'] for r in plan['cases']] == CASES
    assert plan['seeds'] == [42] and plan['threads'] == 1
    assert plan['concurrent_solver_processes'] == 1
    assert plan['historical_feasible_plan_used']
    assert not plan['historical_lower_bound_used']
    assert not plan['historical_domain_or_route_library_used']
    execution = read(HERE / 'execution.json')
    assert not execution['failures'] and len(execution['rows']) == 9
    assert sorted(p.parent.parent.name for p in (HERE / 'runs').rglob('summary.json')) == CASES


@pytest.mark.parametrize('case', CASES)
def test_original_business_cost_and_two_cycle_audit(case):
    p = load(case)
    row = read(folder(case) / 'summary.json')
    result = read(folder(case) / 'best_audited_result.json')
    audit = audit_solution(p, result)
    assert audit['valid'] and audit['periodic_replay']['valid']
    cost = evaluate_comparison_cost(p, result, p.cost_profile.config, audit=audit)['total']
    assert cost == pytest.approx(row['audited_upper_bound'], abs=TOL)
    assert row['global_lower_bound'] <= cost + TOL
    assert row['proven_optimal'] and abs(cost - row['global_lower_bound']) <= TOL
    assert row['original_K'] == list(range(1, 8)) == list(p.batch_count_candidates)
    assert row['historical_feasible_plan_used'] and not row['historical_lower_bound_used']
    assert not row['historical_library_or_domain_used']
    assert row['total_wall_seconds'] >= row['enumeration_seconds'] + row['input_only_master_seconds']
    assert not row['cold_start_end_to_end']


@pytest.mark.parametrize('case', CASES)
def test_fresh_full_library_and_feasible_original_projection(case):
    p = load(case)
    library, _ = enumerate_trip_library(p)
    # JSON encodes the enumerator's path tuples as arrays; compare the same
    # representation while preserving all masks, paths and numeric distances.
    assert json.loads(json.dumps(library)) == read(folder(case) / 'trip_library.json')
    result = read(folder(case) / 'best_audited_result.json')
    actual = projection(p, result)
    assert set(actual['masks']) <= {r['mask'] for r in library}
    model, ctx = master(p, library)
    try:
        ctx['pool'].LB = ctx['pool'].UB = result['container_pool_size']
        for k, variable in ctx['selectors'].items():
            variable.LB = variable.UB = int(k == actual['K'])
        for i, order in enumerate(p.orders):
            for b in range(max(ctx['selectors'])):
                for key, kind in [('assignments', 'delivery'), ('recovery', 'recovery')]:
                    variable = ctx[key][i, b]
                    variable.LB = variable.UB = int(b == actual[kind][order.id])
        for c, col in enumerate(library):
            variable = ctx['route'][c]
            variable.LB = variable.UB = int(col['mask'] in actual['masks'])
        model.addConstr(model.getObjective() <= audit_solution(p, result)['objective'] + 1e-7)
        model.optimize()
        assert model.Status == gp.GRB.OPTIMAL
        save(folder(case) / 'independent_projection.json', dict(native_status=model.Status,
             cost_lower_bound=model.ObjBound, seed=101, seconds=model.Runtime))
    finally:
        model.dispose()


@pytest.mark.parametrize('case', CASES)
def test_fresh_bound_certificate_replay_without_known_optimum_floor(case):
    p = load(case)
    library = read(folder(case) / 'trip_library.json')
    row = read(folder(case) / 'summary.json')
    raw = read(folder(case) / 'input_only_bound.json')
    assert not raw['prior_U_or_L_supplied'] and not raw['K_or_N_fixed']
    model, ctx = master(p, library)
    try:
        algorithm_path = folder(case) / 'algorithm_summary.json'
        if algorithm_path.exists():
            info = read(algorithm_path)
            domain = read(folder(case) / 'reduction_certificate.json')
            eta = model.addVar(lb=raw['lower_bound'], name='validation_cost_proxy')
            model.addConstr(eta >= model.getObjective())
            model.setObjective(eta)
            ctx['pool'].LB = ctx['pool'].UB = domain['N_min']
            assert domain['N_min'] == domain['N_max']
            for k, variable in ctx['selectors'].items():
                if k not in domain['retained_K']:
                    variable.UB = 0
            lookup = {r['mask']: i for i, r in enumerate(library)}
            for event in info['events']:
                chosen = [ctx['selectors'][event['K']]]
                chosen += [ctx['assignments'][i, event['delivery_batches'][o.id]] for i, o in enumerate(p.orders)]
                chosen += [ctx['recovery'][i, event['recovery_batches'][o.id]] for i, o in enumerate(p.orders)]
                chosen += [ctx['route'][lookup[mask]] for mask in event['masks']]
                mismatch = gp.quicksum(1-variable for variable in chosen)
                if event['cut_kind'] == 'PROVEN_NO_SOLUTION_UNDER_INITIAL_COST_CAP':
                    assert event['native_status'] == gp.GRB.INFEASIBLE
                    model.addConstr(mismatch >= 1)
                elif event['cut_kind'] == 'VALID_CONDITIONAL_LOWER_BOUND':
                    assert event['gamma'] <= event['native_bound'] + TOL
                    assert event['bigM'] >= event['gamma'] - raw['lower_bound'] - TOL
                    model.addConstr(eta >= event['gamma'] - 1e-7 - event['bigM'] * mismatch)
                else:
                    pytest.fail(f'Unrecognized cut: {event}')
        # No upper bound on the objective, and no known optimum supplied as LB.
        model.optimize()
        assert model.Status == gp.GRB.OPTIMAL
        assert abs(model.ObjBound - row['audited_upper_bound']) <= TOL
        save(folder(case) / 'independent_bound_replay.json', dict(native_status=model.Status,
             bound=model.ObjBound, seed=101, seconds=model.Runtime, known_optimum_floor_used=False,
             cost_cap_on_replayed_objective=False, original_K=list(p.batch_count_candidates)))
    finally:
        model.dispose()


@pytest.mark.parametrize('case', ['S03', 'S07'])
def test_fresh_certified_domain_endpoints(case):
    p = load(case)
    library = read(folder(case) / 'trip_library.json')
    domain = read(folder(case) / 'reduction_certificate.json')
    row = read(folder(case) / 'summary.json')
    model, ctx = master(p, library)
    try:
        model.optimize()
        assert model.Status == gp.GRB.OPTIMAL
        assert model.ObjBound == pytest.approx(domain['global_lower_bound'], abs=TOL)
        model.addConstr(model.getObjective() <= row['initial_U'] + 1e-7)
        count = gp.quicksum(k*v for k, v in ctx['selectors'].items())
        endpoints = {}
        for name, expression, sign in [('K_min', count, 1), ('K_max', count, -1),
                                       ('N_min', ctx['pool'], 1), ('N_max', ctx['pool'], -1)]:
            model.setObjective(sign * expression)
            model.optimize()
            assert model.Status == gp.GRB.OPTIMAL
            endpoints[name] = round(sign * model.ObjVal)
            assert endpoints[name] == domain[name]
        save(folder(case) / 'independent_domain_replay.json', dict(seed=101, endpoints=endpoints))
    finally:
        model.dispose()


def test_each_new_cut_with_original_untrimmed_full_model():
    from models.routing.model_8.exact.formulation import build_model
    from route_bounds import add_shared_route_bounds
    replays = []
    for case in ['S03', 'S07']:
        p = load(case)
        domain = read(folder(case) / 'reduction_certificate.json')
        row = read(folder(case) / 'summary.json')
        library = read(folder(case) / 'trip_library.json')
        events = read(folder(case) / 'algorithm_summary.json')['events']
        unique = {}
        for event in events:
            key = (event['K'], tuple(sorted(event['delivery_batches'].items())),
                   tuple(sorted(event['recovery_batches'].items())), tuple(event['masks']))
            old = unique.get(key)
            if old is None or event.get('cut_kind') == 'PROVEN_NO_SOLUTION_UNDER_INITIAL_COST_CAP':
                unique[key] = event
        for event in unique.values():
            child = p.for_count(event['K'])
            assert child.operating_policy.e2_max_trips_per_vehicle == p.operating_policy.e2_max_trips_per_vehicle
            model, sub = build_model(child, compact_e2=True, strengthening='physical_time')
            try:
                model.Params.OutputFlag = 0
                model.Params.Threads = 1
                model.Params.Seed = 101
                model.Params.TimeLimit = 60
                model.Params.MIPGap = 0
                model.Params.MIPGapAbs = 0
                sub['pool'].LB = sub['pool'].UB = domain['N_min']
                batches = [b.id for b in child.operating_policy.batches]
                for (oid, bid), variable in sub['assignments'].items():
                    variable.LB = variable.UB = int(bid == batches[event['delivery_batches'][oid]])
                for oid, assigned in event['delivery_batches'].items():
                    if (oid, batches[assigned]) not in sub['assignments']:
                        model.addConstr(gp.LinExpr() == 1)
                model.update()
                for oid, b in event['recovery_batches'].items():
                    for i, bid in enumerate(batches):
                        variable = model.getVarByName(f'eligible_{oid}_{bid}')
                        assert variable is not None
                        variable.LB = variable.UB = int(i >= b)
                add_shared_route_bounds(child, model, sub, library)
                for c, col in enumerate(library):
                    variable = model.getVarByName(f'bound_shared_mask[{c}]')
                    variable.LB = variable.UB = int(col['mask'] in event['masks'])
                model.addConstr(model.getObjective() <= row['initial_U'] + 1e-7)
                model.addConstr(model.getObjective() >= domain['global_lower_bound'] - 1e-7)
                model.optimize()
                if event['cut_kind'] == 'PROVEN_NO_SOLUTION_UNDER_INITIAL_COST_CAP':
                    assert model.Status == gp.GRB.INFEASIBLE
                else:
                    assert model.ObjBound >= event['gamma'] - TOL
                replays.append(dict(case=case, K=event['K'], masks=event['masks'], native_status=model.Status,
                                    native_bound=model.ObjBound if abs(model.ObjBound) < 1e90 else None,
                                    seconds=model.Runtime, original_slot_cap=child.operating_policy.e2_max_trips_per_vehicle))
            finally:
                model.dispose()
    save(HERE / 'original_slot_cut_replay.json', dict(unique_pattern_count=len(replays), replays=replays))
