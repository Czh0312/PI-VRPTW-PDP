"""Combine same-coefficient historical bounds only after the cold experiment."""
from copy import deepcopy
import json
from pathlib import Path
import sys

HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE/'candidate_source'));sys.path.insert(0,str(HERE))
import validation_core as check
import validation_conjunction as joint
from configuration.loader import load_routing_case
from models.routing.common.cycle import load_instance

registration=check.read(HERE/'best_projection_registration.json')
assert check.sha(Path(__file__))==registration['validator_sha256']
assert check.sha(HERE/'plan.json')==registration['pipeline_plan_sha256']
validation=HERE/'validation.json'
if not validation.exists(): validation=HERE/'validation_v2.json'
current=check.read(validation);assert current['status']=='VERIFIED'
previous_root=HERE.parents[2]/'model5_gap_round7_20261001'
previous_manifest=check.read(previous_root/'final_verification.json')
assert previous_manifest['status']=='VERIFIED'
assert check.sha(previous_root/'best_certified.json')==previous_manifest['artifacts_sha256']['best_certified.json']
previous=check.read(previous_root/'best_certified.json')
old=HERE.parents[2]/'model5_gap_round6_20261001'
manifest=check.read(old/'final_verification.json')
assert manifest['status']=='VERIFIED'
for name in ['validation_final.json','conjunction/result.json','conjunction/full_joint.mps.gz','inputs/previous.json','inputs/full_case.json']:
    assert check.sha(old/name)==manifest['artifacts_sha256'][name]
plan=check.read(HERE/'plan.json')
p=load_instance(*load_routing_case(plan['case'],use_pool_config=False))
q=load_instance(*load_routing_case(str(old/'inputs/full_case.json'),use_pool_config=False))
assert p.orders==q.orders and p.operating_policy==q.operating_policy and vars(p.fleet)==vars(q.fleet)
assert p.depot==q.depot and p.satellites==q.satellites
assert set(p.network.nodes)==set(q.network.nodes)
for name in p.network.nodes:
    assert vars(p.network.nodes[name])==vars(q.network.nodes[name])
    if hasattr(p.network.nodes[name],'service_time'):
        assert p.service(name)==q.service(name)
    for other in p.network.nodes:
        assert p.distance(name,other)==q.distance(name,other)
        assert p.travel(name,other)==q.travel(name,other)
assert p.batch_demands==q.batch_demands and p.demands==q.demands and p.weights==q.weights

cold=min(current['case_results'],key=lambda row:row['upper'])
payload=check.read(HERE/(cold['id']+'.json'))['experiment']
result=deepcopy(payload['result']);audit=check.checked_audit(p,result)
historical=next(row for row in check.read(old/'conjunction/result.json')['cases'] if row['label']=='full')
reference=next(row for row in check.read(old/'inputs/previous.json')['cases'] if row['label']=='full')['reference_certificate']
base=HERE.parents[2]/'model5_gap_round4_20261001/fresh/02_zhou_Set2_100-5-1b_full_43_models/reference/e1_inventory.mps'
original=check.read(HERE.parents[2]/'model5_gap_round4_20261001/final_verification.json')['artifact_sha256']
assert check.sha(base)==original['fresh/02_zhou_Set2_100-5-1b_full_43_models/reference/e1_inventory.mps']
projection=joint.check_joint_conjunction(p,result,reference,historical['joint_certificate'],
                                       old/'conjunction/full_joint.mps.gz',base)
assert cold['upper'] < previous['upper']
upper=cold['upper'];lower=max(cold['lower'],historical['lower'],previous['lower'])
assert cold['upper']<=historical['upper'] # Keep the projected newly found solution.
assert 0<=lower<=upper
gap=max(0,upper-lower)/abs(upper)
out=dict(status='VERIFIED',scope='Cross-run best certified record on identical full M5 coefficients',
         upper=upper,lower=lower,gap_percent=100*gap,target_reached=gap<=.05,
         previous_upper=previous['upper'],previous_lower=previous['lower'],previous_gap_percent=previous['gap_percent'],
         primal_source=cold['id'],current_pipeline_lower=cold['lower'],current_pipeline_gap_percent=cold['gap_percent'],
         physical_costs=audit['distances'],periodic_replay=audit['periodic_replay'],
         original_proof_sha256=check.sha(old/'validation_final.json'),
         historical_joint_mps_sha256=check.sha(old/'conjunction/full_joint.mps.gz'),
         current_pipeline_validation_sha256=check.sha(validation),same_coefficients_verified=True,
         historical_master_projection=projection,
         interpretation='Historical bound was not supplied to either matched fresh-initial pipeline. This is a cross-run record, not the pipeline gap or evidence of independent-start stability.')
(HERE/'best_certified.json').write_text(json.dumps(out,ensure_ascii=False,indent=2,allow_nan=False)+'\n')
print({k:out[k] for k in ['status','upper','lower','gap_percent','current_pipeline_gap_percent','physical_costs']},flush=True)
