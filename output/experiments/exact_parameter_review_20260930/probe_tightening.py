"""Diagnostic ablation of implied E1 time bounds; production files unchanged."""
from pathlib import Path
import hashlib
import json
import math
import sys
import time
ROOT=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT))
OUT=Path(__file__).resolve().parent
from configuration.loader import load_routing_case
from models.routing.common.cycle import load_instance
from models.routing.common.cycle.precheck import travel_lower_bounds
from models.routing.solvers.exact import solver
from models.routing.solvers.exact.domains import assignment_domains
from models.routing.service import solve_routing
from tests.routing.test_single_batch_solver_audit import tiny_problem,enumerate_tiny

ORIGINAL=solver.build_model


def tighten(p,m,ctx,travel=None):
    """Necessary bounds only. Direct arcs are never used as path lower bounds."""
    travel=travel or travel_lower_bounds(p)
    policy=p.operating_policy
    b=policy.batches[0]
    domains=assignment_domains(p,travel)['domains'] if p.free_assignment else None
    for s in p.satellites:
        eligible=[o for o in p.orders if s in domains[o.customer]] if domains is not None else [o for o in p.orders if o.satellite==s]
        if not eligible:
            continue
        delivery=m.getVarByName(f'delivery_{s}_{b.id}')
        supply_lb=b.start+policy.e1_load_time+travel['outward'][p.depot][s]+p.service(s)
        dues={o.id:min(o.delivery_window[1],b.cutoff-o.service_time)
              -policy.e2_dispatch_prep_time-travel['outward'][s][o.customer] for o in eligible}
        if p.free_assignment:
            active=m.getVarByName(f'active_{s}')
            m.addConstr(delivery>=supply_lb*active)
            m.addConstr(delivery<=max(dues.values())*active)
            delivery.UB=max(0,max(dues.values()))
            for o in eligible:
                m.addConstr((ctx['assignments'][o.customer,s]==1)>>(delivery<=dues[o.id]))
        else:
            delivery.LB=supply_lb
            delivery.UB=min(dues.values())
        for r in ctx['e1']:
            for i,node in enumerate(r['nodes']):
                if node['site']!=s:
                    continue
                if node['kind']=='d':
                    lo=supply_lb-p.service(s)
                    hi=(max(dues.values()) if p.free_assignment else min(dues.values()))-p.service(s)
                else:
                    lo=b.cutoff
                    # From this pickup the trip must return, unload, and clean.
                    hi=min(b.end,policy.cycle_end-policy.cleaning_duration,
                           policy.cycle_end-policy.e1_turnaround_time)
                    hi-=p.service(s)+travel['outward'][s][p.depot]+policy.depot_return_unload_time
                r['times'][i].UB=max(0,hi)
                m.addConstr(r['times'][i]>=lo*r['visit'][i])
                m.addConstr(r['times'][i]<=hi*r['visit'][i])
    m.update()


class Instrumented:
    def __init__(self,model,stats):
        self.model,self.stats=model,stats
    def __getattr__(self,key):
        return getattr(self.model,key)
    def optimize(self):
        from gurobipy import GRB
        samples=self.stats.setdefault('root_bound_samples',[])
        def callback(m,where):
            if where==GRB.Callback.MIP:
                node=m.cbGet(GRB.Callback.MIP_NODCNT)
                bound=m.cbGet(GRB.Callback.MIP_OBJBND)
                if node<1 and abs(bound)<1e50 and (not samples or bound!=samples[-1]['bound']):
                    samples.append(dict(seconds=m.cbGet(GRB.Callback.RUNTIME),bound=bound))
        self.model.optimize(callback)
        self.stats.update(status=self.model.Status,nodes=self.model.NodeCount,
            solve_seconds=self.model.Runtime,solutions=self.model.SolCount)
        if self.model.SolCount:
            self.stats.update(objective=self.model.ObjVal,bound=self.model.ObjBound,gap=self.model.MIPGap)


def run(p,variant,budget,tag):
    models=[]
    def build(problem,**kwargs):
        m,ctx=ORIGINAL(problem,**kwargs)
        if variant=='e1_implied_bounds':
            tighten(problem,m,ctx,kwargs.get('travel_bounds'))
        stat=dict(free=problem.free_assignment,variables=m.NumVars,linear=m.NumConstrs,indicators=m.NumGenConstrs)
        models.append(stat)
        m.Params.LogFile=str(OUT/f'{tag}_{len(models)}.log')
        return Instrumented(m,stat),ctx
    solver.build_model=build
    try:
        result=solve_routing(model=p.model_number,method='exact',problem=p,exact_time=budget,seed=42)
    finally:
        solver.build_model=ORIGINAL
    (OUT/f'{tag}_result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    return dict(tag=tag,model=p.model_number,variant=variant,budget=budget,
        valid=result.get('audit',{}).get('valid',False),info=result['info'],models=models)


if __name__=='__main__':
    checks=[]
    for number in (1,2,3,4):
        for seed in range(6):
            p=tiny_problem(number,seed)
            expected=enumerate_tiny(p)
            r=run(p,'e1_implied_bounds',5,f'oracle_M{number}_{seed}')
            info=r['info']
            ok=bool(info.get('proven_infeasible')) if math.isinf(expected) else (
                r['valid'] and info['proven_optimal'] and abs(info['objective']-expected)<1e-7)
            checks.append(dict(model=number,seed=seed,expected=None if math.isinf(expected) else expected,
                actual=info.get('objective'),passed=ok))
            assert ok,(number,seed,r)
    (OUT/'tightening_oracle.json').write_text(json.dumps(checks,indent=2)+'\n')
    print('ORACLE',len(checks),'passed',flush=True)
    rows=[]
    for customers in (7,10):
        case=f'data/single_batch/small/zhou_Set1_{customers}-2-1/case.json'
        cfg,fleet=load_routing_case(case)
        inputs=[case,cfg['data'],cfg['orders'],cfg['routing_config'],cfg['operating_policy']]
        input_hashes={p:hashlib.sha256((ROOT/p).read_bytes()).hexdigest() for p in inputs}
        for path in inputs:
            target=OUT/'input_snapshot'/path
            target.parent.mkdir(parents=True,exist_ok=True)
            target.write_bytes((ROOT/path).read_bytes())
        for number in (1,2,3,4):
            for variant in ('baseline','e1_implied_bounds'):
                cfg['model']=number
                p=load_instance(cfg,fleet)
                tag=f'zhou{customers}_M{number}_{variant}'
                row=run(p,variant,10,tag)
                row.update(case=case,input_hashes=input_hashes)
                rows.append(row)
                (OUT/'ablation.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2)+'\n')
                print(tag,json.dumps(dict(valid=row['valid'],**{k:row['info'].get(k) for k in (
                    'status','objective','best_bound','mip_gap','runtime','proven_optimal')})),flush=True)
