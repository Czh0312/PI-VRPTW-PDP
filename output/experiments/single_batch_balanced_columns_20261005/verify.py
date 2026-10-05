"""Read frozen pairs, independently replay every guided route, check certificates."""
from pathlib import Path
import hashlib
import json
import math
import sys
import time

OUT = Path(__file__).resolve().parent
sys.path.insert(0, str(OUT/'candidate_source'))
from configuration.loader import load_routing_case
from models.routing.common.cycle import load_instance
from models.routing.common.cycle.audit import audit_solution
from models.routing.solvers.exact.single_batch_columns import problem_fingerprint
from models.routing.solvers.exact.single_batch_pickup_bound import pickup_contexts, project_original_schedule
from models.routing.solvers.exact.multibatch_route_pricing import TripPricingModel


def read(p): return json.loads(Path(p).read_text())
def write(p, v): Path(p).write_text(json.dumps(v, indent=2, ensure_ascii=False, allow_nan=False)+'\n')
def sha(p): return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def shortest_times(p):
    nodes = list(p.network.nodes)
    distances = {(a,b):p.travel(a,b) for a in nodes for b in nodes}
    for k in nodes:
        for i in nodes:
            for j in nodes:
                distances[i,j] = min(distances[i,j], distances[i,k]+distances[k,j])
    return distances


def raw_route(p, site, sequence, shortest):
    """Independent raw-matrix replay; no production column evaluation/context."""
    policy, fleet = p.operating_policy, p.fleet
    batch = policy.batches[0]
    assert sequence and len(sequence)==len(set(sequence))
    ready = batch.start+policy.e1_load_time+shortest[p.depot,site]+p.service(site)+policy.e2_dispatch_prep_time
    clock = max(policy.cycle_start, ready) if any(k=='d' for _,k in sequence) else policy.cycle_start
    load = [math.fsum(p.task(o,k)[j] for o,k in sequence if k=='d') for j in (1,2)]
    capacities = [fleet.e2_weight_cap, fleet.e2_volume_cap]
    assert all(-1e-6<=q<=cap+1e-6 for q,cap in zip(load,capacities))
    delivered, previous, cost = {}, site, 0.0
    for oid, kind in sequence:
        assert kind in ('d','p')
        order, weight, volume = p.task(oid,kind)
        earliest_d = max(order.delivery_window[0],ready+shortest[site,order.customer])
        if kind=='d':
            low,high = earliest_d,min(order.delivery_window[1],batch.cutoff-order.service_time)
        else:
            low = max(order.pickup_window[0],earliest_d+order.service_time+order.min_use_time)
            high = min(order.pickup_window[1],batch.cutoff-order.service_time
                       -shortest[order.customer,site]-policy.e2_return_unload_time)
        clock = max(clock+p.travel(previous,order.customer),low)
        if kind=='p' and (oid,'d') in sequence:
            assert oid in delivered
            clock = max(clock,delivered[oid]+order.service_time+order.min_use_time)
        assert clock<=high
        if kind=='d':delivered[oid]=clock
        for j,amount in enumerate((weight,volume)):
            load[j] += amount*(1 if kind=='p' else -1)
        assert all(-1e-6<=q<=cap+1e-6 for q,cap in zip(load,capacities))
        cost += p.distance(previous,order.customer)
        clock += order.service_time;previous=order.customer
    returned = clock+p.travel(previous,site)+policy.e2_return_unload_time
    assert returned+policy.e2_turnaround_time<=policy.cycle_end
    if any(k=='p' for _,k in sequence):assert returned<=batch.cutoff
    return cost+p.distance(previous,site)


def main():
    target=OUT/'verification';target.mkdir()
    begun=time.perf_counter(); hashes=0
    for label in ('baseline','candidate'):
        for rel,h in read(OUT/f'{label}_manifest.json').items():
            assert sha(OUT/f'{label}_source'/rel)==h;hashes+=1
    for rel,h in read(OUT/'input_manifest.json').items():
        assert sha(OUT/rel)==h;hashes+=1
    assert sha(OUT/'driver_frozen.py')==read(OUT/'preparation.json')['driver_sha256']
    jobs=[read(p) for p in read(OUT/'jobs.json')]
    assert len(jobs)==24 and len(read(OUT/'comparison.json'))==24
    cases={}; rows=[]; audits=[]; reproved=[]; route_checks=0; raw_rows=0
    for job in jobs:
        cfg,fleet=load_routing_case(job['input']);p=load_instance(cfg,fleet)
        start=read(job['start']);audit=audit_solution(p,start)
        assert audit['valid'] and audit['periodic_replay']['valid']
        assert problem_fingerprint(p)==job['original_fp']
        assert sha(job['input'])==job['input_sha256'] and sha(job['start'])==job['start_sha256']
        domains,contexts,_=pickup_contexts(p)
        assert sum(len(c['tasks']) for _,c in project_original_schedule(p,start,contexts))==40
        shortest=cases.setdefault(job['case'],shortest_times(p))
        row=read(job['row']);result=read(job['result'])
        assert row['result_sha256']==sha(job['result'])
        assert result['all_task_count']==40 and not result['proven_optimal'] and not result['proven_infeasible']
        assert not result.get('heuristic_columns',False)
        assert result.get('balanced_columns',False)==(job['method']=='balanced')
        assert result['bound']<=audit['objective']+1e-6
        assert math.isclose(result['bound'],result['e1_floor']+result['e2_bound'],abs_tol=1e-7)
        for discovery in result.get('balanced_history',[]):
            assert discovery['corrected_e2_bound'] is None and discovery['proof_scope']=='discovery_only'
            for site,data in discovery['sites'].items():
                original={tuple(k):v for k,v in data['original_prices']}
                balanced={tuple(k):v for k,v in data['balanced_prices']}
                assert set(original)==set(balanced)==set(contexts[site]['lookup'])
                for oid in {k[0] for k in original}:
                    assert math.isclose(math.fsum(original[oid,k] for k in ('d','p')),
                        math.fsum(balanced[oid,k] for k in ('d','p')),abs_tol=1e-7)
                for column in data['columns']:
                    sequence=tuple(tuple(k) for k in column['tasks'])
                    assert set(sequence)<=set(original)
                    for oid,_ in sequence:assert site in domains[p.order_map[oid].customer]
                    cost=raw_route(p,site,sequence,shortest)
                    assert math.isclose(cost,column['cost'],abs_tol=1e-7)
                    rc=cost-math.fsum(original[k] for k in sequence)-data['fleet_price']
                    assert math.isclose(rc,column['original_reduced_cost'],abs_tol=1e-7)
                    assert column['original_negative']==(rc<-1e-7)
                    if column['added']:assert rc<-1e-7
                    route_checks+=1
        sequence=sorted([(h['iteration'],0,h['added']) for h in result.get('balanced_history',[])]+
                        [(h['iteration'],1,0) for h in result['history']])
        consecutive=0
        for _,kind,added in sequence:
            consecutive=consecutive+1 if kind==0 and added else 0
            assert consecutive<=1
        phase2=[h for h in result['history'] if h['phase']==2]
        for h in phase2:
            corrected=h['restricted_objective']+math.fsum(result['maximum_trips'][s]*min(0,lb)
                for s,lb in h['pricing_lower_bounds'].items())-h['margin']
            assert math.isclose(corrected,h['corrected_e2_bound'],abs_tol=1e-7)
            explicit=math.fsum(h['assignment_duals'].values())+math.fsum(
                result['maximum_trips'][s]*data['fleet'] for s,data in h['prices'].items())
            assert math.isclose(explicit,h['restricted_objective'],abs_tol=1e-5)
            raw_rows+=1
        assert math.isclose(result['e2_bound'],max([0]+[h['corrected_e2_bound'] for h in phase2]),abs_tol=1e-7)
        # Only a positive best witness needs large independent repricing. A zero
        # effective floor follows from nonnegative raw distances, not weak rho.
        if result['e2_bound']>0:
            best=max(phase2,key=lambda h:h['corrected_e2_bound'])
            for site,data in best['prices'].items():
                pi={tuple(k):v for k,v in data['tasks']};nu=data['fleet']
                check=TripPricingModel(p,contexts[site],seed=73,linear_links=False)
                try:answer=check.price(pi,nu,time_limit=10)
                finally:check.dispose()
                lower=answer['reduced_cost'] if answer['optimal'] else answer['lower_bound']
                verified=answer['infeasible'] or best['pricing_lower_bounds'][site]<=lower+1e-6
                reproved.append(dict(job=job,site=site,result=answer,verified=verified))
                write(target/'positive_price_checks.json',reproved)
        rows.append(row);audits.append(dict(case=job['case'],seed=job['seed'],budget=job['budget'],
            method=job['method'],original_audit=True,periodic_replay=True,all40tasks=True))
    groups={}
    for row in rows:groups.setdefault((row['case'],row['seed'],row['budget']),{})[row['method']]=row
    pairs=[]
    for (case,seed,budget),group in groups.items():
        assert set(group)=={'baseline','balanced'}
        b,c=group['baseline'],group['balanced']
        assert b['original_fp']==c['original_fp'] and b['start_sha256']==c['start_sha256']
        pairs.append(dict(case=case,seed=seed,budget=budget,baseline=b,balanced=c,
                          delta_e2=c['e2_bound']-b['e2_bound']))
    assert len(pairs)==12
    wins=[p for p in pairs if p['delta_e2']>1e-5]
    losses=[p for p in pairs if p['delta_e2']<-1e-5]
    bound_gate=(len(wins)>=8 and not losses and len({p['case'] for p in wins})==2
                and {p['seed'] for p in wins}=={42,43,44} and {p['budget'] for p in wins}=={15,30})
    speed_gate=all(p['baseline']['complete'] and p['balanced']['complete']
                   and p['baseline']['e2_bound']>0 and abs(p['delta_e2'])<=1e-5 for p in pairs)
    for case,budget in {(p['case'],p['budget']) for p in pairs}:
        selected=[p for p in pairs if p['case']==case and p['budget']==budget]
        speed_gate=speed_gate and all(sum(p['balanced'][key] for p in selected)<
                                     sum(p['baseline'][key] for p in selected)
                                     for key in ('runtime','pricing_work'))
    summary=dict(status='VERIFIED' if all(p['verified'] for p in reproved) else 'POSITIVE_PRICE_UNVERIFIED',
        hashes_checked=hashes,original_schedule_audits=24,original_periodic_replays=24,
        original_task_projections=24,independent_guided_route_replays=route_checks,
        original_certificate_arithmetic_checks=raw_rows,positive_large_price_checks=len(reproved),
        all_positive_prices_verified=all(p['verified'] for p in reproved),pairs=12,
        effective_e2_wins=len(wins),effective_e2_losses=len(losses),
        guided_added=sum(r['guided_added'] for r in rows),guided_returned=sum(r['guided_returned'] for r in rows),
        guided_original_negative=sum(r['guided_original_negative'] for r in rows),
        registered_budget=540,actual_total_runtime=sum(r['runtime'] for r in rows),
        actual_guided_runtime=sum(r['guided_runtime'] for r in rows),
        maximum_guided_quota_overrun=max([0]+[r['guided_runtime']-.2*r['root_allocation'] for r in rows]),
        complete_roots=sum(r['complete'] for r in rows),integer_global_proofs=0,
        root_gate_passed=bool((bound_gate or speed_gate) and all(p['verified'] for p in reproved)),
        production_default_adopted=False,seconds=time.perf_counter()-begun,
        limitation='Root-only comparison; nonpositive large pricing witnesses not independently repriced. Shared load recorded, not controlled. Tiny complete universes verified separately.')
    write(target/'audits.json',audits);write(target/'pairs.json',pairs);write(target/'summary.json',summary)
    print(json.dumps(summary,ensure_ascii=False,indent=2))


if __name__=='__main__':main()
