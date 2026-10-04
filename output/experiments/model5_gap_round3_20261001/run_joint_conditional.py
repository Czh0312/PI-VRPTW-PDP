"""Full-M5 valid E1 objective plus conditional full-domain E2 cost floors."""
from pathlib import Path
import hashlib,json,math,os,subprocess,sys,time
HERE=Path(__file__).resolve().parent;OLD=HERE.parent/'model5_gap_round2_20261001'
sys.path.insert(0,str(HERE/'subset_source_snapshot'))
from configuration.loader import load_routing_case
from models.routing.common.cycle import load_instance
from models.routing.common.cycle.precheck import travel_lower_bounds
from models.routing.solvers.exact.multibatch_route_pricing import route_context,evaluate_column
from models.routing.solvers.exact.multibatch_route_cg import site_route_cover
from models.routing.solvers.exact.multibatch_e1_routes import build_e1_routes,set_route_projection
from models.routing.common.cycle.audit import audit_solution
import gurobipy as gp
DEST=HERE/'joint_conditional'
def read(p):return json.loads(Path(p).read_text())
def write(p,v):p.write_text(json.dumps(v,indent=2,ensure_ascii=False,allow_nan=False)+'\n')
def context_at(p,site,bid,threshold):
    travel=travel_lower_bounds(p);c=route_context(p,site,travel);release={**c['release']}
    release[bid]=max(release[bid],threshold+p.operating_policy.e2_dispatch_prep_time)
    nodes=[]
    for node in c['nodes']:
        order=node['order'];d=max(order.delivery_window[0],release[order.batch]+travel['outward'][site][order.customer])
        low=d if node['key'][1]=='d' else max(order.pickup_window[0],d+order.service_time+order.min_use_time)
        nodes.append({**node,'window':(low,node['window'][1])})
    return {**c,'release':release,'nodes':nodes,'lookup':{n['key']:n for n in nodes}}
def joint(p,conditions,initial,baselines,dest):
    start=time.perf_counter();m,c=build_e1_routes(p,deadline=start+30)
    try:
        set_route_projection(p,c,initial)
        theta=m.addVars(p.satellites,name='conditional_E2_cost')
        for site in p.satellites:m.addConstr(theta[site]>=baselines[site])
        rows=[]
        for cond in conditions:
            job=cond['job'];site,bid,threshold=job['site'],job['batch'],job['threshold'];lower=cond['site']['bound']
            if lower<=baselines[site]+1e-6:continue
            indices=[i for i,r in enumerate(c['patterns']) if r['batch']==bid and site in r['supply'] and r['supply'][site]>=threshold]
            late=gp.quicksum(c['chosen'][i] for i in indices)
            m.addConstr(theta[site]>=baselines[site]+(lower-baselines[site])*late)
            rows.append(dict(site=site,batch=bid,threshold=threshold,conditional_bound=lower,base_bound=baselines[site],late_indices=indices))
        m.setObjective(m.getObjective()+theta.sum())
        actual={s:0.0 for s in p.satellites}
        for t in initial['e2_trips']:
            places=[t['satellite_id']]+[p.order_map[e['order_id']].customer for e in t['events']]+[t['satellite_id']]
            actual[t['satellite_id']]+=math.fsum(p.distance(a,b) for a,b in zip(places,places[1:]))
        for site,var in theta.items():var.Start=actual[site]
        m.Params.Seed=42;m.Params.TimeLimit=max(0,start+30-time.perf_counter());m.optimize()
        raw=m.ObjBound;margin=1e-7*max(1,abs(raw));bound=max(0,raw-margin)
        m.write(str(dest/'joint.mps'))
        return dict(bound=bound,raw_bound=raw,margin=margin,status=int(m.Status),relaxation_optimal=m.Status==gp.GRB.OPTIMAL,
            variables=m.NumVars,constraints=m.NumConstrs,baselines=baselines,conditional_rows=rows,
            runtime=time.perf_counter()-start,proven_optimal=False,proven_infeasible=False,
            scope='M5_E1_inventory_plus_conditional_E2_floors',patterns=len(c['patterns']))
    finally:m.dispose()
if len(sys.argv)>1:
    job=read(DEST/'manifest.json')['jobs'][int(sys.argv[1])]
    p=load_instance(*load_routing_case(job['case']));context=context_at(p,job['site'],job['batch'],job['threshold'])
    cert=read(OLD/'100_full.json')['certificate'];columns=next(row['columns'] for row in cert['sites'] if row['satellite']==job['site'])
    valid=[col for col in columns if evaluate_column(p,context,col['tasks']) is not None]
    bag=dict(e2_trips=[dict(satellite_id=job['site'],events=[dict(order_id=oid,kind=kind) for oid,kind in col['tasks']]) for col in valid])
    start=time.perf_counter();row=site_route_cover(p,context,deadline=start+job['time_limit'],initial_result=bag,
        batch_pricing=True,dual_repair=True,delivery_count_cuts=True,pricing_time_limit=8,seed=42,
        pricing_export_path=DEST/job['id']/'best_pricing.mps')
    write(DEST/(job['id']+'.json'),dict(job=job,site=row,context_release=context['release'],context_windows=[[list(n['key']),list(n['window'])] for n in context['nodes']],wall_seconds=time.perf_counter()-start))
else:
    DEST.mkdir(exist_ok=False)
    pfile=HERE/'global_columns_120s/100_full.json';base=read(pfile);p=load_instance(*load_routing_case(base['job']['case']))
    prior=read(HERE/'validation.json');baselines={s:r['bound'] for s,r in prior['shared_E2_components'].items()}
    inspected=read(HERE/'joint_supply_inspection.json')['supplies']
    jobs=[dict(id=site+'_'+bid,site=site,batch=bid,threshold=next(r['relaxed'] for r in inspected if r['site']==site and r['batch']==bid),
        time_limit=seconds,case=base['job']['case'],seed=42) for site,bid,seconds in [('S3','b1',90),('S0','b1',120),('S0','b2',120)]]
    write(DEST/'manifest.json',dict(jobs=jobs,baselines=baselines,registered_before_run=True,initial_result=str(pfile),
        source_sha256={str(f.relative_to(HERE/'subset_source_snapshot')):hashlib.sha256(f.read_bytes()).hexdigest() for f in (HERE/'subset_source_snapshot').rglob('*.py')},
        base_validation_sha256=hashlib.sha256((HERE/'validation.json').read_bytes()).hexdigest(),target_bound=.95*base['result']['info']['objective']))
    conditions=[];summary=[]
    for i,job in enumerate(jobs):
        with (DEST/(job['id']+'.log')).open('w') as log:
            run=subprocess.run([sys.executable,str(Path(__file__)),str(i)],stdout=log,stderr=subprocess.STDOUT,timeout=job['time_limit']+30,env={**os.environ,'PYTHONHASHSEED':'0'})
        row=dict(id=job['id'],exit=run.returncode)
        if run.returncode==0:
            cond=read(DEST/(job['id']+'.json'));conditions.append(cond);row.update(site_bound=cond['site']['bound'],site_status=cond['site']['status'])
            out=DEST/('master_'+str(i));out.mkdir();proof=joint(p,conditions,base['result'],baselines,out)
            write(out/'certificate.json',proof);row['joint_bound']=proof['bound'];row['gap']=100*(base['result']['info']['objective']-proof['bound'])/base['result']['info']['objective']
        summary.append(row);write(DEST/'summary.json',summary);print(row,flush=True)
        if row.get('gap',100)<=5:break
