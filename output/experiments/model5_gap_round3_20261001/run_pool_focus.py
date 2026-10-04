"""Reuse the saved candidate domain and warm incumbent; stop on certified 5%."""
from copy import deepcopy
from pathlib import Path
import hashlib,json,math,sys,time
HERE=Path(__file__).resolve().parent;sys.path.insert(0,str(HERE/'final_source_snapshot'))
from configuration.loader import load_routing_case
from models.routing.common.cycle import load_instance
from models.routing.solvers.exact.multibatch_e1_routes import build_e1_routes,set_route_projection
from models.routing.solvers.exact.multibatch_route_pool import add_route_pool,extract_pool
from models.routing.common.cycle.audit import audit_solution
import gurobipy as gp
DEST=HERE/'pool_focus_180s_retry01';DEST.mkdir(exist_ok=False)
def read(p):return json.loads(Path(p).read_text())
def write(p,v):p.write_text(json.dumps(v,indent=2,ensure_ascii=False,allow_nan=False)+'\n')
prior=read(HERE/'global_columns_120s/100_full.json');p=load_instance(*load_routing_case(prior['job']['case']))
old_dir=HERE/'global_columns_120s/100_full/pass_0'
metadata=read(old_dir/'initial_result.json');columns=read(old_dir/'columns.json');best=prior['result']
for rows in columns:
    for col in rows:col['tasks']=tuple(tuple(key) for key in col['tasks'])
lb=next(r['lower'] for r in read(HERE/'validation.json')['cases'] if r['id']=='100_full')
job=dict(time_limit=180,seed=42,mip_focus=1,heuristics=.3,target_cost=lb/.95-1e-5,validated_global_lb=lb,
    base=str(HERE/'global_columns_120s/100_full.json'),pool_sha256=hashlib.sha256((old_dir/'columns.json').read_bytes()).hexdigest(),
    original_metadata_sha256=hashlib.sha256((old_dir/'initial_result.json').read_bytes()).hexdigest(),registered_before_run=True,
    proof_scope='restricted_primal_pool_only',source_manifest_sha256=hashlib.sha256((HERE/'resume_cg/manifest.json').read_bytes()).hexdigest())
write(DEST/'manifest.json',job)
start=time.perf_counter();deadline=start+180
m,c=build_e1_routes(p,deadline=deadline)
try:
    add_route_pool(p,m,c,metadata,columns,deadline=deadline)
    set_route_projection(p,c,best)
    by_id={t['trip_id']:t for t in best['e2_trips']}
    for i,old in enumerate(metadata['e2_trips']):
        trip=by_id.get(old['trip_id']);sequence=tuple((e['order_id'],e['kind']) for e in trip['events']) if trip else None
        found=False
        for j,var in c['pool_choices'][i].items():
            chosen=sequence==tuple(tuple(k) for k in columns[i][j]['tasks']);var.Start=int(chosen);found|=chosen
        assert found or trip is None
        slot=c['pool_slots'][i];slot['used'].Start=int(trip is not None)
        slot['start'].Start=trip['departure'] if trip else 0;slot['end'].Start=trip['return_complete'] if trip else 0
        if trip:
            for e in trip['events']:c['order_times'][e['order_id'],e['kind']].Start=e['start']
    m.Params.Seed=42;m.Params.MIPFocus=1;m.Params.Heuristics=.3
    m.Params.BestObjStop=job['target_cost'];m.Params.TimeLimit=max(0,deadline-time.perf_counter());m.optimize()
    result=deepcopy(best)
    if m.SolCount:
        candidate=extract_pool(p,c,metadata,columns,'patterns');a=audit_solution(p,candidate)
        assert a['valid'] and a['periodic_replay']['valid']
        assert math.isclose(a['objective'],m.ObjVal,rel_tol=1e-7,abs_tol=1e-5)
        if a['objective']<best['info']['objective']-1e-6:result=candidate
    a=audit_solution(p,result);assert a['valid'] and a['periodic_replay']['valid']
    result['info']=dict(status='FEASIBLE',objective=a['objective'],best_bound=None,mip_gap=None,
        proven_optimal=False,proven_infeasible=False,proof_scope='restricted_E2_route_pool_primal',
        route_pool_complete=False,route_pool_status=int(m.Status),route_pool_subproblem_bound=m.ObjBound,
        route_pool_subproblem_optimal=m.Status==gp.GRB.OPTIMAL,upper_before=best['info']['objective'],
        route_pool_improved=a['objective']<best['info']['objective']-1e-6,runtime=time.perf_counter()-start)
    result['audit']=a;m.write(str(DEST/'route_pool.mps'))
    write(DEST/'100_full.json',dict(job=job,result=result,wall_seconds=time.perf_counter()-start,
        lower=lb,gap_percent=100*(a['objective']-lb)/a['objective']))
    print(dict(upper=a['objective'],gap=100*(a['objective']-lb)/a['objective'],wall=time.perf_counter()-start),flush=True)
finally:m.dispose()
