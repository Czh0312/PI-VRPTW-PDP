"""Verify optional-slot controls once, then the conjunctive global proof."""
from copy import deepcopy
import json,math
from pathlib import Path
import sys
HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE))
import validate as primal
import validation_conjunction as conjunction
check=primal.check;DEST=HERE/'conjunction'
plan=check.read(DEST/'plan.json');result=check.read(DEST/'result.json')
for f,d in plan['source_sha256'].items():assert check.sha(DEST/'source_snapshot'/f)==d
for f,d in plan['input_sha256'].items():assert check.sha(Path(f))==d
assert check.sha(Path(plan['prior_conditional_path']))==plan['prior_conditional_sha256']
assert check.sha(HERE/'conjunction.py')==plan['runner_sha256']
from configuration.loader import load_routing_case
from models.routing.common.cycle import load_instance
def independent_context(p):
    shortest=check.floyd(p);context=check.independent_context(p,plan['site'],shortest)
    release=dict(context['release'])
    for condition in plan['supply_conditions']:
        bid=condition['batch'];release[bid]=max(release[bid],condition['threshold']+p.operating_policy.e2_dispatch_prep_time)
    nodes=[]
    for original in context['nodes']:
        order=original['order'];d=max(order.delivery_window[0],release[order.batch]+shortest[plan['site'],order.customer])
        low=d if original['key'][1]=='d' else max(order.pickup_window[0],d+order.service_time+order.min_use_time)
        nodes.append({**original,'window':(low,original['window'][1])})
    return {**context,'release':release,'nodes':nodes,'lookup':{n['key']:n for n in nodes}}
p=load_instance(*load_routing_case(str(HERE/'inputs/full_case.json'),use_pool_config=False))
q=load_instance(*load_routing_case(str(HERE/'inputs/tight_case.json'),use_pool_config=False))
context=independent_context(p);assert context==independent_context(q)
assert (p.fleet.e2_weight_cap,p.fleet.e2_volume_cap)==(q.fleet.e2_weight_cap,q.fleet.e2_volume_cap)
for field in ('cycle_start','cycle_end','e2_turnaround_time','e2_return_unload_time'):
    assert getattr(p.operating_policy,field)==getattr(q.operating_policy,field)
assert p.operating_policy.batches[-1].cutoff==q.operating_policy.batches[-1].cutoff
positions=[plan['site']]+[n['order'].customer for n in context['nodes']]
for a in positions:
    for b in positions:assert p.distance(a,b)==q.distance(a,b) and p.travel(a,b)==q.travel(a,b)
leaf=result['leaf'];check.check_site(p,leaf,context)
conjunction.verify_pricing_models(p,leaf,context,DEST/'pricing.mps')
audits=primal.audits;models=primal.models+conjunction.models;records=[]
for payload in result['cases']:
    label=payload['label'];p=load_instance(*load_routing_case(str(HERE/'inputs'/(label+'_case.json')),use_pool_config=False))
    check.floyd(p);audit=check.checked_audit(p,payload['result']);audits+=1
    previous=next(c for c in primal.previous if c['label']==label)
    base=HERE.parent/'model5_gap_round4_20261001/fresh'/('0'+('2' if label=='full' else '3')+'_zhou_Set2_100-5-1b_'+label+'_43_models')/'reference/e1_inventory.mps'
    cert=payload['joint_certificate']
    models.append(conjunction.check_joint_conjunction(p,payload['result'],previous['reference_certificate'],cert,
        DEST/(label+'_joint.mps.gz'),base))
    check.close(payload['upper'],audit['objective'])
    check.close(payload['lower'],max(previous['bound'],cert['bound']))
    check.close(payload['gap'],max(0,payload['upper']-payload['lower'])/abs(payload['upper']))
    assert payload['target_reached']==(payload['gap']<=.05) and payload['lower']<=payload['upper']+1e-6
    rows=payload['conditions'];assert rows[-1]['supply_conditions']==plan['supply_conditions']
    check.close(rows[-1]['bound'],leaf['bound'])
    assert cert['proven_optimal'] is False and cert['proven_infeasible'] is False
    records.append(dict(label=label,upper=payload['upper'],lower=payload['lower'],gap_percent=100*payload['gap'],target_reached=payload['target_reached']))
assert leaf['runtime']<=plan['pricing_seconds']+.5
assert result['wall_seconds']<=plan['pricing_seconds']+2*plan['joint_seconds_per_pool']+1
out=dict(status='VERIFIED',target='REACHED_FOR_BOTH_DEVELOPMENT_CONFIGURATIONS',cases=records,
    whole_cycle_audits=audits,independently_replayed_columns=primal.columns+check.capacity.columns,
    repaired_dual_checks=check.capacity.repair_checks,model_checks=models,
    prior_proof_sha256=check.sha(primal.OLD/'validation_final.json'),
    interpretation='Additional warm-started development budgets, no cold-start or universal stability claim.')
(HERE/'validation_final.json').write_text(json.dumps(out,ensure_ascii=False,indent=2,allow_nan=False)+'\n')
print({k:v for k,v in out.items() if k!='model_checks'},flush=True)
