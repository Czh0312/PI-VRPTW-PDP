"""Proof-generating columns become primal candidates, never primal bounds."""
from pathlib import Path
import hashlib,json,sys,time
HERE=Path(__file__).resolve().parent;OLD=HERE.parent/'model5_gap_round2_20261001'
sys.path.insert(0,str(HERE/'final_source_snapshot'))
from configuration.loader import load_routing_case
from models.routing.common.cycle import load_instance
from models.routing.solvers.exact import multibatch_route_pool as pool
from models.routing.solvers.exact.multibatch_route_pricing import route_context,evaluate_column
from models.routing.common.cycle.audit import audit_solution
DEST=HERE/'global_columns_120s';DEST.mkdir(exist_ok=False)
def read(p):return json.loads(Path(p).read_text())
def write(p,v):p.write_text(json.dumps(v,indent=2,ensure_ascii=False,allow_nan=False)+'\n')
base=read(HERE/'recombine_120s/100_full.json');p=load_instance(*load_routing_case(base['job']['case']))
extra={s:[] for s in p.satellites};sources=[HERE/'resume_cg'/f'{s}.json' for s in ('S1','S4')]
for path in sources:
 d=read(path)['site'];extra[d['satellite']]+=d['columns']
old=read(OLD/'100_full.json')['certificate']
for site in old['sites']:
 if not extra[site['satellite']]:extra[site['satellite']]+=site['columns']
# Preserve per-slot original paths and a bounded standard neighborhood.
# Add 128 globally cheap columns per slot. Every sequence is reconstructed
# against the full current context; whole-cycle feasibility belongs to MIP.
original=pool.candidate_pools
stats=[]
def expanded(problem,initial,**options):
 ordinary=original(problem,initial,**{**options,'max_columns':128})
 result=[];contexts={s:route_context(problem,s) for s in p.satellites}
 for trip,rows in zip(initial['e2_trips'],ordinary):
  known={c['tasks']:c for c in rows};site=trip['satellite_id']
  candidates=[]
  for col in extra[site]:
   replay=evaluate_column(problem,contexts[site],col['tasks'])
   if replay is not None and replay['tasks'] not in known:candidates.append(replay)
  for col in sorted(candidates,key=lambda c:(c['cost']/len(c['tasks']),c['cost'],c['tasks']))[:128]:known[col['tasks']]=col
  result.append(list(known.values()))
 stats.append(dict(columns=sum(map(len,result)),slot_counts=list(map(len,result))))
 return result
pool.candidate_pools=expanded
write(DEST/'manifest.json',dict(time_limit=120,seed=42,base=str(HERE/'recombine_120s/100_full.json'),
    sources=[str(s) for s in sources]+[str(OLD/'100_full.json')],
    source_sha256={str(f.relative_to(HERE/'final_source_snapshot')):hashlib.sha256(f.read_bytes()).hexdigest() for f in (HERE/'final_source_snapshot').rglob('*.py')},
    wrapper_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),registered_before_run=True,
    extra_per_slot=128,ordinary_per_slot=128,full_model_proof=False))
start=time.perf_counter();result=pool.reoptimize_route_pool(p,base['result'],time_limit=120,max_columns=256,
    exchanges=True,recombinations=False,e1_backend='patterns',max_passes=4,seed=42,diagnostics_dir=DEST/'100_full')
assert audit_solution(p,result)['valid']
write(DEST/'100_full.json',dict(job=base['job'],result=result,wall_seconds=time.perf_counter()-start,expansion_stats=stats))
print(dict(upper=result['info']['objective'],improved=result['info']['route_pool_improved'],wall=time.perf_counter()-start),flush=True)
