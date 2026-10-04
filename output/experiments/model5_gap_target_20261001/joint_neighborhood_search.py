"""Exploratory audited E2 perturbations followed by E1/retiming optimization."""
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
import random
import sys
import time

OUT=Path(__file__).resolve().parent
ROOT=OUT.parents[2]
FROZEN=OUT/'diagnostic_60s/source_snapshot'
sys.path.insert(0,str(FROZEN))
from configuration.loader import load_routing_case
from models.routing.common.cycle import load_instance
from models.routing.common.cycle.audit import audit_solution
from models.routing.solvers.alns.solver import _move
from models.routing.solvers.alns.evaluator import evaluate
from models.routing.solvers.alns.neighborhoods import clone_state,state_from_result
from models.routing.variants.model_5.exact import reoptimize_e1

directory=OUT/'joint_50_tight_120s';directory.mkdir(exist_ok=False)
job=json.loads((OUT/'warm_60s_plus_polish_5s/manifest.json').read_text())['jobs'][1]
record=json.loads((OUT/'warm_60s_plus_polish_5s'/(job['id']+'.json')).read_text())
problem=load_instance(*load_routing_case(job['case']))
original=record['result'];best=deepcopy(original)
before=audit_solution(problem,best);assert before['valid'] and before['periodic_replay']['valid']
lower=record['inherited_global_lower_bound'];target=lower/.95
base_original=json.loads(Path(json.loads((OUT/'plan.json').read_text())['diagnostic_cases'][1]['initial_result']).read_text())
bases=[state_from_result(problem,base_original),state_from_result(problem,best)]
current=state_from_result(problem,best);current_cost=before['objective']
rng=random.Random(42);started=time.perf_counter();deadline=started+120
manifest=dict(case=job['case'],seed=42,time_limit=120,polish_per_candidate_limit=2,
    lower_bound=lower,target_upper_bound=target,initial_upper=before['objective'],
    source_snapshot=str(FROZEN),script_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
    scope='Exploratory 50-customer tight-pool B3 closest-to-target case. No default algorithm adoption or generalization. Every accepted result must pass full independent audit. Extra budget; not a paired solver benchmark.')
(directory/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
history=[dict(time=0,upper=before['objective'])];trials=[]
while time.perf_counter()<deadline and best['info']['objective']>target+1e-8:
    operator=rng.choice(['pair','relocate','reverse','split_merge','vehicle'])
    proposal=_move(problem,current,rng,operator)
    # Keep either of two feasible E1 route organizations as decoder proposals.
    # Actual supply/return timing is reoptimized only after the full decode.
    if rng.random()<.5:
        base=rng.choice(bases)
        for field in ('deliveries','pickup_order','pickup_vehicle'):
            proposal[field]=deepcopy(base[field])
    for route in proposal['e2']:
        route.pop('depart_after',None);route.pop('return_after',None)
    _,decoded,issues=evaluate(problem,proposal)
    trial=dict(operator=operator,decoded_valid=not issues,issues=sorted(set(issues)))
    if not issues:
        audit=audit_solution(problem,decoded)
        assert audit['valid'] and audit['periodic_replay']['valid']
        remaining=deadline-time.perf_counter()
        if remaining<=0:break
        polished=reoptimize_e1(problem,decoded,time_limit=min(2,remaining),retime_e2=True,seed=42)
        polished=json.loads(json.dumps(polished));a=audit_solution(problem,polished)
        assert a['valid'] and a['periodic_replay']['valid'] and a['objective']>=lower-1e-6
        trial.update(decoded_upper=audit['objective'],polished_upper=a['objective'])
        if a['objective']<best['info']['objective']-1e-6:
            best=polished;history.append(dict(time=time.perf_counter()-started,upper=a['objective']))
            (directory/'best_result.json').write_text(json.dumps(best,ensure_ascii=False,indent=2)+'\n')
            print('Improved',a['objective'],'GAP',100*(a['objective']-lower)/a['objective'],flush=True)
        temperature=max(.01,10*(1-min(1,(time.perf_counter()-started)/120)))
        if a['objective']<=current_cost or rng.random()<math.exp(min(0,(current_cost-a['objective'])/temperature)):
            current=clone_state(proposal);current_cost=a['objective']
    trials.append(trial)
audit=audit_solution(problem,best);assert audit['valid'] and audit['periodic_replay']['valid']
gap=100*(audit['objective']-lower)/audit['objective']
payload=dict(initial_audit=before,result=best,independent_audit=audit,lower_bound=lower,
    gap_percent=gap,meets_five_percent=gap<=5,history=history,trials=trials,
    wall_seconds=time.perf_counter()-started)
(directory/'result.json').write_text(json.dumps(payload,ensure_ascii=False,indent=2)+'\n')
print('Final',audit['objective'],'GAP',gap,'trials',len(trials),flush=True)
