"""Sequential cold-cache control/treatment, then independent MPS/projection QA."""
import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import time


ROOT=Path(__file__).resolve().parents[2]


def write(path,value):
    Path(path).write_text(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False)+'\n')


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def worker(job_path):
    job=json.loads(Path(job_path).read_text())
    sys.path.insert(0,job['source_snapshot'])
    from configuration.loader import load_routing_case
    from models.routing.common.cycle import load_instance
    from models.routing.variants.model_5.exact import logic_benders_certificate
    p=load_instance(*load_routing_case(job['case']))
    initial=json.loads(Path(job['initial']).read_text())
    r=logic_benders_certificate(p,initial_result=initial,time_limit=job['time_limit'],seed=job['seed'],
        max_rounds=0 if job['variant']=='control' else 8,oracle_time_limit=10,master_time_limit=20,
        reduction_checks=8,diagnostics_dir=job['diagnostics'])
    write(Path(job_path).with_name('result.json'),r)
    print(json.dumps(dict(variant=job['variant'],seed=job['seed'],bound=r['bound'],upper=r['upper_bound'],
        runtime=r['runtime'],cuts=len(r['cuts']),oracles=r['cache']['oracle_calls'],status=r['status'])),flush=True)


def signature(model):
    model.update()
    variables={v.VarName:(v.VType,v.LB,v.UB,v.Obj) for v in model.getVars()}
    def expr(row):
        return sorted((row.getVar(i).VarName,row.getCoeff(i)) for i in range(row.size()))
    linear={c.ConstrName:(c.Sense,c.RHS,expr(model.getRow(c))) for c in model.getConstrs()}
    general={}
    for c in model.getGenConstrs():
        flag,value,row,sense,rhs=model.getGenConstrIndicator(c)
        general[c.GenConstrName]=(flag.VarName,value,sense,rhs,expr(row))
    return dict(variables=variables,linear=linear,general=general,sense=model.ModelSense,
                constant=model.ObjCon)


def equal(a,b):
    import math
    if isinstance(a,(int,float)) and isinstance(b,(int,float)):
        return a==b or math.isclose(a,b,rel_tol=1e-12,abs_tol=1e-9)
    if isinstance(a,dict):return set(a)==set(b) and all(equal(a[k],b[k]) for k in a)
    if isinstance(a,(tuple,list)):return len(a)==len(b) and all(equal(x,y) for x,y in zip(a,b))
    return a==b


def validate(job_path):
    job=json.loads(Path(job_path).read_text());sys.path.insert(0,job['source_snapshot'])
    from configuration.loader import load_routing_case
    from models.routing.common.cycle import load_instance
    from models.routing.common.cycle.audit import audit_solution
    from models.routing.solvers.exact.multibatch_logic_benders import _read_model,_write_model,business_fingerprint,source_fingerprint
    from models.routing.solvers.exact.multibatch_route_pricing import route_context
    from models.routing.solvers.exact.multibatch_route_timing import enumerate_timed_route_universe
    from models.routing.solvers.exact.multibatch_joint_inventory import build_joint_inventory,set_joint_projection
    from models.routing.solvers.exact.multibatch_physical_cuts import lift_physical_costs,add_physical_cut,project_physical_costs
    import gurobipy as gp
    p=load_instance(*load_routing_case(job['case']));r=json.loads(Path(job_path).with_name('result.json').read_text())
    audit=audit_solution(p,r['result']);assert audit['valid']
    assert r['identity']==dict(business=business_fingerprint(p),source=source_fingerprint())
    best=max(r['rounds'],key=lambda row:row['bound'])
    report=dict(full_upper_audit=audit,full_proven_optimal=False,full_proven_infeasible=False,
                identity_checked=True,bound=r['bound'],original_input=True)
    with gp.Env(empty=True) as env:
        env.setParam('OutputFlag',0);env.start()
        frozen=_read_model(gp,best['master_mps'],env)
        try:
            frozen.Params.Threads=1;frozen.Params.Seed=job['seed']+100
            frozen.Params.TimeLimit=30;frozen.Params.BestBdStop=r['bound']
            frozen.optimize()
            report['independent_bound_replay']=dict(status=int(frozen.Status),raw_bound=float(frozen.ObjBound),
                                                   supported=frozen.ObjBound+1e-6>=r['bound'],runtime=frozen.Runtime)
            assert report['independent_bound_replay']['supported']
        finally:frozen.dispose()
        final=_read_model(gp,r['rounds'][-1]['master_mps'],env)
        try:final_signature=signature(final)
        finally:final.dispose()
    domains={}
    for site in sorted({o.satellite for o in p.orders}):
        c=route_context(p,site);u=enumerate_timed_route_universe(p,c);assert u['complete']
        domains[site]=c,u
    model,context=build_joint_inventory(p,domains)
    try:
        for v in context['choices']:v.VType=gp.GRB.BINARY
        lift_physical_costs(model,context)
        # A last unprocessed cut may not be in the final solved MPS. Rebuild
        # only cuts whose rows/flags actually occur in that frozen matrix.
        cuts=[]
        for i,c in enumerate(r['cuts']):
            prefix='lbbd_'+str(i)
            if not any(n.startswith(prefix+'_') for n in final_signature['linear']):break
            proof=json.loads(Path(c['cache_record']).read_text())
            assert proof['identity']==r['identity'] and proof['certificate']==c['certificate']
            mps=Path(c['cache_record']).parent/proof['proof_file']
            assert sha(mps)==proof['proof_sha256']
            cuts.append(add_physical_cut(p,model,context,c['certificate'],name=prefix))
        assert equal(signature(model),final_signature),'Exported master differs from complete original-domain reconstruction'
        report['matrix_reconstruction']=dict(complete_choices=len(context['choices']),
            variables=model.NumVars,rows=model.NumConstrs,indicators=model.NumGenConstrs,
            cuts_checked=len(cuts),all_original_and_added_coefficients_checked=True)
        set_joint_projection(p,context,r['result'],fix=True)
        project_physical_costs(p,context,r['result'],cuts=cuts,fix=True)
        model.Params.TimeLimit=20;model.optimize()
        assert model.SolCount and model.ObjVal<=audit['objective']+1e-5
        report['full_projection']=dict(status=int(model.Status),objective=model.ObjVal,
                                      complete_upper=audit['objective'],valid=True)
        _write_model(model,Path(job_path).with_name('fixed_full_projection.mps.gz'))
    finally:model.dispose()
    write(Path(job_path).with_name('validation.json'),report)
    print('independent validation passed',flush=True)


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--output');parser.add_argument('--time-limit',type=float,default=60)
    parser.add_argument('--seeds',type=int,nargs='+',default=[42,43,44])
    parser.add_argument('--cases',type=int,nargs='+',default=[20])
    parser.add_argument('--worker');parser.add_argument('--validate')
    args=parser.parse_args()
    if args.worker:return worker(args.worker)
    if args.validate:return validate(args.validate)
    if not args.output:parser.error('--output required')
    if any(c not in (20,30) for c in args.cases):parser.error('Only the frozen 20/30 originals are selected here')
    out=Path(args.output).resolve();out.mkdir(parents=True,exist_ok=False)
    snapshot=out/'source_snapshot';snapshot.mkdir()
    for folder in ('models','configuration'):
        shutil.copytree(ROOT/folder,snapshot/folder,ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
    shutil.copy2(Path(__file__),out/'benchmark_driver.py')
    old=ROOT/'output/experiments/m5_overnight_20261001'
    protected=[old/'combined_certificate.json',old/'best_full_result.json',old/'newpoint9/master/no_good.mps.gz',
               old/'master49/combined_certificate.json',old/'medium25/1/combined_certificate.json',old/'checkpoint.json']
    protected_sha={str(p):sha(p) for p in protected};write(out/'protected_before.json',protected_sha)
    jobs=[]
    from configuration.loader import load_routing_case
    from models.routing.common.cycle import load_instance
    from models.routing.solvers.exact.multibatch_logic_benders import business_fingerprint
    for customers in args.cases:
        original=old/('medium25/'+('0' if customers==20 else '1'))/'inputs'
        destination=out/'inputs'/str(customers)
        shutil.copytree(original,destination)
        case=json.loads((destination/'case_relocated.json').read_text())
        for name in ('data','orders','routing_config','dataset_metadata','pool_config'):
            case[name]=str(destination/Path(case[name]).name)
        write(destination/'case.json',case)
        before=load_instance(*load_routing_case(original/'case_relocated.json'))
        after=load_instance(*load_routing_case(destination/'case.json'))
        assert business_fingerprint(before)==business_fingerprint(after)
        write(destination/'business_fingerprint.json',dict(sha256=business_fingerprint(after),unchanged=True))
        for seed in args.seeds:
            for variant in ('control','automatic'):
                directory=out/(str(customers)+'_'+str(seed)+'_'+variant);directory.mkdir()
                job=dict(case=str(destination/'case.json'),initial=str(destination/'initial_result.json'),
                    customers=customers,seed=seed,variant=variant,time_limit=args.time_limit,
                    source_snapshot=str(snapshot),diagnostics=str(directory/'diagnostics'))
                path=directory/'job.json';write(path,job);jobs.append(path)
    write(out/'checkpoint.json',dict(status='RUNNING_SEQUENTIAL',completed=[],jobs=[str(p) for p in jobs]))
    completed=[]
    for path in jobs:
        began=time.perf_counter()
        with path.with_name('worker.log').open('w') as log:
            result=subprocess.run([sys.executable,str(out/'benchmark_driver.py'),'--worker',str(path)],
                                  stdout=log,stderr=subprocess.STDOUT)
        if result.returncode:raise RuntimeError('Worker failed '+str(path))
        completed.append(str(path))
        write(out/'checkpoint.json',dict(status='RUNNING_SEQUENTIAL',completed=completed,jobs=[str(p) for p in jobs]))
        print(path.parent.name+' finished in '+str(round(time.perf_counter()-began,2))+'s',flush=True)
    # Verification follows all measured runs; no concurrent heavy experiments.
    for path in jobs:
        with path.with_name('validation.log').open('w') as log:
            result=subprocess.run([sys.executable,str(out/'benchmark_driver.py'),'--validate',str(path)],
                                  stdout=log,stderr=subprocess.STDOUT)
        if result.returncode:raise RuntimeError('Validation failed '+str(path))
        print(path.parent.name+' validated',flush=True)
    after={str(p):sha(p) for p in protected};assert after==protected_sha
    rows=[]
    for path in jobs:
        job=json.loads(path.read_text());r=json.loads(path.with_name('result.json').read_text())
        rows.append(dict(customers=job['customers'],seed=job['seed'],variant=job['variant'],
            bound=r['bound'],upper=r['upper_bound'],gap=r['gap'],runtime=r['runtime'],cuts=len(r['cuts']),
            oracles=r['cache']['oracle_calls'],status=r['status'],budget_overrun_seconds=r['budget_overrun_seconds']))
    write(out/'summary.json',dict(rows=rows,same_input=True,same_initial=True,same_total_budget=args.time_limit,
        sequential=True,cold_cache=True,all_independent_validations_passed=True,protected_certificates_unchanged=True,
        proof_scope='Verified complete-domain lower and audited upper; no full M5 optimality claim'))
    write(out/'protected_after.json',after)
    write(out/'checkpoint.json',dict(status='COMPLETE_VERIFIED',active_process=None,completed=completed))
    manifest={str(p):sha(p) for p in out.rglob('*') if p.is_file() and p.name not in ('manifest.json',) and not p.name.endswith('.lock') and '__pycache__' not in str(p)}
    write(out/'manifest.json',dict(sha256=manifest))
    print(json.dumps(rows,ensure_ascii=False),flush=True)


if __name__=='__main__':main()
