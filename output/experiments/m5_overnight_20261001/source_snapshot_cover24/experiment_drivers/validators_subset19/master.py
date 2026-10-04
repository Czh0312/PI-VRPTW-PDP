"""Independent coefficient audit of pure-linear three-task subset rows."""
from pathlib import Path
import json,sys,time,hashlib,gzip,itertools
import numpy as np
import gurobipy as gp
HERE=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(HERE/'source_snapshot_subset19'))
from configuration.loader import load_routing_case
from models.routing.common.cycle import load_instance
begun=time.perf_counter()
sha=lambda p:hashlib.sha256(Path(p).read_bytes()).hexdigest()
for path,digest in json.loads((HERE/'manifest_subset19_input.json').read_text())['sha256'].items():
    assert sha(path)==digest
prior=json.loads((HERE/'s1master18/master/independent_validation.json').read_text())
assert prior['matrix']['original_matrix_exactly_identical']
assert prior['matrix']['declared_certified_original_bound_change']['independent_floor_replay']['status']==2
problem=load_instance(*load_routing_case(str(HERE/'inputs/case.json')))
base_path=HERE/'s1master18/master/no_good.mps.gz';changed_path=HERE/'subset19/master/with_rows.mps.gz'
base=gp.read(str(base_path));changed=gp.read(str(changed_path))
try:
    old_vars,new_vars=base.getVars(),changed.getVars()
    n,m=base.NumVars,base.NumConstrs
    assert n==changed.NumVars
    names=base.getAttr('VarName',old_vars)
    for attr in ('VarName','LB','UB','VType','Obj'):
        assert base.getAttr(attr,old_vars)==changed.getAttr(attr,new_vars)
    assert base.ModelSense==changed.ModelSense==gp.GRB.MINIMIZE and base.ObjCon==changed.ObjCon
    old_rows,new_rows=base.getConstrs(),changed.getConstrs()
    for attr in ('ConstrName','Sense','RHS'):
        assert base.getAttr(attr,old_rows)==changed.getAttr(attr,new_rows[:m])
    retained=set(base.getAttr('ConstrName',old_rows))
    def entries(path):
        with gzip.open(path,'rt') as stream:
            active=False
            for line in stream:
                f=line.split()
                if not active:
                    if f==['COLUMNS']:active=True
                    continue
                if f==['RHS']:break
                if len(f)>=2 and f[1]=="'MARKER'":continue
                assert len(f) in (3,5),f
                for j in range(1,len(f),2):
                    if f[j] in retained:yield f[0],f[j],float(f[j+1])
    nonzeros=0
    for a,b in itertools.zip_longest(entries(base_path),entries(changed_path)):
        assert a==b,(a,b);nonzeros+=1
    def indicators(model):
        d={}
        for row in model.getGenConstrs():
            assert row.GenConstrType==gp.GRB.GENCONSTR_INDICATOR
            v,value,e,sense,rhs=model.getGenConstrIndicator(row)
            d[row.GenConstrName]=(v.VarName,value,sense,rhs,{e.getVar(i).VarName:e.getCoeff(i) for i in range(e.size())})
        return d
    assert indicators(base)==indicators(changed)
    tasks=[(o.id,k) for o in problem.orders if o.satellite=='S1' for k in ('d','p')]
    positions={k:i for i,k in enumerate(tasks)};masks={}
    for i,key in enumerate(tasks):
        row=base.getConstrByName(f'joint_cover_1_{i}');e=base.getRow(row)
        assert row.Sense=='=' and row.RHS==1
        for j in range(e.size()):
            v=e.getVar(j);assert e.getCoeff(j)==1 and v.VarName.startswith('joint_route_1_')
            masks[v.index]=masks.get(v.index,0)|(1<<i)
    triangles=json.loads((HERE/'subset19/triangles.json').read_text());expected={}
    assert len(triangles)==16
    for i,t in enumerate(triangles):
        keys=[tuple(k) for k in t['tasks']]
        assert len(keys)==3 and len(set(keys))==3 and all(k in positions for k in keys)
        bits=[1<<positions[k] for k in keys];mask=sum(bits)
        pairs={bits[0]|bits[1],bits[0]|bits[2],bits[1]|bits[2],mask}
        terms={names[j]:1 for j,value in masks.items() if (value&mask) in pairs}
        expected[f'subset19_{i}']=('<',1,terms)
    observed={}
    for row in new_rows[m:]:
        e=changed.getRow(row)
        observed[row.ConstrName]=(row.Sense,row.RHS,{e.getVar(i).VarName:e.getCoeff(i) for i in range(e.size())})
    assert observed==expected
    projection=json.loads((HERE/'subset19/master/full_projection.json').read_text())
    assert projection['status']==2 and abs(projection['objective']-2235.9725985686455)<1e-7
    initial=json.loads((HERE/'s1full16/initial_result.json').read_text())
    tripsets=[{(e['order_id'],e['kind']) for e in t['events']} for t in initial['e2_trips'] if t['satellite_id']=='S1']
    for triangle in triangles:
        subset={tuple(k) for k in triangle['tasks']}
        assert sum(len(s&subset)>=2 for s in tripsets)<=1
    result=dict(original_variables=n,original_rows=m,original_nonzeros=nonzeros,
        original_variables_and_matrix_unchanged=True,original_indicators_checked=base.NumGenConstrs,
        added_variables=0,added_rows_checked=len(expected),original_cover_masks_independently_decoded=True,
        three_task_coefficients_exact=True,full_upper_triangle_projection_checked=True,
        prior_validation=str(HERE/'s1master18/master/independent_validation.json'),
        base_sha256=sha(base_path),changed_sha256=sha(changed_path),
        full_proven_optimal=False,full_proven_infeasible=False,new_bound_promoted=False,
        bound_replay_skipped='No improved bound; matrix scope audit only.',runtime=time.perf_counter()-begun)
finally:base.dispose();changed.dispose()
(HERE/'subset19/master/independent_validation.json').write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps(result,indent=2))
