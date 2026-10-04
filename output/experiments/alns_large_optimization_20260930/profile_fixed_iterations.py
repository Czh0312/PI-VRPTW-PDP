"""Alternating versions; fixed iteration trajectories and CPU/wall diagnostics."""
import json
from pathlib import Path
import subprocess
import sys
import time
import statistics

OUT=Path(__file__).resolve().parent
ROOT=OUT.parents[2]
if len(sys.argv)>1:
    source, model = sys.argv[1], int(sys.argv[2])
    sys.path.insert(0,source)
    from configuration.loader import load_routing_case
    from models.routing.common.cycle import load_instance
    from models.routing.common.cycle.audit import audit_solution
    from models.routing.service import solve_routing
    case=ROOT/'output/experiments/alns_large_status_20260930/benchmark_inputs/zhou_Set2_100-5-1b/case.json'
    config,fleet=load_routing_case(case)
    p=load_instance({**config,'model':model},fleet)
    wall,cpu=time.perf_counter(),time.process_time()
    r=solve_routing(model=model,method='alns',problem=p,alns_time=60,alns_iter=300,seed=42)
    cpu,wall=time.process_time()-cpu,time.perf_counter()-wall
    print(json.dumps(dict(model=model,source=source,iterations=r['info']['iterations'],cpu=cpu,wall=wall,
                         objective=r['info']['objective'],valid=audit_solution(p,r)['valid'],
                         e1_trips=r['e1_trips'],e2_trips=r['e2_trips'])))
else:
    rows=[]
    for repeat in range(3):
        for model in (1,3):
            for version in (('before_source','candidate_source') if repeat%2==0 else ('candidate_source','before_source')):
                done=subprocess.run([sys.executable,str(Path(__file__).resolve()),str(OUT/version),str(model)],
                                    capture_output=True,text=True,timeout=80,check=True)
                r=json.loads(done.stdout);r.update(repeat=repeat,version=version);rows.append(r)
                (OUT/'fixed_iterations_runs.json').write_text(json.dumps(rows,indent=2))
                print(model,version,round(r['cpu'],4),round(r['wall'],4),r['objective'],flush=True)
    summary=[]
    for model in (1,3):
        a=[r for r in rows if r['model']==model and r['version']=='before_source']
        b=[r for r in rows if r['model']==model and r['version']=='candidate_source']
        assert all(r['valid'] and r['iterations']==300 for r in a+b)
        same=all(all(r[k]==a[0][k] for k in ('objective','e1_trips','e2_trips')) for r in a+b)
        before,after=statistics.median(r['cpu'] for r in a),statistics.median(r['cpu'] for r in b)
        summary.append(dict(model=model,identical_routes_and_objective=same,
            before_median_cpu_seconds=before,after_median_cpu_seconds=after,
            cpu_reduction_percent=100*(1-after/before),cpu_speedup=before/after,
            before_median_wall_seconds=statistics.median(r['wall'] for r in a),
            after_median_wall_seconds=statistics.median(r['wall'] for r in b)))
    (OUT/'fixed_iterations_summary.json').write_text(json.dumps(summary,indent=2))
    print(summary)
