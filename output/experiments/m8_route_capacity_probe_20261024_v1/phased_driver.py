"""Cold native feasibility phase, then static valid rows in the same model.

Total optimization budget is 300s. No external solution, hints or MIP starts
are supplied. The second optimize may reuse Gurobi's own previous incumbent;
this is explicitly a two-call experiment, not the public one-call baseline.
"""
from functools import partial
import math
import os
from pathlib import Path
import subprocess
import sys
import time

from driver import HERE,BASE,PROJECT,read,write,sha,imports,validate

PATH=HERE/'phased/S01'


def frozen():
    validate()
    manifest=read(HERE/'phased_manifest.json')
    assert sha(Path(__file__))==manifest['driver_sha256']
    assert sha(HERE/'manifest.json')==manifest['route_probe_manifest_sha256']


def worker():
    frozen();loader,load=imports()
    import gurobipy as gp
    from models.common.cycle.unified_milp import build_unified_model
    from models.routing.model_8.exact.formulation import build_model
    from models.routing.model_8.exact.solution import extract_solution
    from models.routing.model_8.exact.preprocessing import infeasible_time_domain_certificate
    from models.common.cycle.calendar import attach_calendar_decisions
    from models.common.cycle.audit import audit_solution
    from models.common.solver_config import read_milp_info,accept_milp_audit
    from route_bounds import subset_bounds

    p=load(*loader(BASE/'cases/S01.json',use_pool_config=False))
    begun=time.perf_counter()
    model,ctx=build_unified_model(p,build_fixed=partial(build_model,strengthening='physical_time'),compact_e2=True)
    build=time.perf_counter()-begun
    phase1_events=[]
    last=[-100.]
    def observe(m,where):
        if where==gp.GRB.Callback.MIPSOL:
            phase1_events.append(dict(seconds=m.cbGet(gp.GRB.Callback.RUNTIME),objective=m.cbGet(gp.GRB.Callback.MIPSOL_OBJ)))
        if where==gp.GRB.Callback.MIP:
            elapsed=m.cbGet(gp.GRB.Callback.RUNTIME)
            if elapsed>=last[0]+15:
                z=m.cbGet(gp.GRB.Callback.MIP_OBJBST);b=m.cbGet(gp.GRB.Callback.MIP_OBJBND)
                write(PATH/'live.json',dict(phase=1,seconds=elapsed,objective=z if abs(z)<1e99 else None,
                                           best_bound=b if abs(b)<1e99 else None))
                last[0]=elapsed
            if phase1_events and elapsed>=phase1_events[0]['seconds']+5 and m.cbGet(gp.GRB.Callback.MIP_SOLCNT)>0:
                m.terminate()

    def extract(info):
        if not model.SolCount:return None
        k=next(k for k,v in ctx['selectors'].items() if v.X>.5)
        block=ctx['blocks'][k]
        result=extract_solution(block['problem'],model,block['context'],dict(info))
        boundaries=[b['start'] for b in result['batches']]+[block['problem'].operating_policy.cycle_end]
        attach_calendar_decisions(p,result,k,boundaries)
        result['audit']=audit_solution(p,result)
        accept_milp_audit(result,model.ObjVal)
        assert result['audit']['valid'] and result['audit']['periodic_replay']['valid']
        return result

    scope='full_model_8_all_input_K_joint_cost_integer_time_MILP'
    try:
        assert model.NumStart==0
        model.Params.Threads=1;model.Params.Seed=42;model.Params.OutputFlag=1
        model.Params.TimeLimit=210
        model.Params.LogFile=os.path.relpath(PATH/'phase1.log')
        model.optimize(observe)
        first=read_milp_info(model,proof_scope=scope,build_time=build)
        first_result=extract(first)
        if first_result is not None:
            first['objective']=first_result['info']['objective']
            write(PATH/'phase1_result.json',first_result)
        first['events']=phase1_events
        write(PATH/'phase1_summary.json',first)
        second=None;second_result=None;rows=0;certificates={}
        augmentation=0
        if not first['proven_optimal'] and first['mip_runtime']<300:
            augment_begun=time.perf_counter()
            if first_result is not None:
                for k,block in ctx['blocks'].items():
                    certificate=infeasible_time_domain_certificate(block['problem'])
                    if certificate is not None:
                        model.addConstr(ctx['selectors'][k]==0,name=f'phased_K{k}_certificate')
                        certificates[k]=certificate
                        continue
                    bc=block['context'];groups={}
                    for r in bc['e2']:
                        for (a,b),v in r['x'].items():
                            if a>=0 and b>=0:groups.setdefault((r['satellite'],a,b),[]).append(v)
                    physical=bc['variables'].global_arcs
                    edges={key:physical[key] if key in physical else gp.quicksum(copies) for key,copies in groups.items()}
                    for row in subset_bounds(block['problem']):
                        tasks=set(row['tasks'])
                        terms=[v for (site,a,b),v in edges.items() if site==row['site'] and a in tasks and b in tasks]
                        rhs=len(tasks)-row['minimum_routes']
                        if not terms and rhs>=0:continue
                        model.addConstr(gp.quicksum(terms)<=rhs*ctx['selectors'][k],name=f'phased_capacity_{rows}')
                        rows+=1
            model.update();augmentation=time.perf_counter()-augment_begun
            model.Params.LogFile=os.path.relpath(PATH/'phase2.log')
            model.Params.TimeLimit=max(0.,300-first['mip_runtime'])
            last[0]=-100.
            def second_observe(m,where):
                if where==gp.GRB.Callback.MIP:
                    elapsed=m.cbGet(gp.GRB.Callback.RUNTIME)
                    if elapsed>=last[0]+15:
                        z=m.cbGet(gp.GRB.Callback.MIP_OBJBST);b=m.cbGet(gp.GRB.Callback.MIP_OBJBND)
                        write(PATH/'live.json',dict(phase=2,seconds=first['mip_runtime']+elapsed,
                            objective=z if abs(z)<1e99 else None,best_bound=b if abs(b)<1e99 else None))
                        last[0]=elapsed
            model.optimize(second_observe)
            second=read_milp_info(model,proof_scope=scope,build_time=build)
            second_result=extract(second)
            if second_result is not None:
                second['objective']=second_result['info']['objective']
                write(PATH/'phase2_result.json',second_result)
            write(PATH/'phase2_summary.json',second)
        choices=[(name,r) for name,r in [('phase1',first_result),('phase2',second_result)] if r is not None]
        name,best=min(choices,key=lambda item:item[1]['info']['objective']) if choices else (None,None)
        final=dict(second or first)
        final.update(case='S01',method='native_feasible_then_static_route_bounds',phase1=first,phase2=second,
            objective=best['info']['objective'] if best is not None else None,valid=best is not None,
            retained_solution_source=name,augmentation_seconds=augmentation,static_route_rows=rows,
            screened_batch_counts=certificates,batch_count_candidates=list(p.batch_count_candidates),
            complete_count_domain=True,external_starts=False,native_reoptimization=True,
            phase1_callback_action='terminate after first MIPSOL plus 5s at next MIP callback with accepted solution',
            total_optimization_seconds=first['mip_runtime']+(second['mip_runtime'] if second else 0.),
            first_solver_incumbent_seconds=phase1_events[0]['seconds'] if phase1_events else None)
        if best is not None:
            assert not final['proven_infeasible'], 'Valid cuts cannot exclude a previously audited incumbent'
            bound=final['best_bound']
            assert bound is None or bound<=final['objective']+1e-5
            final['mip_gap']=(final['objective']-bound)/abs(final['objective']) if bound is not None else None
            final['mip_gap_source']='audited retained incumbent and final native bound'
            write(PATH/'result.json',best)
        write(PATH/'summary.json',final)
    finally:model.dispose()


def verify():
    frozen();loader,load=imports()
    from models.common.cycle.audit import audit_solution
    from models.cost.comparison import evaluate_comparison_cost
    p=load(*loader(BASE/'cases/S01.json',use_pool_config=False));s=read(PATH/'summary.json')
    if s['valid']:
        result=read(PATH/'result.json');audit=audit_solution(p,result)
        assert audit['valid'] and audit['periodic_replay']['valid']
        cost=evaluate_comparison_cost(p,result,p.cost_profile.config,audit=audit)
        assert math.isclose(cost['total'],s['objective'],rel_tol=1e-8,abs_tol=1e-6)
        write(PATH/'independent_audit.json',audit);write(PATH/'independent_cost.json',cost)
    write(PATH/'complete.json',dict(summary_sha256=sha(PATH/'summary.json')))


def run():
    validate()
    if (HERE/'phased_manifest.json').exists():raise RuntimeError('Already registered')
    write(HERE/'phased_manifest.json',dict(driver_sha256=sha(Path(__file__)),route_probe_manifest_sha256=sha(HERE/'manifest.json'),
        seconds=300,phase1_maximum_seconds=210,phase1_candidate_grace_seconds=5,external_starts=False))
    PATH.mkdir(parents=True,exist_ok=False)
    for action in ('worker','verify'):
        with (PATH/f'{action}.log').open('w',encoding='utf-8') as log:
            env=dict(os.environ,OMP_NUM_THREADS='1',OPENBLAS_NUM_THREADS='1',MKL_NUM_THREADS='1',
                     NUMEXPR_NUM_THREADS='1',PYTHONUTF8='1')
            process=subprocess.Popen([sys.executable,str(Path(__file__)),action],cwd=PROJECT,
                env=env,stdout=log,stderr=subprocess.STDOUT,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            begun=time.perf_counter()
            while process.poll() is None:
                if (PATH/'STOP').exists() or time.perf_counter()-begun>900:
                    process.terminate();process.wait(timeout=10);raise RuntimeError('Interrupted; inspect retained logs')
                time.sleep(2)
            if process.returncode:raise RuntimeError(f'{action} failed: inspect {PATH}')
    s=read(PATH/'summary.json')
    print({k:s.get(k) for k in ('status','valid','objective','best_bound','mip_gap','total_optimization_seconds','static_route_rows')},flush=True)


if __name__=='__main__':
    {'run':run,'worker':worker,'verify':verify}[sys.argv[1]]()
