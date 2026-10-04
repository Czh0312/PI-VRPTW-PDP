"""Run baseline verification once, then validate the separately frozen retry."""
from copy import deepcopy
import json,math
from pathlib import Path
import sys
HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE))
import validate as baseline
check=baseline.check
DEST=HERE/'retry';plan=check.read(DEST/'plan.json');output=check.read(DEST/'result.json')
for f,d in plan['source_sha256'].items():assert check.sha(DEST/'source_snapshot'/f)==d
for f,d in plan['input_sha256'].items():assert check.sha(Path(f))==d
assert check.sha(HERE/'retry.py')==plan['runner_sha256']
initial=check.read(HERE/'e2.json');p=baseline.problems['full'];shortest=baseline.shortest['full']
row=next(r for r in initial['reference']['sites'] if r['satellite']=='S1')
models=[];audits=0
for pilot in check.read(DEST/'pilots.json'):
    witness=deepcopy(row['iterations'][pilot['witness_index']]);report=pilot['report']
    witness['pricing']={k:v for k,v in report.items() if k not in ('column','columns')}
    witness['new_column']=report['column'];witness['new_columns']=report['columns']
    witness.pop('dual_repair',None)
    prices={tuple(k):v for k,v in witness['duals']}
    term=math.fsum(count*price for _,count,price in witness.get('delivery_counts',[]))
    term+=math.fsum(count*price for _,_,count,price in witness.get('subset_counts',[]))
    standard=math.fsum(prices.values())+witness['maximum_trips']*witness['fleet_dual']+term
    standard+=witness['maximum_trips']*min(0,report['lower_bound'])
    witness['standard_candidate_bound']=standard
    witness['candidate_bound']=max(standard,check.capacity.repair_independent(p,'S1',witness)['bound'])
    proof=dict(satellite='S1',bound=max(0,witness['candidate_bound']),best_witness=0,
               iterations=[witness],columns=report['columns'])
    ctx=check.independent_context(p,'S1',shortest);check.check_site(p,proof,ctx)
    baseline.verify_pricing_models(p,proof,ctx,DEST/'pilot_models'/f'w{pilot["witness_index"]}_reuse{int(pilot["reuse_budget"])}.mps')
for run in output['runs']:
    ctx=check.independent_context(p,run['satellite'],shortest)
    check.check_site(p,run['certificate'],ctx)
    baseline.verify_pricing_models(p,run['certificate'],ctx,DEST/'e2_models'/f'{run["satellite"]}_pricing.mps')
cases=[]
for payload in output['cases']:
    label=payload['label'];p=baseline.problems[label];ref=payload['reference_certificate']
    previous=check.read(HERE/(label+'.json'))
    assert payload['result']==previous['result']
    audit=check.checked_audit(p,payload['result']);audits+=1
    for row in ref['sites']:check.check_site(p,row,check.independent_context(p,row['satellite'],baseline.shortest[label]))
    for leaf in payload['conditions']:
        check.check_site(p,leaf['certificate'],check.independent_context(p,leaf['site'],baseline.shortest[label],leaf))
    check.close(ref['raw_bound'],ref['e1_bound']+math.fsum(c['bound'] for c in ref['components']))
    for c in ref['components']:
        prior=next(x for x in previous['reference_certificate']['components'] if x['satellite']==c['satellite'])
        assert c['bound']>=prior['bound']
    old=HERE.parent/'model5_gap_round4_20261001/fresh'/('0'+('2' if label=='full' else '3')+'_zhou_Set2_100-5-1b_'+label+'_43_models')/'reference/e1_inventory.mps'
    models.append(check.check_joint(p,payload['result'],ref,payload['joint_certificate'],DEST/(label+'_joint.mps.gz'),old))
    lower=max(previous['bound'],ref['bound'],payload['joint_certificate']['bound'])
    check.close(payload['bound'],lower);check.close(payload['upper_bound'],audit['objective'])
    check.close(payload['gap'],max(0,audit['objective']-lower)/max(1e-9,abs(audit['objective'])))
    assert lower<=audit['objective']+1e-6 and payload['target_reached']==(payload['gap']<=.05)
    cases.append(dict(label=label,upper=payload['upper_bound'],lower=lower,gap_percent=100*payload['gap'],target_reached=payload['target_reached']))
assert output['e2_seconds']<=plan['certificate_seconds']+.5
assert output['wall_seconds']<=plan['certificate_seconds']+2*plan['joint_seconds_per_pool']+1
result=dict(status='VERIFIED',cases=cases,whole_cycle_audits=baseline.audits+audits,
    model_checks=baseline.models+models,pricing_columns=check.capacity.columns,
    extra_columns=baseline.columns,repaired_dual_checks=check.capacity.repair_checks,
    proof='Independently replays all columns, dual repairs, frozen original-domain pricing MPS, complete E1 pattern matrices, and full schedule projections.')
(HERE/'validation_final.json').write_text(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False)+'\n')
print({k:v for k,v in result.items() if k not in ('model_checks','proof')},flush=True)
