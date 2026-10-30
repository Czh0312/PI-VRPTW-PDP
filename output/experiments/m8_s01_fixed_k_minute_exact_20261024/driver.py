"""Independent fixed-K M8 optimization; calendar and routes remain decisions."""
from pathlib import Path
import sys,json,math,time,argparse,hashlib
OUT=Path(__file__).resolve().parent
ROOT=OUT.parents[2]
PREVIOUS=ROOT/'output/experiments/m8_s01_minute_grid_exact_20261024'
sys.path.insert(0,str(PREVIOUS))
import driver as minute_trial
d=minute_trial.d;gp=d.gp;BASE=minute_trial.BASE

def read(p):return json.loads(Path(p).read_text(encoding='utf-8'))
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def save(name,value):(OUT/name).write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--k',type=int,default=2)
    parser.add_argument('--seconds',type=float,default=300)
    args=parser.parse_args();k=args.k;prefix=f'K{k}'
    assert k in (1,2,3,4)
    for group,records in read(BASE/'manifest.json').items():
        if group in ('source_hashes','input_hashes'):
            for name,digest in records.items():assert sha(BASE/name)==digest,name
    cfg,fleet=d.load_routing_case(BASE/'cases/S01_m8_k1_4.json',use_pool_config=False)
    parent=d.load_instance(cfg,fleet);child=parent.for_count(k)
    settings=dict(case='S01',model=8,fixed_K=k,calendar_grid_seconds=60,routing_clock_seconds=1,
        formulation='compact',threads=1,seed=42,optimization_seconds=args.seconds,
        fixed_schedule=False,custom_callback=False,mip_focus=0,degen_moves=-1,no_rel_heur_time=0,
        proof_scope=f'M8_fixed_K{k}_calendar_grid_60s_integer_time_MILP',complete_original_K_domain=False,
        driver_sha256=sha(__file__),frozen_source_hashes_verified=True,source_snapshot=str(BASE/'source'),
        physical_input_hashes={key:sha(cfg[key]) for key in ('data','routing_config','orders','operating_policy','economic_config')})
    begun=time.perf_counter()
    model,ctx=minute_trial.minute_builder(child,compact_e2=True);model.update()
    assert not any(c.ConstrName.startswith('pin_') for c in model.getConstrs())
    if k==2:
        values=read(PREVIOUS/'fixed_values.json')
        assert len(values)==model.NumVars
        for v in model.getVars():v.Start=values[v.VarName]
        settings.update(mip_start=True,start_values_sha256=sha(PREVIOUS/'fixed_values.json'),
            witness_sha256=sha(minute_trial.WITNESS))
    else:settings['mip_start']=False
    save(prefix+'_manifest.json',settings)
    build=time.perf_counter()-begun
    model.Params.LogFile=(OUT.relative_to(ROOT)/(prefix+'_gurobi.log')).as_posix()
    model.Params.OutputFlag=1;model.Params.Threads=1;model.Params.Seed=42;model.Params.TimeLimit=args.seconds
    print(json.dumps(dict(stage='built',fixed_K=k,variables=model.NumVars,binary=model.NumBinVars,
        linear_constraints=model.NumConstrs,indicators=model.NumGenConstrs,build_seconds=build)),flush=True)
    model.optimize()
    info=d.read_milp_info(model,proof_scope=settings['proof_scope'],build_time=build)
    info.update(settings=settings,fixed_K=k,mip_start_supplied=settings['mip_start'],
        solve_wall_seconds=time.perf_counter()-begun)
    if model.SolCount:
        result=d.extract_solution(child,model,ctx,dict(info))
        boundaries=[b['start'] for b in result['batches']]+[child.operating_policy.cycle_end]
        d.attach_calendar_decisions(parent,result,k,boundaries)
        result.update(calendar_grid_seconds=60,fixed_K=k)
        result['info']['proof_scope']=settings['proof_scope']
        assert len(result['batches'])==k and all(t%60==0 for t in result['batch_boundaries'])
        assert all(t%60==0 for t in result['batch_cutoffs'].values())
        result['audit']=d.audit_solution(parent,result)
        d.accept_milp_audit(result,model.ObjVal)
        assert result['audit']['valid'] and result['audit']['periodic_replay']['valid']
        cost=minute_trial.evaluate_comparison_cost(parent,result,cfg['economic_config'],audit=result['audit'])
        assert math.isclose(cost['total'],model.ObjVal,abs_tol=1e-6)
        if k==2:assert cost['total']<=35.50906153797168+1e-6
        info.update(objective=cost['total'],mip_gap=result['info']['mip_gap'],audit_valid=True,
            minute_grid_valid=True,periodic_replay_valid=True,pool=result['container_pool_size'],
            batch_boundaries=result['batch_boundaries'],batch_cutoffs=result['batch_cutoffs'],
            max_constraint_violation=model.ConstrVio,max_integer_violation=model.IntVio)
        assert model.ConstrVio<=1e-5 and model.IntVio<=1e-5
        save(prefix+'_result.json',result);save(prefix+'_cost.json',cost)
        model.write((OUT.relative_to(ROOT)/(prefix+'_incumbent.sol')).as_posix())
    model.write((OUT.relative_to(ROOT)/(prefix+'_model.mps')).as_posix())
    save(prefix+'_summary.json',info)
    model.dispose()
    save(prefix+'_completion.json',dict(artifacts={p.name:sha(p) for p in OUT.glob(prefix+'_*') if p.is_file()},
        full_unit_suite_run=False))
    print(json.dumps(info,ensure_ascii=False,indent=2),flush=True)

if __name__=='__main__':main()
