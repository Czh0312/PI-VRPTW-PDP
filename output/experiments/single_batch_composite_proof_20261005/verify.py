"""Replay every frozen site MPS at another seed with original start; no reruns."""
from pathlib import Path
from fractions import Fraction
import hashlib
import json
import math
import sys
import time
OUT = Path(__file__).resolve().parent
sys.path.insert(0,str(OUT/'candidate_source'))
import gurobipy as gp
from configuration.loader import load_routing_case
from models.routing.common.cycle import load_instance
from models.routing.common.cycle.audit import audit_solution
from models.routing.solvers.exact.single_batch_columns import problem_fingerprint
from models.routing.solvers.exact.single_batch_physical_bounds import build_physical_site,project_original_site

def read(p):return json.loads(Path(p).read_text())
def write(p,v):Path(p).write_text(json.dumps(v,ensure_ascii=False,indent=2,allow_nan=False)+'\n')
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def cost(model):
    total=Fraction(float(model.ObjCon))
    for v in model.getVars():
        if v.Obj:
            assert v.VType in (gp.GRB.INTEGER,gp.GRB.BINARY)
            assert abs(v.X-round(v.X)) <= 1e-9
            total+=Fraction(float(v.Obj))*round(v.X)
    return total

def raw_costs(p,initial):
    output={'e1':Fraction(),**{o.satellite:Fraction() for o in p.orders}}
    for layer in ('e1','e2'):
        for trip in initial[layer+'_trips']:
            home=p.depot if layer=='e1' else trip['satellite_id']
            sites=[e['site_id'] if layer=='e1' else p.order_map[e['order_id']].customer for e in trip['events']]
            path=[home]+sites+[home]
            output['e1' if layer=='e1' else home]+=sum((Fraction(float(p.distance(a,b))) for a,b in zip(path,path[1:])),Fraction())
    return output


def main():
    target=OUT/'verification';target.mkdir()
    begun=time.perf_counter();hashes=0
    for name in ('baseline','candidate'):
        for rel,value in read(OUT/(name+'_manifest.json')).items():
            assert sha(OUT/(name+'_source')/rel)==value;hashes+=1
    for rel,value in read(OUT/'input_manifest.json').items():
        assert sha(OUT/rel)==value;hashes+=1
    assert sha(OUT/'driver_frozen.py')==read(OUT/'preparation.json')['driver_sha256']
    assert sha(OUT/'plan.json')==read(OUT/'preparation.json')['plan_sha256']
    jobs=[read(p) for p in read(OUT/'jobs.json')]
    assert len(jobs)==len(read(OUT/'comparison.json'))==24
    checks=[];audits=[];rows=[];templates={};closures=[]
    e1proof={r['case']:r for r in read(OUT/'e1_independent.json')}
    for job in jobs:
        cfg,f=load_routing_case(job['input']);p=load_instance(cfg,f)
        initial=read(job['start']);r=read(job['result']);row=read(job['row'])
        assert problem_fingerprint(p)==job['original_fp']==row['original_fp']
        for key,path in (('input',job['input']),('start',job['start']),('result',job['result'])):
            assert sha(path)==row[key+'_sha256']
            if key!='result':assert sha(path)==job[key+'_sha256']
        a=audit_solution(p,initial);b=audit_solution(p,r)
        assert a['valid'] and b['valid'] and a['periodic_replay']['valid'] and b['periodic_replay']['valid']
        tasks=[(e['order_id'],e['kind']) for t in r['e2_trips'] for e in t['events']]
        assert len(tasks)==40 and set(tasks)=={(o.id,k) for o in p.orders for k in ('d','p')}
        assert row['upper']<=a['objective']+1e-6 and row['lower']<=row['upper']+1e-6
        assert math.isclose(row['upper'],b['objective'],abs_tol=1e-6)
        assert not r['info']['proven_infeasible']
        cert=r['info'].get('component_certificate');assert bool(cert)==(job['method']=='composite')
        if cert:
            assert cert['problem_sha256']==job['original_fp']
            raw=raw_costs(p,initial)
            assert {k:Fraction(*v) for k,v in cert['target_costs'].items()}==raw
            e1=cert['e1']
            if e1['complete']:
                assert Fraction(*e1['exact_cost'])==Fraction(e1proof[job['case']]['expected'])
            if cert['closed']:
                assert e1['complete'] and Fraction(*e1['exact_cost'])==raw['e1']
                assert set(cert['sites'])=={o.satellite for o in p.orders}
                assert all(x['complete'] and x['exact_match'] for x in cert['sites'].values())
                assert r['info']['proven_optimal'] and row['gap']==0 and row['lower']==row['upper']
                assert Fraction(*cert['exact_total_cost'])==sum(raw.values(),Fraction())
            else:
                assert math.isclose(cert['bound'],math.fsum([e1['bound']]+[x['bound'] for x in cert['sites'].values()]),abs_tol=1e-7)
            local=[]
            for site,x in cert['sites'].items():
                assert x['bound']<=float(raw[site])+1e-6
                if x['bound']<=0 and not cert['closed']:continue
                key=(job['case'],site)
                if key not in templates:
                    fresh,ctx=build_physical_site(p,site)
                    try:
                        base={v.VarName:(v.LB,v.UB,v.Obj,v.VType) for v in fresh.getVars()}
                        projection=project_original_site(p,ctx,initial,fix=True);fresh.update()
                        fixed={v.VarName:(v.LB,v.UB) for v in fresh.getVars()}
                        templates[key]=(base,fixed,projection,fresh.NumConstrs,fresh.NumGenConstrs)
                    finally:fresh.dispose()
                base,fixed,projection,nrows,ngens=templates[key]
                path=Path(x['model_file']);startpath=Path(x['start_file'])
                assert path.is_relative_to(Path(job['run'])) and startpath.is_relative_to(Path(job['run']))
                warm=read(startpath)
                for name,value in warm.items():
                    assert fixed[name][0]==fixed[name][1]==value
                item=dict(case=job['case'],seed=job['seed'],budget=job['budget'],site=site,closed_claim=cert['closed'],
                    claimed_bound=x['bound'],original_exact_match=x['exact_match'],model_sha256=sha(path),start_sha256=sha(startpath))
                with gp.Env(empty=True) as env:
                    env.setParam('OutputFlag',0);env.start();m=gp.read(str(path),env=env)
                    try:
                        assert m.NumVars==len(base) and m.NumConstrs==nrows and m.NumGenConstrs==ngens
                        coefficient_roundtrip=True
                        for v in m.getVars():
                            lo,hi,obj,kind=base[v.VarName]
                            assert all(math.isclose(a,b,rel_tol=1e-9,abs_tol=1e-8) for a,b in ((lo,v.LB),(hi,v.UB),(obj,v.Obj)))
                            assert kind==v.VType
                            coefficient_roundtrip &= obj==v.Obj
                            if v.VarName in warm:v.Start=warm[v.VarName]
                        # Exact coefficient roundtrip is required for a composite equality replay.
                        item['exact_coefficients_roundtrip']=coefficient_roundtrip
                        m.Params.Threads=1;m.Params.Seed=job['seed']+101;m.Params.DualReductions=0
                        m.Params.FeasibilityTol=m.Params.IntFeasTol=1e-9;m.Params.MIPGap=m.Params.MIPGapAbs=0
                        m.Params.TimeLimit=5;m.Params.OutputFlag=1;m.Params.LogToConsole=0
                        m.Params.LogFile=str(target/f'replay_{len(checks)}.log');m.optimize()
                        lb=float(m.ObjBound);finite=math.isfinite(lb) and abs(lb)<1e90
                        exact=cost(m) if m.Status==gp.GRB.OPTIMAL else None
                        close=(m.Status==gp.GRB.OPTIMAL and coefficient_roundtrip and exact==raw[site]
                            and m.ObjBound==m.ObjVal and m.MaxVio<=1e-7)
                        item.update(status=int(m.Status),replay_bound=lb if finite else None,replay_runtime=m.Runtime,
                            supported=finite and x['bound']<=lb+1e-6,exact_closure_confirmed=bool(close),
                            exact_objective=str(exact) if exact is not None else None)
                        for v in m.getVars():v.LB,v.UB=fixed[v.VarName]
                        m.Params.LogFile=str(target/f'projection_{len(checks)}.log');m.Params.TimeLimit=5;m.optimize()
                        assert m.Status==gp.GRB.OPTIMAL and math.isclose(m.ObjVal,projection['cost'],abs_tol=1e-6)
                        item.update(projection_status=int(m.Status),projection_runtime=m.Runtime,all_site_tasks=projection['tasks'])
                    finally:m.dispose()
                checks.append(item);local.append(item);write(target/'site_checks.json',checks)
                print(f"Site {len(checks)} {job['case']} {site}: supported={item['supported']} equality={item['exact_closure_confirmed']}",flush=True)
            if cert['closed']:
                closures.append(dict(case=job['case'],seed=job['seed'],budget=job['budget'],
                    verified=len(local)==len(cert['sites']) and all(x['exact_closure_confirmed'] for x in local)))
        audits.append(dict(case=job['case'],seed=job['seed'],budget=job['budget'],method=job['method'],full_audit=True,periodic_replay=True,all40tasks=True))
        rows.append(row)
    groups={}
    for row in rows:groups.setdefault((row['case'],row['seed'],row['budget']),{})[row['method']]=row
    pairs=[]
    for (case,seed,budget),group in groups.items():
        base,candidate=group['baseline'],group['composite']
        assert base['original_fp']==candidate['original_fp'] and base['start_sha256']==candidate['start_sha256']
        gap=base['gap']-candidate['gap'];both=base['proven_optimal'] and candidate['proven_optimal']
        faster=both and candidate['runtime']<=.9*base['runtime']
        slower=both and candidate['runtime']>=1.1*base['runtime']
        lost=base['proven_optimal'] and not candidate['proven_optimal']
        pairs.append(dict(case=case,seed=seed,budget=budget,baseline=base,composite=candidate,gap_improvement=gap,
            proof_speed_improvement=bool(faster),proof_time_regression=bool(slower),lost_proof=bool(lost),
            win=bool(gap>1e-5 or faster),loss=bool(gap< -1e-5 or slower or lost)))
    wins=[p for p in pairs if p['win']];losses=[p for p in pairs if p['loss']]
    correctness=all(x['supported'] for x in checks) and all(c['verified'] for c in closures)
    gate=correctness and len(wins)>=8 and not losses and len({p['case'] for p in wins})==2 and {p['seed'] for p in wins}=={42,43,44} and {p['budget'] for p in wins}=={15,30}
    loads=[read(p) for p in (OUT/'runs').rglob('load_*.json')]
    summary=dict(status='VERIFIED' if correctness else 'INDEPENDENT_REPLAY_INCONCLUSIVE',hashes_checked=hashes,
        original_outputs_audited=24,all_task_and_periodic_replays=24,positive_site_checks=len(checks),
        supported_site_checks=sum(c['supported'] for c in checks),fixed_mps_projections=len(checks),
        closures_claimed=len(closures),closures_confirmed=sum(c['verified'] for c in closures),
        pairs=12,wins=len(wins),losses=len(losses),gap_wins=sum(p['gap_improvement']>1e-5 for p in pairs),
        gap_losses=sum(p['gap_improvement']< -1e-5 for p in pairs),
        acceptance_gate=bool(gate),production_default_adopted=False,registered_seconds=540,
        actual_runtime=sum(r['runtime'] for r in rows),component_runtime=sum(r['component_runtime'] for r in rows),
        maximum_total_overrun=max(r['runtime']-r['budget'] for r in rows),
        process_wall=sum(x.get('process_wall',0) for x in loads),verification_wall=time.perf_counter()-begun,
        minimum_load1=min(x['loadavg'][0] for x in loads),maximum_load1=max(x['loadavg'][0] for x in loads),
        caveat='Native numerical MILP optimality, not a symbolic rational certificate. Host load uncontrolled. No altered timing reruns.')
    write(target/'audits.json',audits);write(target/'closures.json',closures);write(target/'pairs.json',pairs);write(target/'summary.json',summary)
    print(json.dumps(summary,ensure_ascii=False,indent=2),flush=True)

if __name__=='__main__':main()
