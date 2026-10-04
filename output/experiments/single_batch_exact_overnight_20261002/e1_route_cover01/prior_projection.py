"""Additional unchanged original M3 schedule projections, no timed reruns."""
from pathlib import Path
import hashlib
import json
import sys
ROOT=Path(__file__).resolve().parents[4];OUT=Path(__file__).resolve().parent
BATCH=OUT/'paired15_30';sys.path.insert(0,str(BATCH/'source_snapshot'))
from configuration.loader import load_routing_case
from models.routing.common.cycle import load_instance
from models.routing.common.cycle.audit import audit_solution
from models.routing.solvers.exact.single_batch_e1_bound import enumerate_e1_routes,project_e1_schedule
cfg,fleet=load_routing_case(BATCH/'zhou_Set2_100-5-1b_subset20/case.json');p=load_instance(cfg,fleet)
routes,info=enumerate_e1_routes(p);assert info['complete'];rows=[]
for batch in ('full20','causal20'):
    for seed in (42,43):
        path=ROOT/'output/experiments/single_batch_short_cycles_20261001'/batch/'zhou_Set2_100-5-1b_subset20_M3'/f'baseline_{seed}/result.json'
        r=json.loads(path.read_text());a=audit_solution(p,r);assert a['valid'] and a['periodic_replay']['valid']
        projection=project_e1_schedule(p,r,routes)
        rows.append(dict(origin=str(path),sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
            original_upper=a['objective'],original_e1=a['distances']['e1'],projection=projection,audited=True,replay=True))
(OUT/'additional_prior_projections.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2)+'\n')
print('additional fixed original projections',len(rows))
