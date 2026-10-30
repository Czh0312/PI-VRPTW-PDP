"""Second-stage static route-bound probe using the first-stage frozen source."""
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import time
from xml.etree import ElementTree
from types import SimpleNamespace

HERE=Path(__file__).resolve().parent
PROJECT=HERE.parents[2]
BASE=PROJECT/'output/experiments/m8_temporal_preprocess_ablation_20261024_v1'


def read(path):return json.loads(path.read_text(encoding='utf-8-sig'))
def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def write(path,value):
    temp=path.with_name(path.name+'.tmp')
    temp.write_text(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
    temp.replace(path)


def imports():
    sys.path.insert(0,str(BASE/'source'))
    from tools.configuration.loader import load_routing_case
    from models.common.cycle import load_instance
    return load_routing_case,load_instance


def validate():
    m=read(HERE/'manifest.json')
    assert sha(BASE/'manifest.json')==m['dependency_manifest_sha256']
    for name,digest in m['own_files'].items():assert sha(HERE/name)==digest,name
    for name,digest in read(BASE/'manifest.json')['files'].items():assert sha(BASE/name)==digest,name
    return m


def prepare():
    if (HERE/'manifest.json').exists():raise RuntimeError('Already prepared')
    suites=list(ElementTree.parse(HERE/'validation.xml').getroot().iter('testsuite'))
    validation={k:sum(int(s.get(k,0)) for s in suites) for k in ('tests','failures','errors','skipped')}
    assert validation['tests']>0 and all(validation[k]==0 for k in ('failures','errors','skipped'))
    loader,load=imports()
    from route_bounds import subset_bounds
    checks=[]
    for number in range(1,10):
        case=f'S{number:02d}'
        p=load(*loader(BASE/'cases'/f'{case}.json',use_pool_config=False))
        result=read(PROJECT/f'output/experiments/small7_m8_alns300_20261024_v1/runs/{case}_m8_alns_300s_s42/attempt_001/result.json')
        keys={site:{(o.id,k):2*i+offset for i,o in enumerate([o for o in p.orders if o.satellite==site])
                    for offset,k in [(0,'d'),(1,'p')]} for site in p.satellites}
        edges={site:[] for site in p.satellites}
        for trip in result['e2_trips']:
            site=trip['satellite_id']
            tasks=[keys[site][e['order_id'],e['kind']] for e in trip['events']]
            edges[site].extend(zip(tasks,tasks[1:]))
        n=0
        for row in subset_bounds(p):
            tasks=set(row['tasks'])
            internal=sum(a in tasks and b in tasks for a,b in edges[row['site']])
            assert internal<=len(tasks)-row['minimum_routes'],(case,row,internal)
            n+=1
        checks.append(dict(case=case,checked_bounds=n,satisfied=True))
    write(HERE/'witness_checks.json',checks)
    write(HERE/'manifest.json',dict(dependency_manifest_sha256=sha(BASE/'manifest.json'),
        own_files={name:sha(HERE/name) for name in ('driver.py','route_bounds.py','test_route_bounds.py')},
        cases=['S01','S03'],seconds=300,seed=42,preprocessing=True,vehicle_symmetry=False,
        static_route_capacity_bounds=True,external_starts=False,custom_callback_cuts=False,
        budget_scope='optimization_only; build and audit separately reported',
        selected_test_counts=validation,validation_xml_sha256=sha(HERE/'validation.xml')))
    print(json.dumps(dict(prepared=True,witnesses_checked=len(checks))),flush=True)


def worker(case):
    manifest=validate();loader,load=imports()
    from experiments.runs import probe_model8_physical_bounds as probe
    from route_bounds import add_capacity_bounds
    original=probe.build_model
    statistics={}
    def fixed(p,**kwargs):
        model,context=original(p,**kwargs)
        stats=add_capacity_bounds(p,model,context)
        context['route_capacity_bounds']=stats
        statistics[len(p.operating_policy.batches)]=stats
        return model,context
    probe.build_model=fixed
    p=load(*loader(BASE/'cases'/f'{case}.json',use_pool_config=False))
    path=HERE/'runs'/case;path.mkdir(parents=True,exist_ok=True)
    args=SimpleNamespace(seconds=manifest['seconds'],seed=42,formulation='compact',
                         solution_limit=None,preprocessing=True,vehicle_symmetry=False)
    summary=probe.run(args,p,'physical_time',path)
    summary.update(case=case,route_capacity_bounds=statistics)
    write(path/'summary.json',summary)


def verify(case):
    validate();loader,load=imports()
    from models.common.cycle.audit import audit_solution
    from models.cost.comparison import evaluate_comparison_cost
    p=load(*loader(BASE/'cases'/f'{case}.json',use_pool_config=False))
    path=HERE/'runs'/case;s=read(path/'summary.json');s['valid']=False
    if s['sol_count']:
        result=read(path/'physical_time_result.json')
        audit=audit_solution(p,result)
        assert audit['valid'] and audit['periodic_replay']['valid']
        cost=evaluate_comparison_cost(p,result,p.cost_profile.config,audit=audit)
        assert math.isclose(cost['total'],s['objective'],rel_tol=1e-8,abs_tol=1e-6)
        write(path/'independent_audit.json',audit);write(path/'independent_cost.json',cost)
        s['valid']=True
    write(path/'summary.json',s)
    write(path/'complete.json',dict(summary_sha256=sha(path/'summary.json')))


def run():
    m=validate()
    env=dict(os.environ,OMP_NUM_THREADS='1',OPENBLAS_NUM_THREADS='1',MKL_NUM_THREADS='1',
             NUMEXPR_NUM_THREADS='1',PYTHONUTF8='1')
    rows=[]
    for case in m['cases']:
        if (HERE/'STOP').exists():
            write(HERE/'active.json',dict(status='STOPPED'))
            return
        path=HERE/'runs'/case;path.mkdir(parents=True,exist_ok=True)
        print(json.dumps(dict(start=case,seconds=m['seconds'])),flush=True)
        write(HERE/'active.json',dict(case=case,status='RUNNING'))
        if not (path/'complete.json').exists():
            for action in ('worker','verify'):
                with (path/f'{action}.log').open('w',encoding='utf-8') as log:
                    process=subprocess.Popen([sys.executable,str(Path(__file__)),action,'--case',case],cwd=PROJECT,
                        env=env,stdout=log,stderr=subprocess.STDOUT,
                        creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
                    begun=time.perf_counter()
                    while process.poll() is None:
                        if (HERE/'STOP').exists() or time.perf_counter()-begun>900:
                            process.terminate();process.wait(timeout=10)
                            write(HERE/'active.json',dict(status='STOPPED',case=case))
                            raise RuntimeError('Interrupted; preserve this attempt for inspection')
                        time.sleep(2)
                    if process.returncode:
                        raise RuntimeError(f'{case}/{action} failed; inspect logs')
        assert sha(path/'summary.json')==read(path/'complete.json')['summary_sha256']
        rows.append(read(path/'summary.json'));write(HERE/'comparison.json',rows)
        print(json.dumps({k:rows[-1].get(k) for k in ('case','status','objective','best_bound','mip_gap',
            'first_solver_incumbent_seconds','variables','constraints','build_time','mip_work')},ensure_ascii=False),flush=True)
    validate();write(HERE/'active.json',dict(status='COMPLETE'))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['prepare','run','worker','verify'])
    parser.add_argument('--case',choices=['S01','S03'])
    args=parser.parse_args()
    if args.action=='prepare':prepare()
    elif args.action=='run':run()
    elif args.action=='worker':worker(args.case)
    else:verify(args.case)
