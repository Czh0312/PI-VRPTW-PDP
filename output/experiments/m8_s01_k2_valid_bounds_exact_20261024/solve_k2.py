"""Fixed K=2, free minute calendar, strengthened by physical valid bounds."""
from pathlib import Path
import sys,json,math,time,hashlib,argparse,importlib.util
OUT=Path(__file__).resolve().parent
ROOT=OUT.parents[2]
MINUTE=ROOT/'output/experiments/m8_s01_minute_grid_exact_20261024'
spec=importlib.util.spec_from_file_location('minute_trial_bounds',MINUTE/'driver.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
d=m.d;gp=d.gp;BASE=m.BASE
SEED=ROOT/'output/experiments/m8_s01_fixed_k_minute_exact_20261024/K2_result.json'

def read(p):return json.loads(Path(p).read_text(encoding='utf-8'))
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def save(name,obj):(OUT/name).write_text(json.dumps(obj,ensure_ascii=False,indent=2),encoding='utf-8')

def add_valid_bounds(problem,model,ctx):
    """Necessary physical conditions, not fixed assignments/calendar/routes.

    Stock is debited by each batch's entire demand at its loading instant;
    therefore D_b <= N. Sum D_b = Q implies integer N >= ceil(Q/K).
    With one satellite, every productive batch must visit that satellite
    once for delivery and once for recovery. Existing upper bounds are 1.
    """
    batches=problem.operating_policy.batches
    lower=math.ceil(problem.total/len(batches))
    ctx['pool'].LB=max(ctx['pool'].LB,lower)
    for b in batches:
        demand=gp.quicksum(o.count*v for (oid,bid),v in ctx['assignments'].items()
                          for o in [problem.order_map[oid]] if bid==b.id)
        model.addConstr(ctx['pool']>=demand,name=f'valid_pool_covers_batch_{b.id}')
    if len(problem.satellites)==1:
        satellite=next(iter(problem.satellites))
        for b in batches:
            for kind in ('d','p'):
                visits=[r['visit'][i] for r in ctx['e1'] if r['batch'].id==b.id
                        for i,n in enumerate(r['nodes']) if n['site']==satellite and n['kind']==kind]
                model.addConstr(gp.quicksum(visits)==1,name=f'valid_productive_visit_{b.id}_{kind}')
    model.update()
    return dict(pool_lower_bound=lower,batch_pool_inequalities=len(batches),
                one_satellite_visit_equalities=2*len(batches) if len(problem.satellites)==1 else 0)

def finish(parent,child,model,ctx,info):
    result=d.extract_solution(child,model,ctx,dict(info))
    boundaries=[b['start'] for b in result['batches']]+[child.operating_policy.cycle_end]
    d.attach_calendar_decisions(parent,result,2,boundaries)
    result.update(fixed_K=2,calendar_grid_seconds=60)
    result['info']['proof_scope']=info['proof_scope']
    result['audit']=d.audit_solution(parent,result)
    d.accept_milp_audit(result,model.ObjVal)
    assert result['audit']['valid'] and result['audit']['periodic_replay']['valid']
    assert all(v%60==0 for v in result['batch_boundaries'])
    assert all(v%60==0 for v in result['batch_cutoffs'].values())
    assert len(result['batches'])==2 and result['container_pool_size']>=4
    return result

def main():
    parser=argparse.ArgumentParser();parser.add_argument('mode',choices=['prepare-start','solve'])
    parser.add_argument('--seconds',type=float,default=1800)
    args=parser.parse_args();fixed=args.mode=='prepare-start';prefix='start' if fixed else 'solve'
    frozen=read(BASE/'manifest.json')
    for group in ('source_hashes','input_hashes'):
        for name,digest in frozen[group].items():assert sha(BASE/name)==digest,name
    cfg,fleet=d.load_routing_case(BASE/'cases/S01_m8_k1_4.json',use_pool_config=False)
    parent=d.load_instance(cfg,fleet);child=parent.for_count(2)
    seed=read(SEED);assert d.audit_solution(parent,seed)['valid']
    begun=time.perf_counter();model,ctx=m.minute_builder(child,compact_e2=True);model.update()
    valid_bounds=add_valid_bounds(child,model,ctx)
    names=[v.VarName for v in model.getVars()]
    if fixed:
        d.fix_schedule(model,ctx,seed)
    else:
        assert not any(c.ConstrName.startswith('pin_') for c in model.getConstrs())
        assert ctx['batch_starts']['b2'].LB<ctx['batch_starts']['b2'].UB
        assert all(v.LB<v.UB for v in ctx['cutoffs'].values())
        values=read(OUT/'start_values.json')
        for v in model.getVars():v.Start=values[v.VarName]
    settings=dict(fixed_K=2,fixed_schedule=fixed,calendar_grid_seconds=60,routing_clock_seconds=1,
        free_decisions=['order_batch_assignment','routes','vehicles','service_times','container_pool','batch_boundary','batch_cutoffs'],
        valid_bounds=valid_bounds,formulation='compact',threads=1,seed=42,mip_start=not fixed,
        custom_callback=False,optimization_seconds=60 if fixed else args.seconds,
        proof_scope='fixed_seed_only' if fixed else 'M8_K2_all_free_minute_calendar_with_valid_bounds',
        driver_sha256=sha(__file__),seed_sha256=sha(SEED),frozen_hashes_verified=True,
        source_snapshot=str(BASE/'source'))
    save(prefix+'_manifest.json',settings)
    build=time.perf_counter()-begun
    model.Params.LogFile=(OUT.relative_to(ROOT)/(prefix+'_gurobi.log')).as_posix()
    model.Params.OutputFlag=1;model.Params.Threads=1;model.Params.Seed=42
    model.Params.TimeLimit=settings['optimization_seconds']
    print(json.dumps(dict(stage='built',mode=args.mode,settings=settings,variables=model.NumVars,
        binary=model.NumBinVars,constraints=model.NumConstrs+model.NumGenConstrs)),flush=True)
    model.optimize()
    info=d.read_milp_info(model,proof_scope=settings['proof_scope'],build_time=build)
    info.update(settings=settings,mip_start_supplied=not fixed,solve_wall_seconds=time.perf_counter()-begun)
    if model.SolCount:
        result=finish(parent,child,model,ctx,info)
        info.update(objective=result['info']['objective'],mip_gap=result['info']['mip_gap'],
            audit_valid=True,periodic_replay_valid=True,pool=result['container_pool_size'],
            batch_boundaries=result['batch_boundaries'],batch_cutoffs=result['batch_cutoffs'],
            max_constraint_violation=model.ConstrVio,max_integer_violation=model.IntVio)
        assert info['objective']<=seed['info']['objective']+1e-6
        assert model.ConstrVio<=1e-5 and model.IntVio<=1e-5
        save(prefix+'_result.json',result)
        save(prefix+'_values.json',{name:model.getVarByName(name).X for name in names})
    save(prefix+'_summary.json',info)
    model.write((OUT.relative_to(ROOT)/(prefix+'_model.mps')).as_posix())
    model.dispose()
    save(prefix+'_completion.json',dict(artifacts={p.name:sha(p) for p in OUT.glob(prefix+'_*') if p.is_file()}))
    print(json.dumps(info,ensure_ascii=False,indent=2),flush=True)

if __name__=='__main__':main()
