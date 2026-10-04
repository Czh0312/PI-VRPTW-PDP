"""Select valid rounded capacity subsets; full-domain pricing certifies them."""
from itertools import combinations
from pathlib import Path
import hashlib,json,math,os,subprocess,sys,time
HERE=Path(__file__).resolve().parent;OLD=HERE.parent/'model5_gap_round2_20261001'
sys.path.insert(0,str(HERE/'subset_source_snapshot'))
from configuration.loader import load_routing_case
from models.routing.common.cycle import load_instance
from models.routing.solvers.exact.multibatch_route_pricing import route_context
from models.routing.solvers.exact.multibatch_route_cg import site_route_cover
import gurobipy as gp
DEST=HERE/'subset_cg_180s'
def read(p):return json.loads(Path(p).read_text())
def write(p,v):p.write_text(json.dumps(v,indent=2,ensure_ascii=False,allow_nan=False)+'\n')
def select(p,site,columns):
 c=route_context(p,site);m=gp.Model();m.Params.OutputFlag=0;m.Params.Threads=1
 try:
  x=m.addVars(len(columns),obj={i:v['cost'] for i,v in enumerate(columns)})
  sets=[set(tuple(k) for k in v['tasks']) for v in columns]
  for n in c['nodes']:m.addConstr(gp.quicksum(x[i] for i,s in enumerate(sets) if n['key'] in s)==1)
  m.addConstr(x.sum()<=c['maximum_trips'])
  floors={}
  for bid in c['release']:
   ns=[n for n in c['nodes'] if n['key'][1]=='d' and n['order'].batch==bid]
   if not ns:continue
   count=max(math.ceil(math.fsum(n[r] for n in ns)/(cap+1e-6)) for r,cap in [('weight',p.fleet.e2_weight_cap),('volume',p.fleet.e2_volume_cap)])
   floors[bid]=count;keys={n['key'] for n in ns}
   m.addConstr(gp.quicksum(x[i] for i,s in enumerate(sets) if s.intersection(keys))>=count)
  m.optimize();assert m.Status==gp.GRB.OPTIMAL
  chosen=[];stats=[]
  for bid in floors:
   keys=[n['key'] for n in c['nodes'] if n['key'][1]=='d' and n['order'].batch==bid];candidates=[]
   for k in range(2,len(keys)):
    for subset in combinations(keys,k):
     rows=[c['lookup'][key] for key in subset]
     rhs=max(math.ceil(math.fsum(n[r] for n in rows)/(cap+1e-6)) for r,cap in [('weight',p.fleet.e2_weight_cap),('volume',p.fleet.e2_volume_cap)])
     if rhs<2:continue
     lhs=math.fsum(x[i].X for i,s in enumerate(sets) if s.intersection(subset))
     if rhs-lhs>1e-5:candidates.append((rhs-lhs,subset,rhs,lhs))
   kept=[]
   for gap,subset,rhs,lhs in sorted(candidates,key=lambda r:(-r[0],len(r[1]),r[1])):
    if any(len(set(subset)&set(other))>=min(len(subset),len(other))-1 for other in kept):continue
    kept.append(subset);chosen.append(subset);stats.append(dict(batch=bid,tasks=subset,rhs=rhs,seed_lhs=lhs,violation=gap))
    if len(kept)>=6:break
  return chosen,stats,m.ObjVal
 finally:m.dispose()
if len(sys.argv)>1:
 job=read(DEST/'manifest.json')['jobs'][int(sys.argv[1])]
 p=load_instance(*load_routing_case(job['case']));context=route_context(p,job['site'])
 columns=read(HERE/'delivery_count_cg'/(job['site']+'.json'))['site']['columns']
 subsets,stats,restricted=select(p,job['site'],columns)
 write(DEST/(job['site']+'_selection.json'),dict(subsets=stats,restricted_objective=restricted,never_used_as_bound=True))
 bag=dict(e2_trips=[dict(satellite_id=job['site'],events=[dict(order_id=oid,kind=kind) for oid,kind in c['tasks']]) for c in columns])
 start=time.perf_counter()
 row=site_route_cover(p,context,deadline=start+job['time_limit'],initial_result=bag,batch_pricing=True,
  dual_repair=True,delivery_count_cuts=True,subset_cuts=subsets,pricing_time_limit=12,seed=42,
  pricing_export_path=DEST/job['site']/'best_pricing.mps')
 write(DEST/(job['site']+'.json'),dict(job=job,site=row,selection=stats,wall_seconds=time.perf_counter()-start))
else:
 DEST.mkdir(exist_ok=False)
 jobs=[dict(site=s,time_limit=180,seed=42,max_cuts_per_batch=6,pricing_time_limit=12,
  case=read(OLD/'100_full.json')['job']['case']) for s in ('S1','S4')]
 write(DEST/'manifest.json',dict(jobs=jobs,registered_before_run=True,source_sha256={str(f.relative_to(HERE/'subset_source_snapshot')):hashlib.sha256(f.read_bytes()).hexdigest() for f in (HERE/'subset_source_snapshot').rglob('*.py')}))
 summary=[]
 for i,j in enumerate(jobs):
  with (DEST/(j['site']+'.log')).open('w') as log:
   r=subprocess.run([sys.executable,str(Path(__file__)),str(i)],stdout=log,stderr=subprocess.STDOUT,
    timeout=210,env={**os.environ,'PYTHONHASHSEED':'0'})
  row=dict(site=j['site'],exit=r.returncode)
  if not r.returncode:
   d=read(DEST/(j['site']+'.json'));row.update(bound=d['site']['bound'],status=d['site']['status'],
    cuts=len(d['selection']),iterations=len(d['site']['iterations']),wall=d['wall_seconds'])
  summary.append(row);write(DEST/'summary.json',summary);print(row,flush=True)
