"""Independent full-domain replay of matched fresh shared-initial pipelines."""
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
for binding in plan['input_bindings']:
    assert check.sha(Path(binding['origin']))==check.sha(Path(binding['frozen']))==binding['sha256']
base=plan['source_sha256']['baseline_source'];candidate=plan['source_sha256']['candidate_source']
assert set(base)==set(candidate)
assert {name for name in base if base[name]!=candidate[name]}==set(plan['declared_source_changes'])
fresh=check.read(HERE/'initial.json');manifest=check.read(HERE/'initial_manifest.json')
assert manifest['frozen_before_paired_jobs'] and manifest['initializer']==plan['initializer']==fresh['initializer']
assert check.sha(HERE/'initial.json')==manifest['sha256'] and fresh['wall_seconds']<=15.5
summary=check.read(HERE/'summary.json')
assert [r['job'] for r in summary]==plan['jobs'] and not any(r['failure'] for r in summary)
models=[];audits=columns=0;cases=[];selection_checks=0



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


for summary in check.read(HERE/'summary.json'):
    if summary['failure']:
        cases.append(dict(id=summary['job']['id'],failure=summary['failure']));continue
    job=summary['job'];payload=check.read(HERE/(job['id']+'.json'));out=payload['experiment']
    p=load_instance(*load_routing_case(plan['case'],use_pool_config=False));shortest=check.floyd(p)
    first,last=out['initial_result'],out['result'];a,b=check.checked_audit(p,first),check.checked_audit(p,last);audits+=2
    assert b['objective']<=a['objective']+1e-6
    assert out['initial_result']==fresh['result']
    check.close(payload['charged_initial_wall_seconds'],fresh['wall_seconds'])
    check.close(payload['actual_hybrid_time_limit'],660-fresh['wall_seconds'])
    check.close(payload['pipeline_wall_seconds'],payload['wall_seconds']+fresh['wall_seconds'])
    assert payload['pipeline_wall_seconds']<=660.5
    assert not out['proven_optimal'] and not out['proven_infeasible']
    dest=HERE/(job['id']+'_models');extras=job['options'].get('extra_slots_per_vehicle',0)
    conditional_seed=first
    for parent in sorted(dest.glob('primal_*')):
        projected=pools(p,parent,extras)
        if projected:conditional_seed=projected
    ref=out['reference_certificate'];baseline=distance_certificate(p)
    assert ref['distance_certificate']==baseline
    models.append(check.verify_e1(p,last,ref['e1_certificate'],dest/'reference/e1_inventory.mps'))
    for index,row in enumerate(ref['sites']):
        context=context_for(p,row['satellite'],shortest);assert context==route_context(p,row['satellite'])
        # A refined site may originate in a subset run. Validate every original
        # export against its own witness, then check the selected site below.
        path=dest/'reference'/f'site_{index}_best_pricing.mps'
        original=next((r for r in ref.get('subset_runs',[]) if r['satellite']==row['satellite'] and r['certificate']==row),None)
        if original:
            check.check_site(p,row,context)
        else:leaf(p,row,context,path)
    for number,row in enumerate(ref.get('subset_runs',[])):
        assert not row['restricted_objective_used_as_bound']
        leaf(p,row['certificate'],context_for(p,row['satellite'],shortest),dest/f'subsets_{number}_pricing.mps')
    check.close(ref['raw_bound'],ref['e1_bound']+math.fsum(c['bound'] for c in ref['components']))
    components(ref,baseline)
    assert ref['bound']>=baseline['bound']-1e-6
    check.close(ref['bound'],max(baseline['bound'],max(0,ref['raw_bound']-1e-7*max(1,abs(ref['raw_bound'])))))
    cond=out['conditional_certificate'];lower=ref['bound']
    if cond:
        for number,row in enumerate(cond['conditions'],1):
            leaf(p,row['certificate'],context_for(p,row['site'],shortest,row),dest/'conditional'/f'condition_{number}_pricing.mps')
        for number,cert in enumerate(cond['masters']):
            if cert['raw_bound'] is not None:
                check.close(cert['bound'],max(lower,max(0,cert['raw_bound']-cert['margin'])))
                models.append(joint.check_joint_conjunction(p,last,ref,cert,
                    dest/'conditional'/f'master_{number}.mps.gz',dest/'reference/e1_inventory.mps'))
            else:check.close(cert['bound'],lower)
            lower=max(lower,cert['bound'])
        check.close(cond['bound'],lower)
        conjunctions=[row for row in cond['conditions'] if 'supply_conditions' in row]
        assert len(conjunctions)<=job['options'].get('max_supply_conjunctions',0)
        assert len(cond['conditions'])<=job['options']['max_conditions']
        # Independently reproduce the pair choice from this run's first E1
        # solution and its physical incumbent, with no hard-coded site/time.
        if conjunctions:
            incumbent={(e['site_id'],t['batch_id']):e['start']+p.service(e['site_id'])
                       for t in conditional_seed['e1_trips'] for e in t['events'] if e['kind']=='d'}
            floors={c['satellite']:c['bound'] for c in ref['components']};groups={}
            for supply in cond['masters'][0]['supplies']:
                site,bid,threshold=supply['site'],supply['batch'],supply['supply']
                unconditional=context_for(p,site,shortest)['release'][bid]-p.operating_policy.e2_dispatch_prep_time
                if threshold>unconditional:
                    groups.setdefault(site,{})[bid]=(threshold,max(0,threshold-incumbent.get((site,bid),threshold)))
            order={b.id:i for i,b in enumerate(p.operating_policy.batches)};ranked=[]
            for site,batches in groups.items():
                if len(batches)<2:continue
                pair=sorted(batches,key=lambda bid:(-batches[bid][1],order[bid]))[:2]
                conditions=[dict(batch=bid,threshold=batches[bid][0]) for bid in sorted(pair,key=order.get)]
                ranked.append((max(1,sum(batches[bid][1] for bid in pair))*floors[site],site,conditions))
            ranked.sort(key=lambda row:(-row[0],row[1]))
            for row,(_,site,conditions) in zip(conjunctions,ranked):
                assert row['site']==site and row['supply_conditions']==conditions;selection_checks+=1
    adaptive=out.get('adaptive_certificate')
    if adaptive:
        refined=adaptive['reference_certificate']
        components(refined,baseline)
        check.close(refined['raw_bound'],refined['e1_bound']+math.fsum(c['bound'] for c in refined['components']))
        for number,row in enumerate(adaptive['sites']):
            leaf(p,row['certificate'],context_for(p,row['satellite'],shortest),dest/'adaptive'/f'site_{number}_pricing.mps')
        for row in refined['sites']:check.check_site(p,row,context_for(p,row['satellite'],shortest))
        lower=max(lower,refined['bound'])
        for number,row in enumerate(adaptive['masters']):
            cert=row['certificate'];frame=deepcopy(refined)
            for c in frame['components']:c['bound']=row['site_bounds'][c['satellite']]
            if cert['raw_bound'] is not None:
                check.close(cert['bound'],max(lower,max(0,cert['raw_bound']-cert['margin'])))
                models.append(joint.check_joint_conjunction(p,last,frame,cert,
                    dest/'adaptive'/f'master_{number}.mps.gz',dest/'reference/e1_inventory.mps'))
            else:check.close(cert['bound'],lower)
            lower=max(lower,cert['bound'])
        pools(p,dest/'adaptive/primal',extras)
        check.close(adaptive['bound'],lower)
    check.close(out['bound'],lower);check.close(out['upper_bound'],b['objective'])
    check.close(out['gap'],max(0,out['upper_bound']-lower)/abs(out['upper_bound']))
    assert out['target_reached']==(out['gap']<=job['options']['target_gap']) and lower<=out['upper_bound']+1e-6
    cases.append(dict(id=job['id'],initial_upper=a['objective'],upper=out['upper_bound'],lower=lower,
                      gap_percent=100*out['gap'],target_reached=out['target_reached'],wall_seconds=payload['wall_seconds'],pipeline_wall_seconds=payload['pipeline_wall_seconds'],
                      automatic_conjunctions=len(conjunctions) if cond else 0))
    print('VERIFIED CASE',cases[-1],flush=True)

models+=joint.models
out=dict(status='VERIFIED',case_results=cases,whole_cycle_audits=audits,
         independently_replayed_columns=columns+check.capacity.columns,repaired_dual_checks=check.capacity.repair_checks,
         model_checks=models,automatic_selection_checks=selection_checks,
         interpretation='Fresh ALNS common incumbent charged to each matched 660s pipeline; sequential one-seed budget comparison, not independent cold starts or population stability evidence. Historical best remains separate.')
(HERE/'validation.json').write_text(json.dumps(out,ensure_ascii=False,indent=2,allow_nan=False)+'\n')
print({k:v for k,v in out.items() if k!='model_checks'},flush=True)
