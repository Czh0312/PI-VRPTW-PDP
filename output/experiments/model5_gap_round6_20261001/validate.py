"""Independent physical slots, route loads, frozen MPS and global projections."""
from copy import deepcopy
import json,math
from pathlib import Path
import sys
HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE/'source_snapshot'));sys.path.insert(0,str(HERE))
import validation_core as check
import gurobipy as gp
from configuration.loader import load_routing_case
from models.routing.common.cycle import load_instance
PLAN=check.read(HERE/'plan.json');OLD=HERE.parent/'model5_gap_round5_20261001'
for f,d in PLAN['source_sha256'].items():assert check.sha(HERE/'source_snapshot'/f)==d
for f,d in PLAN['input_sha256'].items():assert check.sha(HERE/f)==d
for f,d in check.read(HERE/'validation_sources.json').items():assert check.sha(Path(f))==d
assert check.sha(HERE/'run.py')==PLAN['runner_sha256']
dependencies=check.read(HERE/'dependency_binding.json')
assert check.sha(Path(dependencies['original_manifest_path']))==dependencies['original_manifest_sha256']
original_manifest=check.read(Path(dependencies['original_manifest_path']))['artifact_sha256']
for entry in dependencies['records']:
    assert check.sha(Path(entry['path']))==entry['sha256']==original_manifest[entry['original_round4_artifact']]
previous=check.read(HERE/'inputs/previous.json')['cases']
baseline_verified=check.read(OLD/'validation_final.json');assert baseline_verified['status']=='VERIFIED'
audits=columns=0;models=[];records=[]

def validate_slots(p,initial,slots,extra):
    original={t['trip_id']:t for t in initial['e2_trips']}
    assert len({t['trip_id'] for t in slots})==len(slots)
    assert set(original)<=set(t['trip_id'] for t in slots)
    for slot in slots:
        assert slot['satellite_id'] in p.satellites and 0<=slot['vehicle_id']<p.fleet.e2_per_sat_count
        assert 0<=slot['trip_index']<p.operating_policy.e2_max_trips_per_vehicle
        if slot['trip_id'] in original:
            assert {k:v for k,v in slot.items() if k!='trip_index'}=={k:v for k,v in original[slot['trip_id']].items() if k!='trip_index'}
        else:
            assert extra>0 and slot['_pool_optional'] and slot['events']==[]
            assert slot['departure']==slot['return_complete']==0
    for site in {o.satellite for o in p.orders}:
        for vehicle in range(p.fleet.e2_per_sat_count):
            old=sorted([t for t in initial['e2_trips'] if (t['satellite_id'],t['vehicle_id'])==(site,vehicle)],key=lambda t:t['trip_index'])
            rows=sorted([t for t in slots if (t['satellite_id'],t['vehicle_id'])==(site,vehicle)],key=lambda t:t['trip_index'])
            assert len(rows)==min(len(old)+extra,p.operating_policy.e2_max_trips_per_vehicle)
            assert [t['trip_id'] for t in rows if t['trip_id'] in original]==[t['trip_id'] for t in old]
            assert len({t['trip_index'] for t in rows})==len(rows)

def project_pool(p,initial,result,slots,pools,path):
    model=gp.read(str(path))
    try:
        variables={v.VarName:v for v in model.getVars()}
        def fix(name,value):
            var=variables[name];var.LB=var.UB=value
        patterns=check.all_patterns(p)
        lookup={(r['batch'],r['delivery'],r['pickup']):i for i,r in enumerate(patterns)}
        chosen=set();amounts={}
        for trip in result['e1_trips']:
            ds=tuple(e['site_id'] for e in trip['events'] if e['kind']=='d')
            ps=tuple(e['site_id'] for e in trip['events'] if e['kind']=='p')
            i=lookup[trip['batch_id'],ds,ps];chosen.add(i)
            for e in trip['events']:
                if e['kind']=='p':amounts[i,e['site_id']]=e['quantity']
        for i,r in enumerate(patterns):
            check.close(variables[f'route[{i}]'].Obj,r['cost']);fix(f'route[{i}]',int(i in chosen))
            for site in r['pickup']:fix(f'quantity_{i}_{site}',amounts.get((i,site),0))
        active={t['trip_id']:t for t in result['e2_trips']}
        # The unchanged fallback may retain its original slot indices; IDs
        # still identify the same chronologically ordered physical slots.
        for i,(slot,pool) in enumerate(zip(slots,pools)):
            trip=active.get(slot['trip_id']);keys=[(e['order_id'],e['kind']) for e in trip['events']] if trip else []
            selected=next((j for j,c in enumerate(pool) if [tuple(k) for k in c['tasks']]==keys),None) if trip else None
            assert not trip or selected is not None
            fix(f'pool_used_{i}',int(trip is not None))
            fix(f'pool_start_{i}',trip['departure'] if trip else 0);fix(f'pool_end_{i}',trip['return_complete'] if trip else 0)
            for j,column in enumerate(pool):
                check.close(variables[f'pool_choice_{i}[{j}]'].Obj,column['cost'])
                fix(f'pool_choice_{i}[{j}]',int(j==selected))
        for trip in result['e2_trips']:
            for e in trip['events']:
                oid,kind=e['order_id'],e['kind']
                fix(('delivery_' if kind=='d' else 'pickup_time_')+oid,e['start'])
                if kind=='p':
                    fix('e2_return_'+oid,trip['return_complete'])
                    first=next(b.id for b in p.operating_policy.batches if trip['return_complete']<=b.cutoff)
                    for b in p.operating_policy.batches:fix(f'cutoff_{oid}_{b.id}',int(first==b.id))
        for o in p.orders:
            for kind in ('d','p'):
                constraint=model.getConstrByName(f'pool_cover_{o.id}_{kind}')
                assert constraint.Sense=='=' and constraint.RHS==1
                expected={f'pool_choice_{i}[{j}]':1 for i,pool in enumerate(pools) for j,c in enumerate(pool)
                          if (o.id,kind) in [tuple(k) for k in c['tasks']]}
                assert check.expr(model.getRow(constraint))==expected
        # Validate every pair of slots, including pairs separated by unused
        # placeholders, against the physical timing inequalities.
        rows=set()
        for c in model.getConstrs():
            if c.Sense=='>':
                terms=check.expr(model.getRow(c))
                if any(name.startswith('pool_start_') for name in terms):
                    rows.add((c.RHS,tuple(sorted(terms.items()))))
        M=p.operating_policy.cycle_end+p.operating_policy.e2_turnaround_time
        pairs=0
        for site in {o.satellite for o in p.orders}:
            for vehicle in range(p.fleet.e2_per_sat_count):
                indices=sorted([i for i,t in enumerate(slots) if (t['satellite_id'],t['vehicle_id'])==(site,vehicle)],key=lambda i:slots[i]['trip_index'])
                for pos,i in enumerate(indices):
                    for j in indices[pos+1:]:
                        terms={f'pool_start_{j}':1,f'pool_end_{i}':-1,f'pool_used_{i}':-M,f'pool_used_{j}':-M}
                        if p.operating_policy.e2_dispatch_prep_time:
                            terms.update({f'pool_choice_{j}[{k}]':-p.operating_policy.e2_dispatch_prep_time
                                          for k,c in enumerate(pools[j]) if any(kind=='d' for _,kind in c['tasks'])})
                        assert (p.operating_policy.e2_turnaround_time-2*M,tuple(sorted(terms.items()))) in rows
                        pairs+=1
        model.Params.OutputFlag=0;model.Params.Threads=1;model.Params.TimeLimit=30
        model.Params.FeasibilityTol=model.Params.IntFeasTol=1e-9
        model.optimize();assert model.Status==gp.GRB.OPTIMAL
        check.close(model.ObjVal,result['info']['objective'])
        return dict(path=str(path),full_solution_projection=True,projected_objective=model.ObjVal,
                    physical_pair_rows=pairs,all_tasks_exactly_covered=True,patterns=len(patterns))
    finally:model.dispose()

jobs=list(check.read(HERE/'summary.json'))
if (HERE/'full_refined.json').exists():
    refinement=check.read(HERE/'refine_plan.json')
    assert check.sha(HERE/'refine.py')==refinement['runner_sha256']
    assert check.sha(HERE/refinement['input_path'])==refinement['input_sha256']
    jobs.append(dict(job=dict(id='full_refined',label='full',extra_slots=refinement['extra_slots_per_vehicle']),failure=None))
best={}
for record in jobs:
    if record['failure']:records.append(record);continue
    job=record['job'];payload=check.read(HERE/(job['id']+'.json'))
    p=load_instance(*load_routing_case(str(HERE/'inputs'/(job['label']+'_case.json')),use_pool_config=False))
    check.floyd(p)
    audit=check.checked_audit(p,payload['result']);audits+=1
    assert payload['result']['info']['best_bound'] is None and not payload['result']['info']['proven_optimal']
    prior=next(c for c in previous if c['label']==job['label'])
    close=check.close;close(payload['lower'],prior['bound']);close(payload['upper'],audit['objective'])
    close(payload['gap'],max(0,payload['upper']-payload['lower'])/abs(payload['upper']))
    models_path=HERE/(job['id']+'_models')
    for directory in sorted(models_path.glob('pass_*')):
        initial=check.read(directory/'initial_result.json');result=check.read(directory/'output_result.json')
        check.checked_audit(p,initial);check.checked_audit(p,result);audits+=2
        assert result['info']['objective']<=initial['info']['objective']+1e-6
        if not (directory/'columns.json').exists():
            assert result['info']['route_pool_status']=='TIME_LIMIT_BUILD';close(result['info']['objective'],initial['info']['objective']);continue
        slots=check.read(directory/'slots.json');pools=check.read(directory/'columns.json')
        validate_slots(p,initial,slots,job['extra_slots']);assert len(slots)==len(pools)
        for slot,pool in zip(slots,pools):
            if slot['events']:
                assert [tuple(k) for k in pool[0]['tasks']]==[(e['order_id'],e['kind']) for e in slot['events']]
            for column in pool:check.replay.replay_column(p,slot['satellite_id'],column);columns+=1
        models.append(project_pool(p,initial,result,slots,pools,directory/'route_pool.mps'))
    if job['label'] not in best or payload['upper']<best[job['label']]['upper']:
        best[job['label']]=payload
    records.append(dict(id=job['id'],upper=payload['upper'],lower=payload['lower'],gap_percent=100*payload['gap'],target_reached=payload['target_reached']))
for label,payload in best.items():
    p=load_instance(*load_routing_case(str(HERE/'inputs'/(label+'_case.json')),use_pool_config=False))
    check.floyd(p);prior=next(c for c in previous if c['label']==label)
    base=HERE.parent/'model5_gap_round4_20261001/fresh'/('0'+('2' if label=='full' else '3')+'_zhou_Set2_100-5-1b_'+label+'_43_models')/'reference/e1_inventory.mps'
    models.append(check.check_joint(p,payload['result'],prior['reference_certificate'],prior['joint_certificate'],OLD/'retry'/(label+'_joint.mps.gz'),base))
result=dict(status='VERIFIED',cases=records,best={k:{f:v[f] for f in ['upper','lower','gap','target_reached']} for k,v in best.items()},
    whole_cycle_audits=audits,independently_replayed_columns=columns,model_checks=models,
    global_proof_source_sha256=check.sha(OLD/'validation_final.json'))
write=lambda p,v:p.write_text(json.dumps(v,ensure_ascii=False,indent=2,allow_nan=False)+'\n')
write(HERE/'validation.json',result);print({k:v for k,v in result.items() if k!='model_checks'},flush=True)
