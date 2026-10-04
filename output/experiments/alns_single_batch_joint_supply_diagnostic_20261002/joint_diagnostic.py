"""Descriptive bounded joint repair, with fresh decode and complete audit."""
from pathlib import Path
import collections, hashlib, json, os, sys, time, traceback
OUT=Path(__file__).resolve().parent
sys.path.insert(0,str(OUT/'frozen_source'))
from configuration.loader import load_routing_case
from models.routing.common.cycle import load_instance
from models.routing.common.cycle.audit import audit_solution
from models.routing.solvers.alns.evaluator import evaluate
from models.routing.solvers.alns.neighborhoods import clone_state, state_key, state_from_result, _goods_ready
from models.routing.solvers.alns.single_batch_descent import SingleBatchDescent

def write(path,value): path.write_text(json.dumps(value,indent=2),encoding='utf-8')
def sha(path): return hashlib.sha256(path.read_bytes()).hexdigest()
def inventory(state):
    return collections.Counter((r['satellite'],oid,kind) for r in state['e2'] for oid,kind in r['tasks'])
def apply_e1(state,op):
    result=clone_state(state); kind,args=op
    bid,a,pos,b,dest=args; rows=result['deliveries'][bid]
    if kind=='delivery': rows[b].insert(dest,rows[a].pop(pos))
    elif kind=='delivery_swap': rows[a][pos],rows[b][dest]=rows[b][dest],rows[a][pos]
    else: raise ValueError(kind)
    return result
def apply_e2(state,changes):
    result=clone_state(state)
    for index,tasks in changes: result['e2'][index]['tasks']=list(tasks)
    # Keep empty rows until final decoding so indices stay fixed for repair.
    assert inventory(result)==inventory(state)
    return result
def vehicle_variants(problem,state,changed,limit):
    result=[]; seen={state_key(state)}
    for index in sorted(changed):
        row=state['e2'][index]
        for vehicle in range(problem.fleet.e2_per_sat_count):
            if vehicle==row['vehicle']:continue
            candidate=clone_state(state);candidate['e2'][index]['vehicle']=vehicle
            key=state_key(candidate)
            if key not in seen:
                seen.add(key);result.append((dict(kind='vehicle',index=index,vehicle=vehicle),candidate))
        # Reorder complete route rows within this satellite; full DAG handles
        # pickup release precedence and the physical vehicle serial schedule.
        for other,route in enumerate(state['e2']):
            if other==index or route['satellite']!=row['satellite']:continue
            candidate=clone_state(state)
            candidate['e2'][index],candidate['e2'][other]=candidate['e2'][other],candidate['e2'][index]
            key=state_key(candidate)
            if key not in seen:
                seen.add(key);result.append((dict(kind='route_order',index=index,other=other),candidate))
    return result[:limit]

def measure(problem,state):
    start=time.perf_counter()
    score,result,issues=evaluate(problem,state) # no evaluation or return cache
    audit=audit_solution(problem,result) # repeat=True includes periodic replay
    valid=not issues and audit.get('valid') and audit.get('periodic_replay',{}).get('valid')
    row=dict(score=score,objective=result['info'].get('objective'),issues=issues,
             audit_valid=audit.get('valid',False),periodic=audit.get('periodic_replay',{}).get('valid',False),
             audit_errors=audit.get('errors',[]),valid=bool(valid),seconds=time.perf_counter()-start)
    return row,result,audit

def run_job(item,protocol):
    folder=OUT/'diagnostic'/Path(item['name']).stem;folder.mkdir(parents=True)
    config,fleet=load_routing_case(item['case']);problem=load_instance({**config,'model':3},fleet)
    raw=json.loads((OUT/'incumbents'/item['name']).read_text())
    state=state_from_result(problem,raw['result']);original_key=state_key(state)
    before,result,audit=measure(problem,state)
    write(folder/'reconstructed_state.json',state)
    write(folder/'reconstruction.json',dict(measure=before,result=result,independent_audit=audit))
    equality=before['objective'] is not None and abs(before['objective']-raw['result']['info']['objective'])<=1e-7
    summary=dict(name=item['name'],job=item['job'],load_start=os.getloadavg(),reconstruction_valid=before['valid'],
        reconstruction_objective_equal=equality,original_objective=raw['result']['info']['objective'],
        baseline_objective=before['objective'],attempts=0,valid_variants=0,barriers=[],best=None)
    if not before['valid'] or not equality:
        summary['status']='RECONSTRUCTION_FAILED';write(folder/'summary.json',summary);return summary
    deadline=time.perf_counter()+protocol['per_incumbent_seconds']
    descent=SingleBatchDescent(problem,capacity=protocol['geometry_capacity'],cache_sites=False,cache_distances=True)
    geometry=descent._e2_candidates(state,min(deadline,time.perf_counter()+protocol['enumeration_seconds']))
    e1ops=[op for op in reversed(descent._candidates(state,deadline)) if op[0] in ('delivery','delivery_swap')]
    # Canonical deterministic deduplication; no scoring based selection of E1.
    e1ops=list(dict.fromkeys(e1ops))[:protocol['e1_capacity']]
    write(folder/'candidates.json',dict(e1=e1ops,e2=geometry))
    rows=[];baseline=before['objective'];e1metrics={};best=baseline
    def record(mode,candidate,**metadata):
        nonlocal best
        if time.perf_counter()>=deadline:return None
        row,decoded,checked=measure(problem,candidate)
        row.update(mode=mode,**metadata);row['index']=len(rows);rows.append(row)
        # All successful and failed trials are retained, not only improvements.
        write(folder/('trial_%04d.json'%row['index']),dict(parameters=metadata,mode=mode,state=candidate,
             measure=row,result=decoded,independent_audit=checked))
        if row['valid'] and row['objective']<best-1e-7:
            best=row['objective'];summary['best']=dict(index=row['index'],mode=mode,objective=best,
                change_percent=100*(best/baseline-1),parameters=metadata)
        return row
    for e1idx,op in enumerate(e1ops):
        row=record('e1_only',apply_e1(state,op),e1=e1idx)
        if row is None:break
        e1metrics[e1idx]=row
    blocked=[]
    for e2idx,(_,changes) in enumerate(geometry):
        row=record('e2_only',apply_e2(state,changes),e2=e2idx)
        if row is None:break
        # Supply/order/vehicle changes cannot change tasks or route loads.
        if not row['valid'] and not set(row['issues']) & {'E2_CAPACITY','E2_TASK_COVERAGE','MIXED_DELIVERY_BATCHES','INVALID_E2_VEHICLE'}:
            blocked.append((e2idx,changes,row))
    for e2idx,changes,e2metric in blocked[:protocol['blocked_capacity']]:
        e2state=apply_e2(state,changes)
        for e1idx,op in enumerate(e1ops):
            if e1idx not in e1metrics:break
            row=record('supply_e2',apply_e1(e2state,op),e1=e1idx,e2=e2idx)
            if row is None:break
            if row['valid'] and row['objective']<baseline-1e-7 and (not e1metrics[e1idx]['valid'] or e1metrics[e1idx]['objective']>=baseline-1e-7):
                summary['barriers'].append(dict(index=row['index'],mode='supply_e2',e1=e1idx,e2=e2idx,
                    objective=row['objective'],e1_only=e1metrics[e1idx],e2_only=e2metric))
        vehicles=vehicle_variants(problem,e2state,{i for i,_ in changes},protocol['vehicle_capacity'])
        for vidx,(vop,vstate) in enumerate(vehicles):
            row=record('vehicle_e2',vstate,e2=e2idx,vehicle=vidx,vehicle_op=vop)
            if row is None:break
            for e1idx,op in enumerate(e1ops[:protocol['triple_e1_capacity']]):
                if e1idx not in e1metrics:break
                triple=record('supply_vehicle_e2',apply_e1(vstate,op),e1=e1idx,e2=e2idx,vehicle=vidx,vehicle_op=vop)
                if triple is None:break
    assert state_key(state)==original_key, 'Diagnostic must not mutate control'
    summary.update(status='BOUNDED_DIAGNOSTIC_COMPLETE',attempts=len(rows),valid_variants=sum(r['valid'] for r in rows),
        e1_candidates=len(e1ops),geometry_candidates=len(geometry),blocked_repairable=len(blocked),
        visited_blocked=min(len(blocked),protocol['blocked_capacity']),time_limit_reached=time.perf_counter()>=deadline,
        best_objective=best,change_percent=100*(best/baseline-1),load_end=os.getloadavg())
    write(folder/'trials_summary.json',rows);write(folder/'summary.json',summary)
    return summary

def main():
    for name,value in json.loads((OUT/'registration_hashes.json').read_text()).items():assert sha(OUT/name)==value,name
    protocol=json.loads((OUT/'protocol.json').read_text());items=json.loads((OUT/'incumbent_manifest.json').read_text())
    summaries=[]
    for item in items:
        try: summary=run_job(item,protocol)
        except Exception as exc:
            traceback.print_exc();summary=dict(name=item['name'],status='FAILED',error=repr(exc))
        summaries.append(summary);write(OUT/'summary.json',summaries)
        print(json.dumps({k:summary.get(k) for k in ('name','status','reconstruction_valid','attempts','valid_variants','change_percent','best')}),flush=True)
    write(OUT/'completion.json',dict(jobs=len(summaries),errors=sum(r['status']=='FAILED' for r in summaries),load=os.getloadavg()))
if __name__=='__main__':main()
