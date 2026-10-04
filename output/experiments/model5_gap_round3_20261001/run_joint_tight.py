"""Same conditional E2 floors, pool-specific E1 inventory for the tight case."""
from pathlib import Path
import ast,hashlib,json,math,sys,time
HERE=Path(__file__).resolve().parent;sys.path.insert(0,str(HERE/'subset_source_snapshot'))
from configuration.loader import load_routing_case
from models.routing.common.cycle import load_instance
from models.routing.solvers.exact.multibatch_e1_routes import build_e1_routes,set_route_projection
import gurobipy as gp

def read(p):return json.loads(Path(p).read_text())
def write(p,v):p.write_text(json.dumps(v,indent=2,ensure_ascii=False,allow_nan=False)+'\n')
source=HERE/'run_joint_conditional.py';tree=ast.parse(source.read_text())
function=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='joint')
exec(compile(ast.Module(body=[function],type_ignores=[]),str(source),'exec'))
base=read(HERE/'recombine_120s/100_tight.json');p=load_instance(*load_routing_case(base['job']['case']))
conditions=[read(HERE/'joint_conditional/S3_b1.json')]
params=read(HERE/'joint_conditional/manifest.json');dest=HERE/'joint_conditional/master_tight';dest.mkdir(exist_ok=False)
write(dest/'manifest.json',dict(case=base['job']['case'],initial_result=str(HERE/'recombine_120s/100_tight.json'),
    time_limit=30,conditions=[str(HERE/'joint_conditional/S3_b1.json')],registered_before_run=True,
    runner_sha256=hashlib.sha256(source.read_bytes()).hexdigest()))
cert=joint(p,conditions,base['result'],params['baselines'],dest)
write(dest/'certificate.json',cert)
print(dict(bound=cert['bound'],gap=100*(base['result']['info']['objective']-cert['bound'])/base['result']['info']['objective']),flush=True)
