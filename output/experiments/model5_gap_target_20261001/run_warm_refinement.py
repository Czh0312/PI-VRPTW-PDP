"""Frozen, sequential warm ALNS + optional retiming. A hybrid diagnostic."""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

OUT=Path(__file__).resolve().parent
ROOT=OUT.parents[2]
FROZEN=OUT/'diagnostic_60s/source_snapshot'
sys.path.insert(0,str(FROZEN))


def write(path,data):
    path.write_text(json.dumps(data,ensure_ascii=False,indent=2,allow_nan=False)+'\n')


def worker(job,destination):
    from configuration.loader import load_routing_case
    from models.routing.common.cycle import load_instance
    from models.routing.common.cycle.audit import audit_solution
    from models.routing.solvers.alns.solver import solve_alns
    from models.routing.variants.model_5.exact import reoptimize_e1
    problem=load_instance(*load_routing_case(job['case']))
    initial=json.loads(Path(job['initial_record']).read_text())['result']
    untouched=deepcopy(initial)
    before=audit_solution(problem,initial)
    assert before['valid'] and before['periodic_replay']['valid']
    started=time.perf_counter()
    searched=solve_alns(problem,max_time=job['alns_time'],max_iterations=100000,
                       seed=job['seed'],initial_result=initial)
    search_audit=audit_solution(problem,searched)
    assert search_audit['valid'] and search_audit['periodic_replay']['valid']
    assert search_audit['objective']<=before['objective']+1e-6 and initial==untouched
    result=reoptimize_e1(problem,searched,time_limit=job['polish_time'],
                         retime_e2=True,seed=job['seed'],verbose=True,
                         diagnostics_path=destination.with_suffix('.mps'))
    result=json.loads(json.dumps(result))
    audit=audit_solution(problem,result)
    assert audit['valid'] and audit['periodic_replay']['valid']
    assert audit['objective']<=search_audit['objective']+1e-6
    assert job['lower_bound']<=audit['objective']+1e-6
    write(destination,dict(job=job,initial_audit=before,alns_result=searched,
        alns_audit=search_audit,result=result,independent_audit=audit,
        inherited_global_lower_bound=job['lower_bound'],
        gap_percent=100*(audit['objective']-job['lower_bound'])/abs(audit['objective']),
        wall_seconds=time.perf_counter()-started,
        note='ALNS and E1 retiming improve feasible UB only; reuse the previous full-M5 certificate LB.'))


if len(sys.argv)>1:
    worker(json.loads(sys.argv[1]),Path(sys.argv[2]))
else:
    directory=OUT/'warm_60s_plus_polish_5s'
    directory.mkdir(exist_ok=False)
    old=json.loads((OUT/'diagnostic_60s/summary.json').read_text())
    jobs=[dict(id=r['job']['id'],case=r['job']['case'],
        initial_record=str(OUT/'diagnostic_60s'/(r['job']['id']+'.json')),
        lower_bound=r['bound'],alns_time=60,polish_time=5,seed=42) for r in old]
    sources={str(p.relative_to(FROZEN)):hashlib.sha256(p.read_bytes()).hexdigest()
             for p in sorted(FROZEN.rglob('*.py'))}
    write(directory/'manifest.json',dict(jobs=jobs,source_sha256=sources,
        script_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        scope='Four fixed observed configurations; extra 65s beyond the 60s certificate diagnostic and earlier incumbent construction. Not an equal-budget pure ALNS comparison.'))
    rows=[]
    for job in jobs:
        path=directory/(job['id']+'.json')
        try:
            with path.with_suffix('.log').open('w') as log:
                completed=subprocess.run([sys.executable,str(Path(__file__)),json.dumps(job),str(path)],
                    cwd=ROOT,stdout=log,stderr=subprocess.STDOUT,timeout=85,
                    env={**os.environ,'PYTHONHASHSEED':'0'})
            row=dict(id=job['id'],valid=False,returncode=completed.returncode)
            if completed.returncode==0:
                saved=json.loads(path.read_text());row.update(valid=True,
                    upper=saved['independent_audit']['objective'],lower=job['lower_bound'],
                    gap_percent=saved['gap_percent'],wall_seconds=saved['wall_seconds'])
        except subprocess.TimeoutExpired:
            row=dict(id=job['id'],valid=False,status='OUTER_TIMEOUT')
        rows.append(row);write(directory/'summary.json',rows)
        print(row,flush=True)
