"""Independent full-domain proof and physical replay of warm adaptive refinement."""
from copy import deepcopy
import json,math
from pathlib import Path
import sys
HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE/'candidate_source'));sys.path.insert(0,str(HERE))
import validation_core as check
import validation_pool as pool
import validation_conjunction as joint
from configuration.loader import load_routing_case
from models.routing.common.cycle import load_instance
from models.routing.solvers.exact.multibatch_bounds import distance_certificate
from models.routing.solvers.exact.multibatch_route_pricing import route_context
plan=check.read(HERE/'plan.json')
assert check.sha(HERE/'run.py')==plan['runner_sha256']
assert check.sha(Path(__file__))==plan['validator_sha256']
for directory,hashes in plan['source_sha256'].items():
    for name,digest in hashes.items():assert check.sha(HERE/directory/name)==digest
for name,digest in plan['input_sha256'].items():assert check.sha(HERE/name)==digest
for name,digest in plan['checker_sha256'].items():assert check.sha(HERE/name)==digest
base=plan['source_sha256']['baseline_source'];candidate=plan['source_sha256']['candidate_source']
assert {name for name in set(base)|set(candidate) if base.get(name)!=candidate.get(name)}==set(plan['declared_source_changes'])
assert check.sha(Path(plan['origin']))==plan['origin_sha256']
assert check.sha(Path(plan['origin_final']))==plan['origin_final_sha256']
origin_final=check.read(Path(plan['origin_final']));assert origin_final['status']=='VERIFIED'
origin=check.read(Path(plan['origin']))['experiment'];state=check.read(Path(plan['state']))
assert state==dict(initial_result=origin['result'],reference=origin['reference_certificate'],conditional=origin['conditional_certificate'],fallback_bound=origin['conditional_certificate']['bound'])
for binding in plan['input_bindings']:
    assert check.sha(Path(binding['origin']))==check.sha(Path(binding['frozen']))==binding['sha256']
    assert binding['sha256']==origin_final['artifact_sha256'][str(Path(binding['origin']).relative_to(Path(plan['origin']).parent))]
summary=check.read(HERE/'summary.json');assert [r['job'] for r in summary]==plan['jobs'] and not any(r['failure'] for r in summary)
models=[];audits=columns=0
def context_for(p,site,shortest,row=None):
    context=check.independent_context(p,site,shortest)
    if not row:return context
    supplies=row.get('supply_conditions',[dict(batch=row.get('batch'),threshold=row.get('threshold'))])
    release=dict(context['release'])
    for condition in supplies:
        bid=condition['batch'];release[bid]=max(release[bid],condition['threshold']+p.operating_policy.e2_dispatch_prep_time)
    nodes=[]
    for node in context['nodes']:
        order=node['order'];d=max(order.delivery_window[0],release[order.batch]+shortest[site,order.customer])
        lo=d if node['key'][1]=='d' else max(order.pickup_window[0],d+order.service_time+order.min_use_time)
        nodes.append({**node,'window':(lo,node['window'][1])})
    return {**context,'release':release,'nodes':nodes,'lookup':{n['key']:n for n in nodes}}


def leaf(p,row,context,path):
    check.check_site(p,row,context)
    joint.verify_pricing_models(p,row,context,path)


def components(ref,baseline):
    e1=math.fsum(max(0,r['bound']-1e-7*max(1,r['bound']))
                 for r in baseline['components'] if r['echelon']=='E1')
    check.close(ref['e1_bound'],max(e1,ref['e1_certificate']['bound']))
    distances={r['satellite']:max(0,r['bound']-1e-7*max(1,r['bound']))
               for r in baseline['components'] if r['echelon']=='E2'}
    rows={r['satellite']:r for r in ref['sites']}
    assert set(rows)==set(distances)=={c['satellite'] for c in ref['components']}
    for c in ref['components']:
        check.close(c['distance_bound'],distances[c['satellite']])
        check.close(c['route_cover_bound'],rows[c['satellite']]['bound'])
        check.close(c['bound'],max(c['distance_bound'],c['route_cover_bound']))


def pools(p,parent,extras):
    global audits,columns
    last=None
    for directory in sorted(parent.glob('pass_*')):
        initial=check.read(directory/'initial_result.json');result=check.read(directory/'output_result.json')
        check.checked_audit(p,initial);check.checked_audit(p,result);audits+=2
        assert result['info']['objective']<=initial['info']['objective']+1e-6
        assert result['info']['best_bound'] is None and not result['info']['proven_optimal']
        last=result
        if not (directory/'columns.json').exists():
            assert result['info']['route_pool_status']=='TIME_LIMIT_BUILD'
            check.close(result['info']['objective'],initial['info']['objective']);continue
        slots=check.read(directory/'slots.json');choices=check.read(directory/'columns.json')
        pool.validate_slots(p,initial,slots,extras);assert len(slots)==len(choices)
        for slot,rows in zip(slots,choices):
            if slot['events']:
                assert [tuple(k) for k in rows[0]['tasks']]==[(e['order_id'],e['kind']) for e in slot['events']]
            for column in rows:check.replay.replay_column(p,slot['satellite_id'],column);columns+=1
        models.append(pool.project_pool(p,initial,result,slots,choices,directory/'route_pool.mps'))
    return last



cases=[]
for item in summary:
    job=item['job'];payload=check.read(HERE/(job['id']+'.json'));out=payload['experiment']
    p=load_instance(*load_routing_case(plan['case'],use_pool_config=False));shortest=check.floyd(p)
    first,last=state['initial_result'],out['result'];a,b=check.checked_audit(p,first),check.checked_audit(p,last);audits+=2
    assert b['objective']<=a['objective']+1e-6 and payload['wall_seconds']<=plan['time_limit']+.5
    assert not out['proven_optimal'] and not out['proven_infeasible']
    baseline=distance_certificate(p);ref=out['reference_certificate'];components(ref,baseline)
    check.close(ref['raw_bound'],ref['e1_bound']+math.fsum(c['bound'] for c in ref['components']))
    lower=max(state['fallback_bound'],ref['bound']);dest=HERE/(job['id']+'_models')
    for number,row in enumerate(out['sites']):
        context=context_for(p,row['satellite'],shortest);leaf(p,row['certificate'],context,dest/f'site_{number}_pricing.mps')
        if job['id']=='candidate_fixed' and row['certificate'].get('refinement')=='fixed_duals':
            assert not row['certificate']['lp_converged']
            old=next(r for r in state['reference']['sites'] if r['satellite']==row['satellite'])
            prior=[w for w in old['iterations'] if w.get('phase')=='distance' and w.get('method')=='integer_pricing'][-1]
            assert prior['pricing']['optimal'] is False and not prior['pricing'].get('infeasible')
            for w in row['certificate']['iterations']:
                assert w['duals']==prior['duals'] and w['fleet_dual']==prior['fleet_dual']
    for number,row in enumerate(out['masters']):
        cert=row['certificate'];frame=deepcopy(ref)
        for c in frame['components']:c['bound']=row['site_bounds'][c['satellite']]
        if cert['raw_bound'] is not None:
            check.close(cert['bound'],max(lower,max(0,cert['raw_bound']-cert['margin'])))
            original_e1=Path(plan['origin']).parent/'baseline_subset25_models/reference/e1_inventory.mps'
            assert check.sha(original_e1)==origin_final['artifact_sha256'][str(original_e1.relative_to(Path(plan['origin']).parent))]
            models.append(joint.check_joint_conjunction(p,last,frame,cert,dest/f'master_{number}.mps.gz',original_e1))
        else:check.close(cert['bound'],lower)
        lower=max(lower,cert['bound'])
    pools(p,dest/'primal',plan['extra_slots_per_vehicle'])
    check.close(out['bound'],lower);check.close(out['upper_bound'],b['objective']);check.close(out['gap'],max(0,b['objective']-lower)/abs(b['objective']))
    assert lower<=b['objective']+1e-6 and out['target_reached']==(out['gap']<=.05)
    cases.append(dict(id=job['id'],upper=out['upper_bound'],lower=lower,gap_percent=100*out['gap'],wall_seconds=payload['wall_seconds'],site_bounds={r['satellite']:r['bound'] for r in ref['sites']},system_load_at_start=payload['system_load_at_start'],system_load_at_end=payload['system_load_at_end']))
    print('VERIFIED WARM DUAL CASE',cases[-1],flush=True)
models+=joint.models
out=dict(status='VERIFIED',case_results=cases,whole_cycle_audits=audits,replayed_columns=columns+check.capacity.columns,dual_repairs=check.capacity.repair_checks,model_checks=models,interpretation='One matched warm state and 180s budget, full original-domain proof replay. Not cold, not single-660s target attainment or population stability evidence.')
(HERE/'validation.json').write_text(json.dumps(out,ensure_ascii=False,indent=2,allow_nan=False)+'\n')
print('VERIFIED',len(models),'models',flush=True)
