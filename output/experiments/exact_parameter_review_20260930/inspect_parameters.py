"""Read-only model/data diagnosis; writes only into this audit directory."""
from pathlib import Path
import collections
import hashlib
import json
import math
import sys
import time

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
OUT = Path(__file__).resolve().parent
from configuration.loader import load_routing_case
from models.routing.common.cycle import load_instance, MultiBatchInstance
from models.routing.common.cycle.precheck import travel_lower_bounds
from models.routing.solvers.exact.domains import assignment_domains
from models.routing.solvers.exact.formulation import build_model


def save(name, data):
    (OUT/name).write_text(json.dumps(data, indent=2, ensure_ascii=False)+'\n')


def hashes():
    paths = list((ROOT/'models/routing/solvers/exact').glob('*.py'))
    paths += list((ROOT/'models/routing/common/cycle').glob('*.py'))
    paths += [ROOT/'data/manifest.json', ROOT/'config/routing/container_pools.json']
    return {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}


def scan():
    manifest = json.loads((ROOT/'data/manifest.json').read_text())
    rows = []
    for record in manifest['cases']:
        if record['batch_count'] != 1:
            continue
        cfg, fleet = load_routing_case(record['case_path'])
        cfg['model'] = 4
        p = load_instance(cfg, fleet)
        policy, f = p.operating_policy, p.fleet
        travel = travel_lower_bounds(p)
        check = assignment_domains(p, travel)
        pure = MultiBatchInstance(p.network, p.orders, policy, model=2)
        pure_check = assignment_domains(pure, travel)
        estimates = {}
        for number in (1, 2, 3, 4):
            kinds = 2 if number in (3, 4) else 1
            applicable = pure_check if number == 2 else check
            tasks = [sum(s in applicable['domains'][o.customer] if number in (2, 4)
                         else o.satellite == s for o in p.orders)*kinds for s in p.satellites]
            slots = [min(f.e2_per_sat_count, t)*min(policy.e2_max_trips_per_vehicle, t) for t in tasks]
            estimate = sum((t*(t-1)+2*t)*copies for t, copies in zip(tasks, slots))
            estimates[str(number)] = dict(estimated_arcs=estimate, route_copies=sum(slots),
                tasks=len(p.orders)*kinds,
                strategy=('joint' if estimate <= 60000 else 'enumeration') if number in (2, 4) else 'fixed')
        e1_bounds = []
        for s in p.satellites:
            orders = [o for o in p.orders if o.satellite == s]
            if not orders:
                continue
            earliest = policy.batches[0].start+policy.e1_load_time+travel['outward'][p.depot][s]
            latest = min(min(o.delivery_window[1],policy.batches[0].cutoff-o.service_time)
                         -policy.e2_dispatch_prep_time-travel['outward'][s][o.customer]-p.service(s)
                         for o in orders)
            e1_bounds.append(dict(satellite=s,
                current_window=[policy.batches[0].start,policy.batches[0].cutoff-p.service(s)],
                safe_window=[earliest,latest]))
        rows.append(dict(case=record['case_id'],path=record['case_path'],source=record['source_kind'],
            customers=record['customer_count'],satellites=len(p.satellites),
            e1_count=f.e1_count,e2_per_satellite=f.e2_per_sat_count,
            max_trips=policy.e2_max_trips_per_vehicle,tick_seconds=policy.time_tick_seconds,
            start=policy.cycle_start,end=policy.cycle_end,
            delivery_window_minutes=[policy.minutes(o.delivery_window[1]-o.delivery_window[0]) for o in p.orders],
            pickup_window_minutes=[policy.minutes(o.pickup_window[1]-o.pickup_window[0]) for o in p.orders],
            e1_capacity=[f.e1_weight_cap,f.e1_volume_cap],e2_capacity=[f.e2_weight_cap,f.e2_volume_cap],
            e2_weight_over_volume_implied_cap=(f.e2_weight_cap/(f.e2_volume_cap*max(
                (o.goods_weight+o.count*f.container_weight)/(o.count*f.container_volume) for o in p.orders))
                if p.orders and f.container_volume else None),
            pool=policy.container_pool_size,demand=p.total,
            domain_sizes=dict(collections.Counter(map(len,check['domains'].values()))),
            domain_assignment_count=math.prod(map(len,check['domains'].values())),
            domain_assignment_count_M2=math.prod(map(len,pure_check['domains'].values())),
            precheck_passed=check['passed'],estimates=estimates,e1_bounds=e1_bounds))
    save('parameter_inventory.json', rows)
    groups=[]
    for n in sorted({r['customers'] for r in rows if r['source']=='zhou_derived_scenario'}):
        subset=[r for r in rows if r['source']=='zhou_derived_scenario' and r['customers']==n]
        groups.append(dict(customers=n,cases=len(subset),
            M2_strategies=dict(collections.Counter(r['estimates']['2']['strategy'] for r in subset)),
            M4_strategies=dict(collections.Counter(r['estimates']['4']['strategy'] for r in subset)),
            M4_arc_range=[min(r['estimates']['4']['estimated_arcs'] for r in subset),max(r['estimates']['4']['estimated_arcs'] for r in subset)],
            M4_assignments_range=[min(r['domain_assignment_count'] for r in subset),max(r['domain_assignment_count'] for r in subset)]))
    save('inventory_summary.json',dict(cases=len(rows),tick_counts=dict(collections.Counter(r['tick_seconds'] for r in rows)),
        trip_counts=dict(collections.Counter(r['max_trips'] for r in rows)),zhou_groups=groups))
    print(json.dumps(groups,ensure_ascii=False),flush=True)
    return rows


def inspect_builds(rows):
    selected=[next(r for r in rows if r['source']=='zhou_derived_scenario' and r['customers']==n)
              for n in (7,10,12,20)]
    measurements=[]
    for row in selected:
        for number in (1,2,3,4):
            cfg, fleet=load_routing_case(row['path'])
            cfg['model']=number
            p=load_instance(cfg,fleet)
            begun=time.perf_counter()
            m,ctx=build_model(p,deadline=begun+30)
            e1_windows=[]
            for r in ctx['e1']:
                for i,node in enumerate(r['nodes']):
                    e1_windows.append(dict(node=node['site'],kind=node['kind'],
                        ub=r['times'][i].UB,window=node['window']))
            stats=dict(case=row['case'],model=number,build_seconds=time.perf_counter()-begun,
                vars=m.NumVars,binary=m.NumBinVars,integer=m.NumIntVars,
                linear_constraints=m.NumConstrs,indicator_constraints=m.NumGenConstrs,
                e1_routes=len(ctx['e1']),e2_routes=len(ctx['e2']),
                e2_arcs=sum(len(r['x']) for r in ctx['e2']),
                shared_e2_arcs=ctx['shared_e2_arcs'],max_bound=m.MaxBound,
                min_coefficient=m.MinCoeff,max_coefficient=m.MaxCoeff,
                min_rhs=m.MinRHS,max_rhs=m.MaxRHS,
                MIPGap=m.Params.MIPGap,MIPGapAbs=m.Params.MIPGapAbs,
                Threads=m.Params.Threads,e1_windows=e1_windows)
            measurements.append(stats)
            m.dispose()
            save('build_measurements.json',measurements)
            print(json.dumps({k:v for k,v in stats.items() if k!='e1_windows'}),flush=True)


if __name__=='__main__':
    before=hashes()
    save('source_hashes_before.json',before)
    for relative in before:
        target=OUT/'source_snapshot'/relative
        target.parent.mkdir(parents=True,exist_ok=True)
        target.write_bytes((ROOT/relative).read_bytes())
    rows=scan()
    inspect_builds(rows)
    after=hashes()
    save('source_hashes_after.json',after)
    print('SOURCE_CHANGES',json.dumps([p for p in before if before[p]!=after.get(p)]),flush=True)
