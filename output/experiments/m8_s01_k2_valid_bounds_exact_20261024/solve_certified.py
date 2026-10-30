"""Exact K=2 solve after explicitly certified optimality-preserving reductions."""
from pathlib import Path
import importlib.util,json,time,math,argparse
OUT=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('certificate_for_solve',OUT/'certify_reductions.py')
c=importlib.util.module_from_spec(spec);spec.loader.exec_module(c)
t=c.trial;d=t.d;gp=t.gp

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--seconds',type=float,default=600)
    args=parser.parse_args()
    frozen=t.read(t.BASE/'manifest.json')
    for group in ('source_hashes','input_hashes'):
        for name,digest in frozen[group].items():assert t.sha(t.BASE/name)==digest,name
    cfg,fleet=d.load_routing_case(t.BASE/'cases/S01_m8_k1_4.json',use_pool_config=False)
    parent=d.load_instance(cfg,fleet);child=parent.for_count(2)
    seed=t.read(t.SEED);audit=d.audit_solution(parent,seed);assert audit['valid']
    cert=c.certificate(parent,audit['objective'])
    assert cert==t.read(OUT/'reduction_certificate.json')
    started=time.perf_counter()
    model,ctx=t.m.minute_builder(child,compact_e2=True);model.update()
    t.add_valid_bounds(child,model,ctx)
    ctx['pool'].UB=4
    first=set(cert['forced_first_batch_orders'])
    for (oid,bid),var in ctx['assignments'].items():
        var.LB=var.UB=int((oid in first)==(bid=='b1'))
    ctx['cutoffs']['b1'].LB=max(ctx['cutoffs']['b1'].LB,cert['first_cutoff_lower_bound'])
    ctx['batch_starts']['b2'].LB=max(ctx['batch_starts']['b2'].LB,cert['second_batch_start_lower_bound'])
    model.update()
    assert ctx['batch_starts']['b2'].LB<ctx['batch_starts']['b2'].UB
    assert all(v.LB<v.UB for v in ctx['cutoffs'].values())
    assert not any(row.ConstrName.startswith('pin_') for row in model.getConstrs())
    values=t.read(OUT/'start_values.json')
    for v in model.getVars():v.Start=values[v.VarName]
    settings=dict(fixed_K=2,calendar_grid_seconds=60,routing_clock_seconds=1,threads=1,seed=42,
        formulation='compact',mip_start=True,fixed_calendar=False,custom_callback=False,
        optimization_seconds=args.seconds,reduction_certificate_sha256=t.sha(OUT/'reduction_certificate.json'),
        driver_sha256=t.sha(__file__),source_snapshot=str(t.BASE/'source'),frozen_hashes_verified=True,
        proof_scope='M8_K2_minute_calendar_optimum_preserved_by_certificate',
        unfixed_decisions=['routes','vehicles','service_times','batch_boundary','batch_cutoffs'],
        derived_optimum_conditions=dict(pool=4,first_batch=cert['forced_first_batch_orders'],second_batch=cert['forced_second_batch_orders']))
    t.save('certified_manifest.json',settings)
    build=time.perf_counter()-started
    model.Params.LogFile=(OUT.relative_to(t.ROOT)/'certified_gurobi.log').as_posix()
    model.Params.OutputFlag=1;model.Params.Threads=1;model.Params.Seed=42;model.Params.TimeLimit=args.seconds
    print(json.dumps(dict(stage='built',settings=settings,build_seconds=build,variables=model.NumVars)),flush=True)
    model.optimize()
    info=d.read_milp_info(model,proof_scope=settings['proof_scope'],build_time=build)
    info.update(settings=settings,mip_start_supplied=True,solve_wall_seconds=time.perf_counter()-started)
    if model.SolCount:
        result=t.finish(parent,child,model,ctx,info)
        info.update(objective=result['info']['objective'],mip_gap=result['info']['mip_gap'],
            original_K2_lower_bound=min(info['best_bound'],cert['N_ge_5_total_cost_lower_bound']),
            audit_valid=True,periodic_replay_valid=True,pool=result['container_pool_size'],
            batch_boundaries=result['batch_boundaries'],batch_cutoffs=result['batch_cutoffs'],
            max_constraint_violation=model.ConstrVio,max_integer_violation=model.IntVio)
        assert info['objective']<=cert['incumbent_cost']+1e-6
        assert cert['N_ge_5_total_cost_lower_bound']>info['objective']+1e-5
        t.save('certified_result.json',result)
        t.save('certified_values.json',{v.VarName:v.X for v in model.getVars()})
    t.save('certified_summary.json',info)
    model.write((OUT.relative_to(t.ROOT)/'certified_model.mps').as_posix());model.dispose()
    t.save('certified_completion.json',dict(artifacts={p.name:t.sha(p) for p in OUT.glob('certified_*') if p.is_file()}))
    print(json.dumps(info,ensure_ascii=False,indent=2),flush=True)

if __name__=='__main__':main()
