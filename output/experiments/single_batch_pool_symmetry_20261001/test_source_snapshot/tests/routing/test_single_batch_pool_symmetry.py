"""Enumerate physical schedules and pin actual starts for pool symmetry."""
from copy import deepcopy
import itertools
import math
import time

import pytest

from models.routing.common.cycle import MultiBatchInstance, MultiBatchPolicy, Order
from models.routing.common.cycle.audit import audit_solution
from models.routing.common.cycle.state import empty_result
from models.routing.common.domain.instance import ProblemInstance
from models.routing.common.domain.node import Customer, Depot, Satellite
from models.routing.solvers.exact.single_batch_columns import DeliveryMaster, solve_with_route_bound
from models.routing.solvers.exact.single_batch_pool_master import DeliveryPoolMaster
from models.routing.solvers.exact.single_batch_route_pool import _physical_slots, route_pool_incumbent
from tests.routing.test_single_batch_route_pool import full_pool, raw_trip
from tests.routing.test_single_batch_solver_audit import tiny_problem


def four_tasks(scenario):
    policy = MultiBatchPolicy.from_config(dict(policy_version='multi_batch_cutoff_v1',
        cycle_start=0, cycle_end=65, batches=[dict(id='b1',delivery_duration=30,return_duration=30)],
        container_pool_size=4,cleaning_duration=5,time_tick_seconds=60,e2_max_trips_per_vehicle=4,
        e2_dispatch_prep_time=0 if scenario=='zero' else 2,
        e2_turnaround_time=0 if scenario=='zero' else 1))
    nodes = {'D':Depot('D',0,0),'S':Satellite('S',0,0)}
    orders = []
    for i in range(4):
        customer = f'C{i}'
        nodes[customer] = Customer(customer,0,0,0,0,(0,30),(0,30),'S')
        window = ((12,26) if i%2 else (0,8)) if scenario=='waiting' else (0,28)
        orders.append(Order(f'o{i}',customer,'S','b1',1,1,window,(0,30),
                            0 if scenario=='zero' else 2))
    ids = list(nodes)
    travel = {a:{b:0 if a==b or scenario=='zero' else 1+(ids.index(a)+2*ids.index(b))%4
                 for b in ids} for a in ids}
    distance = {a:{b:0 if a==b else 1+(2*ids.index(a)+ids.index(b))%7 for b in ids} for a in ids}
    fleet = dict(E1_vehicle_count=1,vehicles_per_satellite=2,E1_Qk=20,E1_Ck=20,
                 E2_Qv=2,E2_Cv=1,container_weight=1,container_volume=1)
    return MultiBatchInstance(ProblemInstance(nodes,fleet,distance_matrix=distance,
                             travel_time_matrix=travel),orders,policy,model=1)


def build(p, pool, compact, ordered, timing, slots):
    master = (DeliveryPoolMaster if compact else DeliveryMaster)(p,pool['domains'],pool['contexts'],pool['tours'])
    for site,column in pool['columns']:
        master.add(site,column)
    master.phase_two()
    for v in list(master.assignment.values())+list(master.active.values())+master.alpha+master.variables:
        v.VType,v.UB = 'B',1
    placement,departures,ends,used = _physical_slots(master,pool,time.perf_counter()+10,slots,
        vehicle_order=ordered,tight=timing,aggregate_time=timing)
    master.model.update()
    return master,placement


def raw_feasible(p, layout):
    # Original directed E1 supply and exhaustive INTEGER departures. No
    # production profile, normalization, symmetry or scheduling is consulted.
    policy = p.operating_policy
    supply = policy.batches[0].start+policy.e1_load_time+p.travel(p.depot,'S')+p.service('S')
    for row in layout:
        ready = policy.cycle_start
        for key in row:
            for depart in range(policy.cycle_start,policy.cycle_end+1):
                if depart-policy.e2_dispatch_prep_time < max(supply,ready):
                    continue
                end = raw_trip(p,'S',(key,),depart)
                if end is not None:
                    ready = end+policy.e2_turnaround_time
                    break
            else:
                return False
    return True


def two_vehicle_oracle(p):
    """Original route/owner enumeration with up to two parallel trips/site."""
    assert p.operating_policy.e2_max_trips_per_vehicle == 1 and p.fleet.e1_count == 1
    policy,batch,fleet = p.operating_policy,p.operating_policy.batches[0],p.fleet
    customers = sorted({o.customer for o in p.orders})
    choices = itertools.product(p.satellites,repeat=len(customers)) if p.free_assignment else [tuple(
        next(o.satellite for o in p.orders if o.customer==c) for c in customers)]
    best = math.inf
    for sites in choices:
        owner = dict(zip(customers,sites))
        active = sorted(set(sites))
        for tour in itertools.permutations(active):
            clock,previous,supply,e1cost = batch.start+policy.e1_load_time,p.depot,{},0
            for site in tour:
                clock += p.travel(previous,site)+p.service(site)
                e1cost += p.distance(previous,site)
                supply[site],previous = clock,site
            if clock+p.travel(previous,p.depot)+policy.depot_return_unload_time > min(batch.end,policy.cycle_end-policy.e1_turnaround_time):
                continue
            e1cost += p.distance(previous,p.depot)
            if (sum(o.goods_weight+o.count*fleet.container_weight for o in p.orders)>fleet.e1_weight_cap+1e-6 or
                sum(o.count for o in p.orders)*fleet.container_volume>fleet.e1_volume_cap+1e-6):
                continue
            total = e1cost
            for site in active:
                tasks = [o.id for o in p.orders if owner[o.customer]==site]
                least = math.inf
                for allocation in itertools.product(range(fleet.e2_per_sat_count),repeat=len(tasks)):
                    groups = [[key for key,v in zip(tasks,allocation) if v==vehicle]
                              for vehicle in range(fleet.e2_per_sat_count)]
                    for routes in itertools.product(*(itertools.permutations(row) for row in groups)):
                        cost,valid = 0,True
                        for route in routes:
                            if not route:
                                continue
                            orders = [p.order_map[key] for key in route]
                            if (sum(o.goods_weight+o.count*fleet.container_weight for o in orders)>fleet.e2_weight_cap+1e-6 or
                                sum(o.count for o in orders)*fleet.container_volume>fleet.e2_volume_cap+1e-6 or
                                raw_trip(p,site,route,max(policy.cycle_start,supply[site])+policy.e2_dispatch_prep_time) is None):
                                valid = False
                                break
                            path = (site,)+tuple(o.customer for o in orders)+(site,)
                            cost += sum(p.distance(a,b) for a,b in zip(path,path[1:]))
                        if valid:
                            least = min(least,cost)
                total += least
            best = min(best,total)
    return best


@pytest.mark.parametrize('scenario',['zero','directed','waiting'])
@pytest.mark.parametrize('slots',[2,4])
@pytest.mark.parametrize('compact',[False,True])
@pytest.mark.parametrize('timing',[False,True])
def test_every_labelled_schedule_keeps_one_feasible_vehicle_permutation(scenario,slots,compact,timing):
    gp = pytest.importorskip('gurobipy')
    p = four_tasks(scenario)
    pool = full_pool(p)
    assert len(pool['columns']) == 4  # Capacity enforces singleton trips.
    ids = [o.id for o in p.orders]
    ranks = {c['tasks'][0]:i for i,(_,c) in enumerate(pool['columns'])}
    masters = [build(p,pool,compact,ordered,timing,slots) for ordered in (False,True)]
    classes = {}
    try:
        for owners in itertools.product(range(2),repeat=4):
            rows = [[key for key,vehicle in zip(ids,owners) if vehicle==v] for v in range(2)]
            if max(map(len,rows)) > slots:
                continue
            for layout in itertools.product(*(itertools.permutations(row) for row in rows)):
                original_feasible = raw_feasible(p,layout)
                count_ordered = len(layout[0]) >= len(layout[1])
                id_ordered = (len(layout[0]) != len(layout[1]) or
                              ranks[layout[0][0]] < ranks[layout[1][0]])
                class_key = tuple(sorted(layout))
                entry = classes.setdefault(class_key,dict(raw=original_feasible,accepted=0))
                assert entry['raw'] == original_feasible
                for ordered,(master,placements) in enumerate(masters):
                    for (i,v,k),var in placements.items():
                        key = master.columns[i][1]['tasks'][0]
                        var.LB = var.UB = int(k<len(layout[v]) and layout[v][k]==key)
                    master.model.optimize()
                    accepted = master.model.Status == gp.GRB.OPTIMAL
                    assert accepted == (original_feasible and count_ordered and (not ordered or id_ordered)),layout
                    if ordered:
                        entry['accepted'] += accepted
        assert classes
        assert all(entry['accepted']==int(entry['raw']) for entry in classes.values())
    finally:
        for master,_ in masters:
            master.dispose()


@pytest.mark.parametrize('model',[1,2])
@pytest.mark.parametrize('seed',range(8))
@pytest.mark.parametrize('compact',[False,True])
@pytest.mark.parametrize('timing',[False,True])
def test_complete_tiny_pool_keeps_independent_original_optimum(model,seed,compact,timing):
    pytest.importorskip('gurobipy')
    p = tiny_problem(model,seed)
    p.fleet.e2_per_sat_count = 2
    oracle = two_vehicle_oracle(p)
    before = full_pool(p)
    immutable = deepcopy(before)
    result,stats = route_pool_incumbent(p,before,deadline=time.perf_counter()+2,
        compact=compact,tight=timing,aggregate_time=timing,vehicle_order=True)
    assert before == immutable
    assert stats['slot_bounds']['vehicle_ordering'] and not stats['global_proof']
    assert (result is not None) == math.isfinite(oracle)
    if result:
        assert result['info']['objective'] == pytest.approx(oracle)
        assert audit_solution(p,result)['valid'] and result['audit']['periodic_replay']['valid']
        assert not result['info']['proven_optimal'] and result['info']['best_bound'] is None


def reversed_equal_start(p):
    result = empty_result(p,'test_oracle')
    policy = p.operating_policy
    supply = policy.batches[0].start+policy.e1_load_time+p.travel(p.depot,'S')+p.service('S')
    result['e1_trips'] = [dict(trip_id='in',vehicle_id=0,batch_id='b1',departure=policy.e1_load_time,
        return_complete=supply+p.travel('S',p.depot)+policy.depot_return_unload_time,
        events=[dict(site_id='S',kind='d',quantity=4,start=supply-p.service('S'))])]
    # Equal counts, reversed first IDs; neither direction sorts by departure.
    for vehicle,keys in enumerate((('o2','o0'),('o1','o3'))):
        ready = policy.cycle_start
        for slot,key in enumerate(keys):
            depart = max(ready,supply)+policy.e2_dispatch_prep_time
            finish = raw_trip(p,'S',(key,),depart)
            assert finish is not None
            now = max(depart+p.travel('S',p.order_map[key].customer),p.order_map[key].delivery_window[0])
            result['e2_trips'].append(dict(trip_id=f'{vehicle}_{slot}',satellite_id='S',vehicle_id=vehicle,
                trip_index=slot,departure=depart,return_complete=finish,
                events=[dict(order_id=key,kind='d',quantity=1,start=now)]))
            ready = finish+policy.e2_turnaround_time
    audit = audit_solution(p,result)
    assert audit['valid']
    result.update(audit=audit)
    result['info'].update(status='FEASIBLE',objective=audit['objective'])
    return result


@pytest.mark.parametrize('compact',[False,True])
@pytest.mark.parametrize('timing',[False,True])
def test_actual_warm_start_relabels_tied_vehicles_and_preserves_all_times(monkeypatch,compact,timing):
    gp = pytest.importorskip('gurobipy')
    from models.routing.solvers.exact import single_batch_route_pool as implementation
    p = four_tasks('directed')
    initial = reversed_equal_start(p)
    before,captured,slots = deepcopy(initial),[],[]
    klass = DeliveryPoolMaster if compact else DeliveryMaster
    monkeypatch.setattr(klass,'dispose',lambda master:captured.append(master))
    original_slots = implementation._physical_slots
    def capture(*args,**kwargs):
        result = original_slots(*args,**kwargs)
        slots.append(result)
        return result
    monkeypatch.setattr(implementation,'_physical_slots',capture)
    try:
        _,stats = route_pool_incumbent(p,full_pool(p),initial_result=initial,deadline=time.perf_counter()+2,
            compact=compact,tight=timing,aggregate_time=timing,vehicle_order=True)
        assert initial == before and stats['start_vehicles_relabelled'] == 2
        master = captured[0]
        _,departures,ends,_ = slots[0]
        for trip in before['e2_trips']:
            key = ('S',1-trip['vehicle_id'],trip['trip_index'])
            assert departures[key].Start == trip['departure']
            assert ends[key].Start == trip['return_complete']
        for var in master.model.getVars():
            if abs(var.Start)<1e80:
                var.LB = var.UB = var.Start
        master.model.optimize()
        assert master.model.Status == gp.GRB.OPTIMAL
        starts = [t for t in initial['e2_trips'] if t['trip_index']==0]
        assert starts[0]['events'][0]['order_id']=='o2' and starts[1]['events'][0]['order_id']=='o1'
    finally:
        for master in captured:
            master.model.dispose()


def test_first_route_weighted_row_does_not_replace_original_fractional_count_order():
    pytest.importorskip('gurobipy')
    p = four_tasks('zero')
    master,placement = build(p,full_pool(p),True,True,False,2)
    try:
        # A fractionally more-used second vehicle can satisfy the new row by
        # choosing a larger first ID. Evaluate the ACTUAL generated linear
        # rows, rather than pinning fractional values in a binary MIP.
        weighted = master.model.getConstrByName('pool_vehicle_order_S_1')
        row = master.model.getRow(weighted)
        values = {v.index:0.0 for v in master.model.getVars()}
        for (i,v,k),var in placement.items():
            if k==0 and ((i==0 and v==0) or (i==3 and v==1)):
                values[var.index] = .5 if v==0 else .6
        count_variables = {}
        for j in range(row.size()):
            var,coef = row.getVar(j),row.getCoeff(j)
            if abs(coef)==5:  # Actual used variables carry count coefficient.
                count_variables[var.index] = coef
        assert len(count_variables)==4
        # First-slot usage must equal the sum of its route placements.
        # Used vars occur in one choice equality; identify that actual row.
        for constraint in master.model.getConstrs():
            expression = master.model.getRow(constraint)
            for v in (0,1):
                first_choices = {var.index for (i,a,k),var in placement.items() if a==v and k==0}
                vars_here = {expression.getVar(j).index for j in range(expression.size())}
                if first_choices < vars_here and len(vars_here)==len(first_choices)+1:
                    extra = (vars_here-first_choices).pop()
                    if extra in count_variables:
                        values[extra] = .5 if v==0 else .6
        def lhs(expression):
            return sum(expression.getCoeff(j)*values[expression.getVar(j).index] for j in range(expression.size()))
        assert lhs(row) <= weighted.RHS
        count_rows = []
        for constraint in master.model.getConstrs():
            expression = master.model.getRow(constraint)
            if {expression.getVar(j).index for j in range(expression.size())}==set(count_variables):
                if all(abs(expression.getCoeff(j))==1 for j in range(expression.size())):
                    count_rows.append(constraint)
        assert len(count_rows)==1
        assert lhs(master.model.getRow(count_rows[0])) > count_rows[0].RHS
    finally:
        master.dispose()


@pytest.mark.parametrize('model',[1,2])
def test_hybrid_global_proof_still_comes_from_complete_kernel(model):
    pytest.importorskip('gurobipy')
    p = tiny_problem(model,1)
    p.fleet.e2_per_sat_count = 2
    result = solve_with_route_bound(p,time_limit=3,root_time=1,pool_time=1,
        pricing_method='mip',pool_compact=True,pool_vehicle_order=True)
    assert result['info']['proven_optimal'] and result['info']['objective']==pytest.approx(two_vehicle_oracle(p))
    assert result['audit']['valid'] and result['audit']['periodic_replay']['valid']
    assert not result['info']['route_pool_primal']['global_proof']
    assert result['info']['route_pool_primal']['slot_bounds']['vehicle_ordering']
    assert '_route_pool' not in result['info']['route_bound_certificate']


@pytest.mark.parametrize('model',[3,4])
def test_delivery_vehicle_order_cannot_drop_pickups(model):
    with pytest.raises(ValueError,match='M1/M2'):
        route_pool_incumbent(tiny_problem(model,1),{},deadline=time.perf_counter()+1,vehicle_order=True)
