"""Compare the joint MPS to the already verified E1 relaxation, then project a full schedule."""
from pathlib import Path
import math
from models.routing.common.cycle.audit import audit_solution
from validation_e1 import all_patterns

def close(a,b):assert math.isclose(a,b,rel_tol=1e-10,abs_tol=1e-6),(a,b)
def expression(expr):return {expr.getVar(i).VarName:expr.getCoeff(i) for i in range(expr.size())}
def equal_expr(a,b):
    a,b=expression(a),expression(b);assert set(a)==set(b)
    for k in a:close(a[k],b[k])
def verify_joint(p,result,cert,path,original_e1_path,conditional):
    import gurobipy as gp
    from gurobipy import GRB
    audit=audit_solution(p,result);assert audit['valid'] and audit['periodic_replay']['valid']
    assert cert['scope']=='M5_E1_inventory_plus_conditional_E2_floors'
    assert not cert['proven_optimal'] and not cert['proven_infeasible']
    close(cert['bound'],max(0,cert['raw_bound']-cert['margin']))
    assert cert['bound']<=audit['objective']+1e-6
    old=gp.read(str(original_e1_path));m=gp.read(str(path))
    try:
        patterns=all_patterns(p);assert len(patterns)==cert['patterns']==11706
        # Old E1 exports contain three safe per-batch distance-floor rows.
        # The new joint base omits those rows and otherwise keeps the exact
        # same variables, linear rows and indicator rows. Removing valid
        # rows only relaxes the already verified full-M5 projection.
        prefix=old.NumConstrs-3
        assert m.NumConstrs==prefix+len(p.satellites)+len(cert['conditional_rows'])
        old_vars={v.VarName:v for v in old.getVars()};new_vars={v.VarName:v for v in m.getVars()}
        assert set(new_vars)-set(old_vars)=={f'conditional_E2_cost[{s}]' for s in p.satellites}
        for name,v in old_vars.items():
            new=new_vars[name];assert v.VType==new.VType;close(v.LB,new.LB);close(v.UB,new.UB);close(v.Obj,new.Obj)
        for left,right in zip(old.getConstrs()[:prefix],m.getConstrs()[:prefix]):
            assert left.Sense==right.Sense;close(left.RHS,right.RHS);equal_expr(old.getRow(left),m.getRow(right))
        assert old.NumGenConstrs==m.NumGenConstrs
        for left,right in zip(old.getGenConstrs(),m.getGenConstrs()):
            assert left.GenConstrType==right.GenConstrType==GRB.GENCONSTR_INDICATOR
            lv,lval,le,lsense,lrhs=old.getGenConstrIndicator(left)
            rv,rval,re,rsense,rrhs=m.getGenConstrIndicator(right)
            assert lv.VarName==rv.VarName and lval==rval and lsense==rsense;close(lrhs,rrhs);equal_expr(le,re)
        for site in p.satellites:
            theta=new_vars[f'conditional_E2_cost[{site}]'];close(theta.Obj,1);close(theta.LB,0)
        expected=[]
        for site in p.satellites:expected.append((cert['baselines'][site],{f'conditional_E2_cost[{site}]':1.0}))
        for row in cert['conditional_rows']:
            key=(row['site'],row['batch'],row['threshold']);leaf=conditional[key]
            close(row['conditional_bound'],leaf['site']['bound']);close(row['base_bound'],cert['baselines'][row['site']])
            late=[]
            for i,pattern in enumerate(patterns):
                if pattern['batch']!=row['batch'] or row['site'] not in pattern['delivery']:continue
                now=p.batch_map[pattern['batch']].start+p.operating_policy.e1_load_time;previous=p.depot
                for site in pattern['delivery']:
                    now+=p.travel(previous,site)+p.service(site);previous=site
                    if site==row['site'] and now>=row['threshold']:late.append(i)
            assert late==row['late_indices']
            delta=row['conditional_bound']-row['base_bound'];assert delta>0
            coeff={f'conditional_E2_cost[{row["site"]}]':1.0};coeff.update({f'route[{i}]':-delta for i in late})
            expected.append((row['base_bound'],coeff))
        for con,(rhs,coeff) in zip(m.getConstrs()[prefix:],expected):
            assert con.Sense=='>';close(con.RHS,rhs);actual=expression(m.getRow(con));assert set(actual)==set(coeff)
            for name,value in coeff.items():close(actual[name],value)
        m.Params.OutputFlag=0;m.Params.Threads=1;m.Params.Seed=42;m.Params.MIPGap=0;m.Params.MIPGapAbs=0;m.Params.TimeLimit=30
        m.optimize();assert m.ObjBound>=cert['raw_bound']-1e-5
        if cert['relaxation_optimal']:assert m.Status==GRB.OPTIMAL;close(m.ObjVal,cert['raw_bound'])
        reoptimized=m.ObjBound
        def fix(name,value):
            v=m.getVarByName(name);assert v is not None,name;v.LB=v.UB=value
        lookup={(r['batch'],r['delivery'],r['pickup']):i for i,r in enumerate(patterns)};selected=set();amounts={}
        for trip in result['e1_trips']:
            d=tuple(e['site_id'] for e in trip['events'] if e['kind']=='d');pick=tuple(e['site_id'] for e in trip['events'] if e['kind']=='p')
            i=lookup[trip['batch_id'],d,pick];selected.add(i)
            amounts.update({(i,e['site_id']):e['quantity'] for e in trip['events'] if e['kind']=='p'})
        for i,r in enumerate(patterns):
            close(new_vars[f'route[{i}]'].Obj,r['cost']);fix(f'route[{i}]',int(i in selected))
            for site in r['pickup']:fix(f'quantity_{i}_{site}',amounts.get((i,site),0))
        actual_site={s:0.0 for s in p.satellites}
        for trip in result['e2_trips']:
            previous=trip['satellite_id']
            for event in trip['events']:
                oid,kind=event['order_id'],event['kind'];order=p.order_map[oid]
                fix(('delivery_' if kind=='d' else 'pickup_time_')+oid,event['start'])
                actual_site[trip['satellite_id']]+=p.distance(previous,order.customer);previous=order.customer
                if kind=='p':
                    fix('e2_return_'+oid,trip['return_complete'])
                    first=next(b.id for b in p.operating_policy.batches if trip['return_complete']<=b.cutoff)
                    for batch in p.operating_policy.batches:fix(f'cutoff_{oid}_{batch.id}',int(first==batch.id))
            actual_site[trip['satellite_id']]+=p.distance(previous,trip['satellite_id'])
        for site,cost in actual_site.items():fix(f'conditional_E2_cost[{site}]',cost)
        m.optimize();assert m.Status==GRB.OPTIMAL;close(m.ObjVal,audit['objective'])
        return dict(path=str(path),reoptimized_bound=reoptimized,full_schedule_projection=True,
            projected_objective=m.ObjVal,patterns=len(patterns),compared_base_linear_rows=prefix,compared_indicators=m.NumGenConstrs)
    finally:old.dispose();m.dispose()
