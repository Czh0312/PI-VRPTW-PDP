"""Replay M01 evidence against the original full problem after the timed run."""
import math

import gurobipy as gp
import pytest

from study import HERE, CASE, read, save, sha, load_instance, load_routing_case
from models.common.cycle.audit import audit_solution
from models.cost.comparison import evaluate_comparison_cost
from experiments.runs.model8_stock_relaxation import build_stock_bound


def problem():
    return load_instance(*load_routing_case(CASE, use_pool_config=False))


def test_frozen_source_inputs_seed_and_timing():
    manifest = read(HERE/'manifest.json')
    row = read(HERE/'summary.json')
    assert all(sha(path) == value for path, value in manifest['source_and_input_hashes'].items())
    assert row['sources_and_inputs_unchanged']
    assert manifest['case'] == row['case'] == 'M01'
    assert manifest['seed'] == row['seed'] == 42
    assert manifest['threads'] == row['threads'] == 1
    assert manifest['budget_seconds'] == row['budget_seconds'] == 1200
    assert row['total_wall_seconds'] >= row['native_runtime']+row['build_seconds']+row['frontier_seconds']
    assert row['total_wall_seconds'] < row['budget_seconds']+10
    params = read(HERE/'solver_parameters.json')
    assert params['MIPGap'] == params['MIPGapAbs'] == 0
    assert params['Seed'] == 42 and params['Threads'] == 1


def test_full_competitive_domain_and_original_physical_slots():
    p = problem()
    row = read(HERE/'summary.json')
    cert = read(HERE/'transport_certificate_initial.json')
    final = read(HERE/'transport_certificate.json')
    assert len(p.orders) == 20
    assert row['original_K'] == list(p.batch_count_candidates) == list(range(1, 21))
    assert cert['retained_K'] == [2, 3, 4, 5]
    assert final['retained_K'] == row['retained_K'] == [2, 3, 4]
    assert all(item['lower_bound'] > cert['audited_upper']+1e-6 for item in cert['excluded'].values())
    assert row['variables'] == 24151 and row['constraints'] == 23085
    size = read(HERE/'model_size.json')
    expected = sum(p.fleet.e2_per_sat_count*min(p.operating_policy.e2_max_trips_per_vehicle,
        2*sum(o.satellite == s for o in p.orders)) for s in p.satellites)
    assert size['physical_E2_slots'] == expected == 72
    assert not row['full_route_library_used'] and row['mip_start_supplied']
    assert not read(HERE/'mip_start.json')['fixes_decisions']
    for k in row['retained_K']:
        child = p.for_count(k).operating_policy
        assert (child.time_tick_seconds, child.time_tick_subdivision) == (
            p.operating_policy.time_tick_seconds, p.operating_policy.time_tick_subdivision)


def test_replay_all_stock_frontier_exclusion_and_retention_proofs():
    p = problem()
    cert = read(HERE/'transport_certificate_initial.json')
    frontier = read(HERE/'stock_frontier.json')
    expected = {(k, n) for k in cert['retained_K']
        for n in range(max(cert['pool_lower'], math.ceil(p.total/k)),
                       cert['per_K_pool_upper'][str(k)]+1)}
    assert {(r['K'], r['N']) for r in frontier['pairs']} == expected
    m, ctx = build_stock_bound(p, cert)
    replay = []
    try:
        m.Params.TimeLimit = 5
        for recorded in frontier['pairs']:
            k, n = recorded['K'], recorded['N']
            for count, var in ctx['selectors'].items():
                var.LB = var.UB = int(count == k)
            ctx['pool'].LB = ctx['pool'].UB = n
            m.optimize()
            if recorded['proven_infeasible']:
                assert m.Status == gp.GRB.INFEASIBLE
            else:
                assert m.Status == gp.GRB.OPTIMAL
                assert m.ObjVal == pytest.approx(recorded['lower_bound'], abs=1e-5)
                assert recorded['cost_dominated'] == (m.ObjBound > cert['audited_upper']+1e-6)
            replay.append(dict(K=k, N=n, native_status=int(m.Status),
                native_bound=m.ObjBound if math.isfinite(m.ObjBound) and abs(m.ObjBound) < 1e90 else None,
                seconds=m.Runtime, seed=42))
    finally:
        m.dispose()
    retained = [r for r in frontier['pairs'] if not r['proven_infeasible'] and not r['cost_dominated']]
    assert retained == frontier['retained_pairs'] and len(retained) == 8
    assert frontier['global_lower_bound'] == min(r['lower_bound'] for r in retained)
    save('independent_frontier_replay.json', replay)


@pytest.mark.parametrize('site', ['S5', 'S7'])
def test_metric_tour_lower_bound_against_separate_TSP_formulation(site):
    p = problem()
    cert = read(HERE/'transport_certificate_initial.json')
    nodes = [site]+sorted({o.customer for o in p.orders if o.satellite == site})
    n = len(nodes)-1
    m = gp.Model()
    try:
        m.Params.OutputFlag = 0
        m.Params.Threads = 1
        m.Params.Seed = 42
        m.Params.TimeLimit = 20
        x = m.addVars([(i, j) for i in range(n+1) for j in range(n+1) if i != j], vtype=gp.GRB.BINARY)
        rank = m.addVars(range(1, n+1), lb=1, ub=n)
        for i in range(n+1):
            m.addConstr(gp.quicksum(x[i, j] for j in range(n+1) if i != j) == 1)
            m.addConstr(gp.quicksum(x[j, i] for j in range(n+1) if i != j) == 1)
        for i in range(1, n+1):
            for j in range(1, n+1):
                if i != j:
                    m.addConstr(rank[j] >= rank[i]+1-n*(1-x[i, j]))
        m.setObjective(gp.quicksum(p.distance(nodes[i], nodes[j])*var for (i, j), var in x.items()))
        m.optimize()
        assert m.Status == gp.GRB.OPTIMAL
        assert m.ObjVal == pytest.approx(cert['site_TSP_lengths'][site], abs=1e-8)
        save(f'independent_tsp_{site}.json', dict(status=m.Status, distance=m.ObjVal, seconds=m.Runtime, seed=42))
    finally:
        m.dispose()


def test_final_physical_plan_cost_and_two_cycle_replay():
    p = problem()
    result = read(HERE/'final_best_audited_result.json')
    row = read(HERE/'summary_reviewed.json')
    audit = audit_solution(p, result)
    assert audit['valid'] and audit['periodic_replay']['valid']
    cost = evaluate_comparison_cost(p, result, p.cost_profile.config, audit=audit)['total']
    assert cost == pytest.approx(row['audited_upper_bound'], abs=1e-6)
    assert cost <= row['initial_audited_cost']+1e-6
    assert row['global_lower_bound'] <= cost+1e-6
    assert row['gap_over_upper'] == pytest.approx((cost-row['global_lower_bound'])/cost, abs=1e-10)
    assert row['proven_optimal'] == (cost-row['global_lower_bound'] <= 1e-6)
    assert result['cycle_batch_count'] in p.batch_count_candidates
    save('independent_final_audit.json', audit)


def test_original_audited_witness_projects_to_stock_bound():
    p = problem()
    result = read(HERE/'initial_result.json')
    audit = audit_solution(p, result)
    cert = read(HERE/'transport_certificate_initial.json')
    m, ctx = build_stock_bound(p, cert)
    try:
        k = result['cycle_batch_count']
        first = {}
        for snapshot in audit['cutoff_snapshots']:
            b = int(snapshot['batch_id'][1:])-1
            for trip in snapshot['eligible_events']:
                first[trip] = min(first.get(trip, k), b)
        recovery = {event['order_id']:first[trip['trip_id']] for trip in result['e2_trips']
                    for event in trip['events'] if event['kind'] == 'p'}
        for count, var in ctx['selectors'].items():
            var.LB = var.UB = int(count == k)
        ctx['pool'].LB = ctx['pool'].UB = result['container_pool_size']
        for i, order in enumerate(p.orders):
            d = int(result['order_batch_assignment'][order.id][1:])-1
            for b in range(max(cert['retained_K'])):
                ctx['assignments'][i, b].LB = ctx['assignments'][i, b].UB = int(b == d)
                ctx['recovery'][i, b].LB = ctx['recovery'][i, b].UB = int(b == recovery[order.id])
        for b, var in ctx['boundaries'].items():
            var.LB = var.UB = result['batch_boundaries'][b] if b <= k else p.operating_policy.cycle_end
        for b, var in ctx['cutoffs'].items():
            var.LB = var.UB = result['batch_cutoffs'][f'b{b+1}'] if b < k else p.operating_policy.cycle_end
        m.Params.TimeLimit = 20
        m.optimize()
        assert m.Status == gp.GRB.OPTIMAL and m.ObjVal <= audit['objective']+1e-6
        save('independent_stock_projection.json', dict(status=m.Status, lower_cost=m.ObjVal,
             original_audited_cost=audit['objective'], seconds=m.Runtime, seed=42))
    finally:
        m.dispose()


def test_auxiliary_search_is_upper_bound_only_and_does_not_change_native_log():
    native = read(HERE/'summary.json')
    reviewed = read(HERE/'summary_reviewed.json')
    heuristic = read(HERE/'heuristic_summary.json')
    assert heuristic['seed'] == 42 and heuristic['case'] == 'M01'
    assert not heuristic['used_for_global_lower_bound'] and not heuristic['proves_optimality']
    assert all(row['seed'] == 42 for row in heuristic['searches'])
    assert reviewed['native_lower_bound'] == native['native_lower_bound']
    assert reviewed['native_objective'] == native['native_objective']
    assert reviewed['native_status'] == native['native_status']
    assert reviewed['audited_upper_bound'] <= native['audited_upper_bound']+1e-6


def test_parallel_exact_search_bound_scope_seed_and_audits():
    parallel = read(HERE/'parallel_summary.json')
    reviewed = read(HERE/'summary_reviewed.json')
    native = read(HERE/'summary.json')
    assert parallel['case'] == 'M01' and parallel['seed'] == 42
    assert parallel['threads'] == parallel['parameters']['Threads'] == 8
    assert parallel['parameters']['MIPGap'] == parallel['parameters']['MIPGapAbs'] == 0
    assert parallel['kind'] == 'COMPLETE_ORIGINAL_DOMAIN_SEARCH'
    assert parallel['reused_certificates_computed_in_same_run']
    assert parallel['business_audit_valid'] and parallel['periodic_replay_valid']
    assert parallel['ended_at_outer_seconds'] < 1200
    assert parallel['script_sha256'] == sha(HERE/'parallel_bound_search.py')
    assert reviewed['global_lower_bound'] == max(native['global_lower_bound'], parallel['global_lower_bound'])
    assert parallel['global_lower_bound'] <= reviewed['audited_upper_bound']+1e-6
