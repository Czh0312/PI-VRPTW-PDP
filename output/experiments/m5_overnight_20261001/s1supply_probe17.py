from pathlib import Path
import json,os,sys,hashlib
HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE/'source_snapshot_ceiling14'))
from configuration.loader import load_routing_case
from models.routing.common.cycle import load_instance
from models.routing.solvers.exact.multibatch_physical_site import physical_site_certificate
import gurobipy as gp
OUT=HERE/'s1supply17'
def write(path,value):path.write_text(json.dumps(value,indent=2,allow_nan=False)+'\n')
def checkpoint(status,**extra):
    c=json.loads((HERE/'checkpoint.json').read_text());c.update(status=status,active_process=os.getpid(),**extra)
    write(HERE/'checkpoint.json',c)
for path,digest in json.loads((HERE/'manifest_s1supply17_input.json').read_text())['sha256'].items():
    assert hashlib.sha256(Path(path).read_bytes()).hexdigest()==digest
problem=load_instance(*load_routing_case(str(HERE/'inputs/case.json')))
groups=json.loads((HERE/'s1full16/groups.json').read_text())
previous=json.loads((HERE/'s1full16/independent_validation.json').read_text())
assert previous['replays']['unconditional']['status']==gp.GRB.OPTIMAL
floor=json.loads((HERE/'s1full16/unconditional.json').read_text())['bound']
summary=[]
for name,thresholds in (('supply_b3',{'b3':59893}),('all_supplies',{'b1':31093,'b2':45493,'b3':59893})):
    checkpoint('running_s1supply17_'+name)
    parameters=dict(time_limit=30,seed=42,supply_thresholds=thresholds,maximum_returns={'b2':10},
        trip_groups=groups,partial_groups=False,initial_result=None,compress_routes=True,fallback_bound=floor)
    write(OUT/(name+'_parameters.json'),parameters)
    result=physical_site_certificate(problem,'S1',verbose=True,diagnostics_path=OUT/(name+'.mps.gz'),**parameters)
    result.update(original_business_input_unchanged=True,externally_certified_conditional_floor_source=str(HERE/'s1full16/unconditional.json'))
    write(OUT/(name+'.json'),result)
    summary.append(dict(name=name,status=result['status'],bound=result['bound'],raw_bound=result['raw_bound'],
        objective=result['subproblem_objective'],subproblem_infeasible=result['subproblem_infeasible'],runtime=result['runtime']))
    write(OUT/'summary.json',summary)
checkpoint('s1supply17_complete',active_process=None,next_action='Read supply17 results; check changed supply bounds and conditional floor against independently verified s1full16 max10 MPS, then replay new certificates beforeglobaluse.')
print(json.dumps(summary,indent=2))
