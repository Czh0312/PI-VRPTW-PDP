"""Independent contexts/arithmetic, frozen MPS replay, and whole M5 schedules."""
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
import sys

HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE/'source_snapshot'))
sys.path.insert(0,str(HERE))
import validation_core as check
from configuration.loader import load_routing_case
from models.routing.common.cycle import load_instance
from models.routing.solvers.exact.multibatch_route_pricing import RoutePricer, route_context
import gurobipy as gp

plan=check.read(HERE/'plan.json')
for name,digest in plan['source_sha256'].items(): assert check.sha(HERE/'source_snapshot'/name)==digest
for item in plan['inputs']:
    assert check.sha(HERE/item['file'])==item['sha256']
    assert check.sha(Path(item['source']))==item['source_sha256']
for name,digest in check.read(HERE/'validation_sources.json').items(): assert check.sha(Path(name))==digest
assert check.sha(HERE/'run_v1.py')==plan['runner_sha256']
assert check.sha(HERE/'run.py')==check.read(HERE/'amendment.json')['runner_sha256']
models=[]; audits=columns=0
problems={label:load_instance(*load_routing_case(str(HERE/'inputs'/label/'case.json'),use_pool_config=False))
          for label in plan['cases']}
shortest={label:check.floyd(p) for label,p in problems.items()}
p,q=problems['full'],problems['tight']
for site in p.satellites:
    context=check.independent_context(p,site,shortest['full'])
    assert context==check.independent_context(q,site,shortest['tight'])==route_context(p,site)
    assert (p.fleet.e2_weight_cap,p.fleet.e2_volume_cap)==(q.fleet.e2_weight_cap,q.fleet.e2_volume_cap)
    for field in ('cycle_start','cycle_end','e2_return_unload_time','e2_turnaround_time'):
        assert getattr(p.operating_policy,field)==getattr(q.operating_policy,field)
    assert p.operating_policy.batches[-1].cutoff==q.operating_policy.batches[-1].cutoff
    positions=[site]+[n['order'].customer for n in context['nodes']]
    for a in positions:
        for b in positions: assert p.distance(a,b)==q.distance(a,b) and p.travel(a,b)==q.travel(a,b)

def verify_pricing_models(p,row,context,path):
    if row['best_witness'] is None:return
    witness=row['iterations'][row['best_witness']]
    subset={sid:frozenset(tuple(k) for k in keys) for sid,keys,_,_ in witness.get('subset_counts',[])}
    built=RoutePricer(p,context,subset_keys=subset)
    pi={tuple(k):v for k,v in witness['duals']}
    nu=witness['fleet_dual'];subset_prices={sid:v for sid,_,_,v in witness.get('subset_counts',[])}
    prices={bid:v for bid,_,v in witness.get('delivery_counts',[])}
    try:
        for number,(category,pricer) in enumerate(built.groups):
            pricer.set_prices(pi,nu,False,subset_prices);pricer.model.update()
            file=path.with_name(path.stem+f'_group_{number}'+path.suffix)
            model=gp.read(str(file)); original=pricer.model
            try:
                assert model.NumVars==original.NumVars and model.NumConstrs==original.NumConstrs
                assert model.NumGenConstrs==original.NumGenConstrs
                for a,b in zip(original.getVars(),model.getVars()):
                    assert a.VarName==b.VarName and a.VType==b.VType
                    check.close(a.LB,b.LB);check.close(a.UB,b.UB);check.close(a.Obj,b.Obj)
                check.close(original.ObjCon,model.ObjCon)
                for a,b in zip(original.getConstrs(),model.getConstrs()):
                    assert a.Sense==b.Sense
                    check.close(a.RHS,b.RHS);check.same_expr(original.getRow(a),model.getRow(b))
                for a,b in zip(original.getGenConstrs(),model.getGenConstrs()):
                    av,al,ae,ass,ar=original.getGenConstrIndicator(a)
                    bv,bl,be,bs,br=model.getGenConstrIndicator(b)
                    assert av.VarName==bv.VarName and al==bl and ass==bs
                    check.close(ar,br);check.same_expr(ae,be)
                model.Params.OutputFlag=0;model.Params.Threads=1;model.Params.Seed=43
                model.Params.MIPGap=model.Params.MIPGapAbs=0;model.Params.TimeLimit=30
                model.Params.FeasibilityTol=model.Params.IntFeasTol=1e-9
                model.Params.IntegralityFocus=1;model.Params.DualReductions=0
                model.optimize()
                report=next(g for g in witness['pricing']['groups'] if g['category']==category)
                if report['infeasible']: assert model.Status==gp.GRB.INFEASIBLE
                elif report['raw_lower_bound'] is not None:
                    assert model.ObjBound-prices.get(category,0)>=report['raw_lower_bound']-1e-5
                models.append(dict(path=str(file),full_domain_matrix_equal=True,category=category,
                    reoptimized_bound=model.ObjBound-prices.get(category,0) if not report['infeasible'] else None))
            finally:model.dispose()
    finally:built.dispose()

shared=check.read(HERE/'e2.json')
assert shared['wall_seconds']<=plan['E2_total_seconds']+.5
for run in shared['runs']:
    site=run['satellite'];ctx=check.independent_context(p,site,shortest['full'])
    row=run['certificate'];check.check_site(p,row,ctx)
    verify_pricing_models(p,row,ctx,HERE/'e2_models'/f'{site}_pricing.mps')
for site,rows in check.read(HERE/'seed_columns.json').items():
    for col in rows:check.replay.replay_column(p,site,col);columns+=1

cases=[]
for label,p in problems.items():
    payload=check.read(HERE/(label+'.json'));ref=payload['reference_certificate']
    previous=check.read(HERE/'inputs'/label/'previous.json')
    initial=check.checked_audit(p,previous['result']);audit=check.checked_audit(p,payload['result']);audits+=2
    assert audit['objective']<=initial['objective']+1e-6
    for row in ref['sites']:check.check_site(p,row,check.independent_context(p,row['satellite'],shortest[label]))
    for component in ref['components']:
        shared_component=next(c for c in shared['reference']['components'] if c['satellite']==component['satellite'])
        check.close(component['bound'],shared_component['bound'])
    check.close(ref['raw_bound'],ref['e1_bound']+math.fsum(c['bound'] for c in ref['components']))
    check.close(ref['bound'],max(previous['reference_certificate']['bound'],ref['raw_bound']-1e-7*max(1,abs(ref['raw_bound']))))
    base_path=HERE.parent/'model5_gap_round4_20261001/fresh'/('0'+('2' if label=='full' else '3')+'_zhou_Set2_100-5-1b_'+label+'_43_models')/'reference/e1_inventory.mps'
    models.append(check.verify_e1(p,payload['result'],ref['e1_certificate'],base_path))
    for leaf in payload['previous_conditions']:
        check.check_site(p,leaf['certificate'],check.independent_context(p,leaf['site'],shortest[label],leaf))
    lower=max(previous['bound'],ref['bound'])
    cert=payload['joint_certificate']
    models.append(check.check_joint(p,payload['result'],ref,cert,HERE/(label+'_models')/'joint.mps.gz',base_path))
    lower=max(lower,cert['bound'])
    cond=payload['conditional_certificate']
    if cond:
        for number,leaf in enumerate(cond['conditions'],1):
            ctx=check.independent_context(p,leaf['site'],shortest[label],leaf)
            check.check_site(p,leaf['certificate'],ctx)
            verify_pricing_models(p,leaf['certificate'],ctx,HERE/(label+'_models')/'conditional'/f'condition_{number}_pricing.mps')
        for number,master in enumerate(cond['masters']):
            if master['raw_bound'] is not None:
                models.append(check.check_joint(p,payload['result'],ref,master,
                    HERE/(label+'_models')/'conditional'/f'master_{number}.mps.gz',base_path))
        lower=max(lower,cond['bound'])
    for directory in sorted((HERE/(label+'_models')/'primal').glob('pass_*')):
        original=check.read(directory/'initial_result.json');output=check.read(directory/'output_result.json')
        check.checked_audit(p,original);check.checked_audit(p,output);audits+=2
        assert output['info']['best_bound'] is None and not output['info']['proven_optimal']
        if not (directory/'columns.json').exists():
            assert output['info']['route_pool_status']=='TIME_LIMIT_BUILD'
            check.close(output['info']['objective'],original['info']['objective']);continue
        for trip,pool in zip(original['e2_trips'],check.read(directory/'columns.json')):
            assert any([tuple(k) for k in c['tasks']]==[(e['order_id'],e['kind']) for e in trip['events']] for c in pool)
            for c in pool:check.replay.replay_column(p,trip['satellite_id'],c);columns+=1
    check.close(payload['bound'],lower);check.close(payload['upper_bound'],audit['objective'])
    check.close(payload['gap'],max(0,audit['objective']-lower)/max(1e-9,abs(audit['objective'])))
    assert payload['target_reached']==(payload['gap']<=plan['target_gap'])
    assert payload['wall_seconds']<=plan['joint_seconds']+plan['conditional_seconds_per_failed_pool']+plan['primal_seconds_per_failed_pool']+1
    cases.append(dict(label=label,upper=payload['upper_bound'],lower=lower,gap_percent=100*payload['gap'],
        target_reached=payload['target_reached'],wall_seconds=payload['wall_seconds']))
result=dict(status='VERIFIED',cases=cases,whole_cycle_audits=audits,model_checks=models,
    pricing_columns=check.capacity.columns,extra_columns=columns,repaired_dual_checks=check.capacity.repair_checks)
(HERE/'validation.json').write_text(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False)+'\n')
print({k:v for k,v in result.items() if k not in ('cases','model_checks')},flush=True)
