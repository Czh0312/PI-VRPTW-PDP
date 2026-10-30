"""Isolated M8/S01 integer-minute calendar experiment; seconds for routing."""
from pathlib import Path
import sys,json,math,time,hashlib,argparse
OUT=Path(__file__).resolve().parent
ROOT=OUT.parents[2]
BASE=ROOT/'output/experiments/small7_m8_k1_4_exact1800_20261024_v1'
WITNESS=ROOT/'output/experiments/m8_minute_grid_space_20261024/minute_witness_result.json'
sys.path.insert(0,str(ROOT/'output/experiments/m8_s01_feasibility_diagnosis_20261024'))
import diagnose as d
from models.cost.comparison import evaluate_comparison_cost
gp=d.gp

def read(p):return json.loads(Path(p).read_text(encoding='utf-8'))
def save(name,obj):(OUT/name).write_text(json.dumps(obj,ensure_ascii=False,indent=2),encoding='utf-8')
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()

def minute_builder(problem,**kwargs):
    model,ctx=d.build_model(problem,**kwargs)
    model.update()
    assert problem.operating_policy.time_tick_seconds==1 and problem.operating_policy.time_tick_subdivision==1
    for group in ('batch_starts','cutoffs'):
        for bid,var in ctx[group].items():
            if not isinstance(var,gp.Var) or var.LB==var.UB:continue
            minute=model.addVar(vtype=gp.GRB.INTEGER,lb=math.ceil(var.LB/60),
                ub=math.floor(var.UB/60),name=f'minute_{group}_{bid}')
            model.addConstr(var==60*minute,name=f'minute_grid_{group}_{bid}')
    return model,ctx

def main():
    parser=argparse.ArgumentParser();parser.add_argument('mode',choices=['cold','fixed','warm'])
    parser.add_argument('--seconds',type=float,default=300)
    args=parser.parse_args();mode=args.mode;fixed=mode=='fixed';warm=mode=='warm'
    frozen=read(BASE/'manifest.json')
    for group in ('source_hashes','input_hashes'):
        for name,digest in frozen[group].items():assert sha(BASE/name)==digest,name
    cfg,fleet=d.load_routing_case(BASE/'cases/S01_m8_k1_4.json',use_pool_config=False)
    problem=d.load_instance(cfg,fleet);witness=read(WITNESS)
    witness_audit=d.audit_solution(problem,witness)
    assert witness_audit['valid'] and witness_audit['periodic_replay']['valid']
    assert all(v%60==0 for v in witness['batch_boundaries'])
    assert all(v%60==0 for v in witness['batch_cutoffs'].values())
    settings=dict(case='S01',model=8,mode=mode,allowed_K=[1,2,3,4],calendar_grid_seconds=60,
        routing_clock_seconds=1,formulation='compact',threads=1,seed=42,optimization_seconds=args.seconds,
        mip_start=warm,fixed_schedule=fixed,mip_focus=1 if mode=='cold' else 0,
        degen_moves=0 if mode=='cold' else -1,no_rel_heur_time=60 if mode=='cold' else 0,
        first_solution_stop=mode=='cold',custom_callback=False,frozen_hashes_verified=True,
        source_snapshot=str(BASE/'source'),driver_sha256=sha(__file__),witness_sha256=sha(WITNESS),
        physical_inputs={key:sha(cfg[key]) for key in ('data','routing_config','orders','operating_policy','economic_config')},
        proof_scope='fixed_minute_witness_only' if fixed else 'M8_K1_4_calendar_grid_60s_integer_time_MILP')
    save(mode+'_manifest.json',settings)
    begun=time.perf_counter()
    if fixed:
        child=problem.for_count(2);model,ctx=minute_builder(child,compact_e2=True)
        model.update();original_names=[v.VarName for v in model.getVars()]
        d.fix_schedule(model,ctx,witness)
    else:
        model,ctx=d.build_unified_model(problem,build_fixed=minute_builder,compact_e2=True)
        assert (model.NumVars,model.NumBinVars,model.NumConstrs,model.NumGenConstrs)==(35538,9762,86941,21670)
        assert set(ctx['blocks'])=={1,2,3,4}
        assert not any(c.ConstrName.startswith('pin_') for c in model.getConstrs())
        if warm:
            values=read(OUT/'fixed_values.json')
            for v in model.getVars():
                if v.VarName.startswith('K2_'):v.Start=values[v.VarName[3:]]
                elif v.VarName.startswith('K'):v.Start=0
                elif v.VarName=='select_count[2]':v.Start=1
                elif v.VarName.startswith('select_count['):v.Start=0
                elif v.VarName=='batch_count':v.Start=2
                elif v.VarName=='container_pool':v.Start=witness['container_pool_size']
                else:raise AssertionError(v.VarName)
        else:assert model.NumStart==0
    build=time.perf_counter()-begun
    model.Params.LogFile=(OUT.relative_to(ROOT)/(mode+'_gurobi.log')).as_posix()
    model.Params.OutputFlag=1;model.Params.Threads=1;model.Params.Seed=42
    model.Params.TimeLimit=args.seconds
    if mode=='cold':
        model.Params.MIPFocus=1;model.Params.DegenMoves=0;model.Params.NoRelHeurTime=60
        model.Params.SolutionLimit=1
    print(json.dumps(dict(stage='built',mode=mode,build_seconds=build,variables=model.NumVars)),flush=True)
    model.optimize()
    info=d.read_milp_info(model,proof_scope=settings['proof_scope'],build_time=build)
    if model.Status==gp.GRB.SOLUTION_LIMIT:info['status']='SOLUTION_LIMIT'
    info.update(mode=mode,mip_start_supplied=warm,settings=settings,solve_wall_seconds=time.perf_counter()-begun)
    if model.SolCount:
        if fixed:
            save('fixed_values.json',{name:model.getVarByName(name).X for name in original_names})
            selected,selected_ctx=child,ctx
        else:
            count=next(k for k,v in ctx['selectors'].items() if v.X>.5)
            selected,selected_ctx=ctx['blocks'][count]['problem'],ctx['blocks'][count]['context']
        result=d.extract_solution(selected,model,selected_ctx,dict(info))
        boundaries=[b['start'] for b in result['batches']]+[selected.operating_policy.cycle_end]
        d.attach_calendar_decisions(problem,result,len(result['batches']),boundaries)
        assert all(v%60==0 for v in result['batch_boundaries'])
        assert all(v%60==0 for v in result['batch_cutoffs'].values())
        result['calendar_grid_seconds']=60
        result['audit']=d.audit_solution(problem,result)
        d.accept_milp_audit(result,model.ObjVal)
        assert result['audit']['valid'] and result['audit']['periodic_replay']['valid']
        cost=evaluate_comparison_cost(problem,result,cfg['economic_config'],audit=result['audit'])
        assert math.isclose(cost['total'],model.ObjVal,abs_tol=1e-6)
        if warm or fixed:assert cost['total']<=witness_audit['objective']+1e-6
        save(mode+'_result.json',result);save(mode+'_cost.json',cost)
        info.update(objective=cost['total'],mip_gap=result['info']['mip_gap'],audit_valid=True,minute_grid_valid=True,
            periodic_replay_valid=True,K=len(result['batches']),pool=result['container_pool_size'],
            max_constraint_violation=model.ConstrVio,max_integer_violation=model.IntVio)
    save(mode+'_summary.json',info)
    model.dispose()
    artifacts={p.name:sha(p) for p in OUT.glob(mode+'_*') if p.is_file()}
    save(mode+'_completion.json',dict(artifacts=artifacts,full_unit_suite_run=False))
    print(json.dumps(info,ensure_ascii=False,indent=2),flush=True)

if __name__=='__main__':main()
