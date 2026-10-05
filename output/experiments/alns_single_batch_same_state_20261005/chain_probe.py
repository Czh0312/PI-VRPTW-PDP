"""Fresh joint route/physical-vehicle repair on real rejected proposals."""
from pathlib import Path
from collections import Counter
import gzip
import hashlib
import json
import pickle
import sys
from experiment import OUT, now, write, sha, verify

def register():
    path = OUT / 'chain_protocol.json'
    assert not path.exists()
    write(path, dict(registered=now(), status='registered_before_inspecting_shared_cache_entries',
        purpose='Mechanism diagnostic on actual rejected search proposals, not another broad historical incumbent enumeration or quality adoption.',
        selection='All8 shared M3 branch states. Inspect evaluation LRU newest-first. Select first8 proposals with exactly E2_TIME_WINDOW as unique issue and distance below branch incumbent. No replacement or reseeding if none.',
        repair='Find routes containing a late customer start or terminal finish. Add physical-vehicle preceding routes and delivery routes of pickups, for2 dependency hops. In route-index order use first12 routes; change one route vehicle to each other physical vehicle at same satellite, preserving all tasks and route order. First32 variants per rejected proposal. No E1 edits or rule changes.',
        authority='Fresh uncached decode/full independent audit and periodic replay on shared incumbent, selected rejected proposals and every repair. Save all successes/failures and raw decoded results. Changed full E1+E2 distance compared to shared incumbent.',
        mechanism_gate='Require at least2 distinct shared states with audited improvement over incumbent, including at least1 size50 and1 size100, before considering an online bounded repair candidate. Failure deprioritizes this exact repair; no adoption, no arbitrary broader failure conclusion.',
        limits='8 correlated/previously used geometry states, cache-selected rejected moves, extra CPU. Not same-budget quality or true GAP. No change to earlier rejected diversity decision.'))
    write(OUT/'chain_registration_hashes.json',{'chain_protocol.json':sha(path),'chain_probe.py':sha(Path(__file__))})

def state_from_key(key):
    deliveries, pickup_order, pickup_vehicle, routes = key
    state = dict(deliveries={b:[list(r) for r in rows] for b,rows in deliveries},
        pickup_order={b:list(rows) for b,rows in pickup_order},
        pickup_vehicle={b:dict(rows) for b,rows in pickup_vehicle},e2=[])
    for s,v,tasks,depart,ret in routes:
        route=dict(satellite=s,vehicle=v,tasks=list(tasks))
        if depart is not None: route['depart_after']=depart
        if ret is not None: route['return_after']=ret
        state['e2'].append(route)
    return state

def chain(problem, state, decoded):
    policy=problem.operating_policy
    late=set(); dependencies={}; previous={}; delivered={}
    indices=[i for i,r in enumerate(state['e2']) if r['tasks']]
    assert len(indices)==len(decoded['e2_trips'])
    for i in indices:
        for oid,kind in state['e2'][i]['tasks']:
            if kind=='d': delivered[oid]=i
    for i,trip in zip(indices,decoded['e2_trips']):
        r=state['e2'][i]; vehicle=(r['satellite'],r['vehicle'])
        deps=set()
        if vehicle in previous: deps.add(previous[vehicle])
        previous[vehicle]=i
        for event in trip['events']:
            order=problem.order_map[event['order_id']]
            due=order.delivery_window[1] if event['kind']=='d' else order.pickup_window[1]
            if event['kind']=='d': due=min(due,policy.batches[0].cutoff-order.service_time)
            elif delivered[event['order_id']]!=i: deps.add(delivered[event['order_id']])
            if event['start']>due: late.add(i)
        if trip['return_complete']>policy.cycle_end-policy.e2_turnaround_time: late.add(i)
        dependencies[i]=deps
    selected=set(late)
    for _ in range(2):
        selected.update(parent for i in list(selected) for parent in dependencies[i])
    return sorted(selected)[:12],sorted(late)

def run():
    verify()
    for rel,digest in json.loads((OUT/'chain_registration_hashes.json').read_text()).items():
        assert sha(OUT/rel)==digest
    sys.path.insert(0,str(OUT/'baseline_source'))
    from configuration.loader import load_routing_case
    from models.routing.common.cycle import load_instance
    from models.routing.common.cycle.audit import audit_solution
    from models.routing.solvers.alns.evaluator import evaluate
    from models.routing.solvers.alns.neighborhoods import state_key,clone_state
    target=OUT/'chain_results';target.mkdir()
    summaries=[]
    for index,spec in enumerate(json.loads((OUT/'diagnostic_manifest.json').read_text())):
        config,fleet=load_routing_case(spec['case'])
        problem=load_instance({**config,'model':3},fleet)
        raw=json.loads((OUT/'diagnostic'/('%03d_baseline.json'%index)).read_text())
        blob=gzip.decompress((OUT/'diagnostic'/('%03d_shared_state.pkl.gz'%index)).read_bytes())
        assert hashlib.sha256(blob).hexdigest()==raw['branch']['state_sha256']
        shared=pickle.loads(blob)  # Only our own hash-verified snapshot.
        _,incumbent,issues=evaluate(problem,shared['best_state'])
        checked=audit_solution(problem,incumbent)
        assert not issues and checked['valid'] and checked['periodic_replay']['valid']
        assert abs(incumbent['info']['objective']-shared['best_cost'])<=1e-8
        selected=[]
        for key,(score,decoded,problems) in reversed(list(shared['cache_entries'].items())):
            if set(problems)=={'E2_TIME_WINDOW'} and decoded['info']['objective']<shared['best_cost']-1e-9:
                selected.append((key,decoded))
                if len(selected)==8:break
        stats=Counter(selected=len(selected));best=shared['best_cost'];rows=[]
        with gzip.open(target/('%03d_raw.jsonl.gz'%index),'wt') as handle:
            handle.write(json.dumps(dict(kind='incumbent',state=shared['best_state'],result=incumbent,audit=checked))+'\n')
            for number,(key,old_decoded) in enumerate(selected):
                original=state_from_key(key)
                assert state_key(original)==key
                _,decoded,issues=evaluate(problem,original)
                audit=audit_solution(problem,decoded)
                assert set(issues)=={'E2_TIME_WINDOW'} and not audit['valid']
                assert decoded['info']['objective']==old_decoded['info']['objective']
                indices,late=chain(problem,original,decoded)
                handle.write(json.dumps(dict(kind='rejected',number=number,state=original,result=decoded,issues=issues,audit=audit,chain=indices,late=late))+'\n')
                variants=0
                for route_index in indices:
                    for vehicle in range(problem.fleet.e2_per_sat_count):
                        if vehicle==original['e2'][route_index]['vehicle']:continue
                        if variants>=32:break
                        proposed=clone_state(original);proposed['e2'][route_index]['vehicle']=vehicle
                        assert Counter(t for r in proposed['e2'] for t in r['tasks'])==Counter(t for r in original['e2'] for t in r['tasks'])
                        assert proposed['deliveries']==original['deliveries'] and proposed['pickup_order']==original['pickup_order'] and proposed['pickup_vehicle']==original['pickup_vehicle']
                        _,result,problems=evaluate(problem,proposed);audit=audit_solution(problem,result)
                        valid=not problems and audit['valid'] and audit['periodic_replay']['valid']
                        improve=valid and result['info']['objective']<shared['best_cost']-1e-9
                        variants+=1;stats['variants']+=1;stats['valid']+=int(valid);stats['improving']+=int(improve)
                        if improve:best=min(best,result['info']['objective'])
                        record=dict(number=number,variant=variants,route=route_index,vehicle=vehicle,valid=valid,improving=improve,objective=result['info']['objective'],issues=problems)
                        rows.append(record)
                        handle.write(json.dumps(dict(check=record,state=proposed,result=result,audit=audit))+'\n')
                    if variants>=32:break
        summary=dict(index=index,job=spec,incumbent=shared['best_cost'],best=best,improved=best<shared['best_cost']-1e-9,counts=dict(stats),results=rows)
        write(target/('%03d_summary.json'%index),summary);summaries.append(summary)
        print('chain',index,spec['size'],dict(stats),'incumbent',shared['best_cost'],'best',best,flush=True)
    improved=[r for r in summaries if r['improved']]
    eligible=len(improved)>=2 and {r['job']['size'] for r in improved}=={50,100}
    write(OUT/'chain_decision.json',dict(eligible_for_online_trial=eligible,adopted=False,
        improved_states=len(improved),total_states=len(summaries),summaries=[{k:v for k,v in r.items() if k!='results'} for r in summaries],
        interpretation='Mechanism-only extra work; no production adoption or equal-time claim.'))

if __name__=='__main__':
    {'register':register,'run':run}[sys.argv[1]]()
