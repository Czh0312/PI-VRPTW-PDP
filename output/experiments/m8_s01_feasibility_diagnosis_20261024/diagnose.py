"""Isolated ALNS -> MILP feasibility and warm-start diagnostic; no baseline edits."""
from pathlib import Path
import sys, json, time, hashlib, argparse

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[2]
BASE = ROOT/'output/experiments/small7_m8_k1_4_exact1800_20261024_v1'
sys.path.insert(0, str(BASE/'source'))
from tools.configuration.loader import load_routing_case
from models.common.cycle import load_instance
from models.common.cycle.audit import audit_solution
from models.common.cycle.calendar import attach_calendar_decisions
from models.routing.model_8.exact.formulation import build_model
from models.routing.model_8.exact.solution import extract_solution
from models.common.cycle.unified_milp import build_unified_model
from models.common.solver_config import read_milp_info, accept_milp_audit
import gurobipy as gp

def save(name, value):
    (OUT/name).write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')

def pin(model, expr, value, label):
    if isinstance(expr, (int, float)):
        assert abs(expr-value)<1e-6, (label, expr, value)
    else:
        model.addConstr(expr == value, name='pin_'+label)

def fix_schedule(model, ctx, result):
    for batch in result['batches']:
        bid=batch['id']
        pin(model,ctx['batch_starts'][bid],batch['start'],'start_'+bid)
        pin(model,ctx['cutoffs'][bid],batch['cutoff'],'cutoff_'+bid)
    pin(model,ctx['pool'],result['container_pool_size'],'pool')
    for (oid,bid), var in ctx['assignments'].items():
        pin(model,var,int(result['order_batch_assignment'][oid]==bid),'assign_'+oid+'_'+bid)
    matched=[]
    for layer in ('e1','e2'):
        trips={t['trip_id']:t for t in result[layer+'_trips']}
        for r in ctx[layer]:
            trip=trips.get(r['label'])
            pin(model,r['used'],int(trip is not None),r['label']+'_used')
            order=[]
            if trip:
                matched.append(trip['trip_id'])
                pin(model,r['start'],trip['departure'],r['label']+'_start')
                pin(model,r['end'],trip['return_complete'],r['label']+'_end')
                for event in trip['events']:
                    indices=[i for i,n in enumerate(r['nodes']) if n['kind']==event['kind'] and
                             (n['site']==event['site_id'] if layer=='e1' else n['order'].id==event['order_id'])]
                    assert len(indices)==1, (r['label'],event,indices)
                    i=indices[0];order.append(i)
                    pin(model,r['times'][i],event['start'],r['label']+'_time_'+str(i))
                    pin(model,r['nodes'][i]['quantity'],event['quantity'],r['label']+'_q_'+str(i))
            edges=set(zip([-1]+order,order+[-2])) if order else set()
            assert edges<=set(r['x']), (r['label'],edges-set(r['x']))
            for edge,var in r['x'].items():pin(model,var,int(edge in edges),r['label']+'_arc_'+str(edge))
            for i,var in r['visit'].items():pin(model,var,int(i in order),r['label']+'_visit_'+str(i))
        assert set(trips)<=set(matched),set(trips)-set(matched)
    return matched

def main():
    parser=argparse.ArgumentParser();parser.add_argument('mode');parser.add_argument('--seconds',type=float,default=300)
    args=parser.parse_args();mode=args.mode;compact=mode.endswith('compact');fixed=mode.startswith('fixed');warm=mode.startswith('warm')
    manifest=json.loads((BASE/'manifest.json').read_text(encoding='utf-8'))
    for group in ('source_hashes','input_hashes'):
        for name,digest in manifest[group].items():
            assert hashlib.sha256((BASE/name).read_bytes()).hexdigest()==digest,name
    config=BASE/'cases/S01_m8_k1_4.json'
    seed_path=ROOT/'output/experiments/small7_m8_alns300_20261024_v1/runs/S01_m8_alns_300s_s42/attempt_001/result.json'
    seed=json.loads(seed_path.read_text(encoding='utf-8'))
    problem=load_instance(*load_routing_case(config,use_pool_config=False))
    original_contract=seed['calendar_config']
    assert {k:v for k,v in original_contract.items() if k!='batch_count_candidates'} == {k:v for k,v in problem.calendar_contract.items() if k!='batch_count_candidates'}
    attach_calendar_decisions(problem,seed,2,seed['batch_boundaries'])
    audit=audit_solution(problem,seed);assert audit['valid'],audit
    save('seed_domain_reaudit.json',dict(original_contract=original_contract,new_contract=problem.calendar_contract,audit=audit))
    begun=time.perf_counter()
    if fixed:
        child=problem.for_count(2)
        model,ctx=build_model(child,compact_e2=compact)
        model.update();original_names=[v.VarName for v in model.getVars()]
        matched=fix_schedule(model,ctx,seed)
    else:
        model,ctx=build_unified_model(problem,build_fixed=build_model,compact_e2=compact)
        if warm:
            values=json.loads((OUT/('fixed-'+('compact' if compact else 'expanded')+'_values.json')).read_text(encoding='utf-8'))
            for v in model.getVars():
                if v.VarName.startswith('K2_'):v.Start=values[v.VarName[3:]]
                elif v.VarName.startswith('K'):v.Start=0
                elif v.VarName=='select_count[2]':v.Start=1
                elif v.VarName.startswith('select_count['):v.Start=0
                elif v.VarName=='batch_count':v.Start=2
                elif v.VarName=='container_pool':v.Start=seed['container_pool_size']
                else:raise AssertionError(v.VarName)
    build=time.perf_counter()-begun
    model.Params.LogFile=(OUT.relative_to(ROOT)/(mode+'.log')).as_posix()
    model.Params.OutputFlag=1;model.Params.Threads=1;model.Params.Seed=42
    model.Params.TimeLimit=args.seconds
    if mode.startswith('cold'):
        model.Params.MIPFocus=1;model.Params.DegenMoves=0;model.Params.NoRelHeurTime=60
        model.Params.SolutionLimit=1
    print(json.dumps(dict(stage='built',mode=mode,seconds=build,variables=model.NumVars)),flush=True)
    model.optimize()
    info=read_milp_info(model,proof_scope='fixed_ALNS_schedule_only' if fixed else 'full_K1_4',build_time=build)
    if model.Status==gp.GRB.SOLUTION_LIMIT:info['status']='SOLUTION_LIMIT'
    info.update(mode=mode,warm_start=warm,mip_start_supplied=warm,solve_wall_seconds=time.perf_counter()-begun,
                optimizer_time_limit=args.seconds,threads=1,seed=42,formulation='compact' if compact else 'expanded',
                fixed_schedule=fixed,mip_focus=model.Params.MIPFocus,degen_moves=model.Params.DegenMoves,
                no_rel_heur_time=model.Params.NoRelHeurTime,
                solution_limit=model.Params.SolutionLimit,
                seed_sha256=hashlib.sha256(seed_path.read_bytes()).hexdigest(),frozen_hashes_verified=True)
    if model.SolCount:
        if fixed:
            save(mode+'_values.json',{n:model.getVarByName(n).X for n in original_names})
            selected=child;selected_ctx=ctx
        else:
            selected_k=next(k for k,v in ctx['selectors'].items() if v.X>.5)
            selected=ctx['blocks'][selected_k]['problem'];selected_ctx=ctx['blocks'][selected_k]['context']
        result=extract_solution(selected,model,selected_ctx,dict(info))
        boundaries=[b['start'] for b in result['batches']]+[selected.operating_policy.cycle_end]
        attach_calendar_decisions(problem,result,len(result['batches']),boundaries)
        result['audit']=audit_solution(problem,result)
        accept_milp_audit(result,model.ObjVal)
        assert result['audit']['valid'],result['audit']
        info.update(objective=result['info']['objective'],audit_valid=True,K=len(result['batches']),pool=result['container_pool_size'],
                    mip_gap=result['info']['mip_gap'])
        if fixed:assert abs(model.ObjVal-seed['info']['objective'])<1e-5
        save(mode+'_result.json',result)
        info['max_constraint_violation']=model.ConstrVio;info['max_integer_violation']=model.IntVio
    save(mode+'_summary.json',info)
    print(json.dumps(info,ensure_ascii=False),flush=True)
    if model.Status==gp.GRB.INFEASIBLE:
        model.Params.TimeLimit=120;model.computeIIS()
        model.write((OUT.relative_to(ROOT)/(mode+'.ilp')).as_posix())
    model.dispose()

if __name__=='__main__':main()
