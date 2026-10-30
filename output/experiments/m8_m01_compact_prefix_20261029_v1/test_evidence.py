"""Original-domain audits and independent M01 lower-bound certificates."""
import math

import gurobipy as gp
import pytest

from study import HERE,CASE,read,save,sha,load_instance,load_routing_case
from models.common.cycle.audit import audit_solution
from models.cost.comparison import evaluate_comparison_cost
from experiments.runs.model8_stock_relaxation import build_stock_bound


def problem():
    return load_instance(*load_routing_case(CASE,use_pool_config=False))


def test_original_scope_and_frozen_complete_model():
    p = problem()
    manifest = read(HERE/'manifest.json')
    row = read(HERE/'summary.json')
    assert all(sha(path)==value for path,value in manifest['source_and_input_hashes'].items())
    assert row['sources_and_inputs_unchanged']
    assert len(p.orders) == row['customers'] == 20
    assert list(p.batch_count_candidates) == row['original_K'] == list(range(1,21))
    assert row['retained_K'] == [2,3,4,5]
    assert row['variables'] == 24459 and row['constraints'] == 24510
    assert not row['full_route_library_used'] and row['mip_start_supplied']
    assert row['seed'] == row['threads']*42 == 42
    assert row['total_wall_seconds'] >= row['native_runtime']+row['build_seconds']
    assert row['status'] == 'TIME_LIMIT' and not row['proven_optimal']


@pytest.mark.parametrize('site',['S5','S7'])
def test_metric_tour_certificate_against_independent_exact_TSP(site):
    p = problem()
    cert = read(HERE/'transport_certificate.json')
    customers = sorted({o.customer for o in p.orders if o.satellite == site})
    nodes = [site]+customers
    n = len(customers)
    m = gp.Model()
    try:
        m.Params.OutputFlag = 0
        m.Params.Threads = 1
        m.Params.Seed = 42
        m.Params.TimeLimit = 20
        x = m.addVars([(i,j) for i in range(n+1) for j in range(n+1) if i != j],vtype=gp.GRB.BINARY)
        rank = m.addVars(range(1,n+1),lb=1,ub=n)
        for i in range(n+1):
            m.addConstr(gp.quicksum(x[i,j] for j in range(n+1) if i != j) == 1)
            m.addConstr(gp.quicksum(x[j,i] for j in range(n+1) if i != j) == 1)
        for i in range(1,n+1):
            for j in range(1,n+1):
                if i != j:
                    m.addConstr(rank[j] >= rank[i]+1-n*(1-x[i,j]))
        m.setObjective(gp.quicksum(p.distance(nodes[i],nodes[j])*v for (i,j),v in x.items()))
        m.optimize()
        assert m.Status == gp.GRB.OPTIMAL
        # This data is metric; using original distances rather than the
        # certificate's shortest-path closure independently reproduces it.
        assert cert['site_TSP_lengths'][site] == pytest.approx(m.ObjVal,abs=1e-8)
        save(f'independent_tsp_{site}.json',dict(status=m.Status,distance=m.ObjVal,seconds=m.Runtime,seed=42))
    finally:
        m.dispose()


def test_final_original_business_cost_and_two_cycle_replay():
    p = problem()
    result = read(HERE/'best_audited_result.json')
    row = read(HERE/'summary.json')
    audit = audit_solution(p,result)
    assert audit['valid'] and audit['periodic_replay']['valid']
    cost = evaluate_comparison_cost(p,result,p.cost_profile.config,audit=audit)['total']
    assert cost == pytest.approx(row['audited_upper_bound'],abs=1e-6)
    assert cost == pytest.approx(row['initial_audited_cost'],abs=1e-6)
    assert row['native_lower_bound'] <= cost+1e-6
    assert result['cycle_batch_count'] == 2 and result['container_pool_size'] == 14
    save('independent_final_audit.json',audit)


def test_original_witness_projects_to_auxiliary_stock_relaxation():
    p = problem()
    result = read(HERE/'initial_result.json')
    audit = audit_solution(p,result)
    assert audit['valid'] and audit['periodic_replay']['valid']
    cert = read(HERE/'transport_certificate.json')
    m,ctx = build_stock_bound(p,cert)
    try:
        k = result['cycle_batch_count']
        first = {}
        for snapshot in audit['cutoff_snapshots']:
            b = int(snapshot['batch_id'][1:])-1
            for trip in snapshot['eligible_events']:
                first[trip] = min(first.get(trip,k),b)
        recovery = {event['order_id']:first[trip['trip_id']] for trip in result['e2_trips']
                    for event in trip['events'] if event['kind'] == 'p'}
        for count,variable in ctx['selectors'].items():
            variable.LB = variable.UB = int(count == k)
        ctx['pool'].LB = ctx['pool'].UB = result['container_pool_size']
        for i,o in enumerate(p.orders):
            d = int(result['order_batch_assignment'][o.id][1:])-1
            for b in range(max(cert['retained_K'])):
                ctx['assignments'][i,b].LB = ctx['assignments'][i,b].UB = int(b == d)
                ctx['recovery'][i,b].LB = ctx['recovery'][i,b].UB = int(b == recovery[o.id])
        for b,variable in ctx['boundaries'].items():
            value = result['batch_boundaries'][b] if b <= k else p.operating_policy.cycle_end
            variable.LB = variable.UB = value
        for b,variable in ctx['cutoffs'].items():
            value = result['batch_cutoffs'][f'b{b+1}'] if b < k else p.operating_policy.cycle_end
            variable.LB = variable.UB = value
        m.Params.TimeLimit = 20
        m.optimize()
        assert m.Status == gp.GRB.OPTIMAL
        assert m.ObjVal <= audit['objective']+1e-6
        save('independent_stock_projection.json',dict(status=m.Status,lower_cost=m.ObjVal,
            original_audited_cost=audit['objective'],seconds=m.Runtime,seed=42))
    finally:
        m.dispose()


def test_combined_global_bound_and_no_false_construction_claim():
    row = read(HERE/'summary.json')
    aux = read(HERE/'auxiliary_stock_bound.json')
    cert = read(HERE/'transport_certificate.json')
    construction = read(HERE/'construction_summary.json')
    assert aux['native_status'] == gp.GRB.OPTIMAL
    assert aux['global_lower_bound'] <= row['audited_upper_bound']+1e-6
    assert all(value['lower_bound'] > cert['audited_upper']+1e-6 for value in cert['excluded'].values())
    assert not construction['used_for_global_lower_bound'] and not construction['used_for_infeasibility_cut']
    assert construction['sol_count'] == 0 and construction['native_status'] == gp.GRB.TIME_LIMIT
    lower = max(row['global_lower_bound'],aux['global_lower_bound'])
    assert lower < row['audited_upper_bound']-1e-6
