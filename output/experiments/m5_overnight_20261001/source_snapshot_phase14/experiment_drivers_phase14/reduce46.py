"""Greedy certificate search; only a proved infeasible relaxation deletes a condition."""
from pathlib import Path
import sys,json,time,os
HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE/'source_snapshot_phase14'))
from configuration.loader import load_routing_case
from models.routing.common.cycle import load_instance
from models.routing.solvers.exact.multibatch_physical_site import physical_site_certificate,normalize_trip_groups
p=load_instance(*load_routing_case(str(HERE/'medium25/0/inputs/case_relocated.json')))
source=json.loads((HERE/'site45/certificate_pending_validation.json').read_text());assert source['subproblem_infeasible'] and source['status']==3
original=list(normalize_trip_groups(p,'S3',source['trip_groups']));current=original.copy();out=HERE/'reduce46';out.mkdir()
(out/'parameters.json').write_text(json.dumps(dict(total_search_budget=120,per_trial_budget=15,seed=42,site='S3',
 original_problem=True,all_original_site_orders_retained=True,delete_condition_only_on_INFEASIBLE=True),indent=2)+'\n')
started=time.perf_counter();history=[];accepted=None
c=json.loads((HERE/'checkpoint.json').read_text());c.update(status='running_reduce46',active_process=os.getpid());(HERE/'checkpoint.json').write_text(json.dumps(c,indent=2)+'\n')
for i,group in enumerate(original):
 if time.perf_counter()-started>=120:break
 proposal=[g for g in current if g!=group]
 if not proposal:continue
 target=out/str(i);target.mkdir()
 r=physical_site_certificate(p,'S3',time_limit=min(15,120-(time.perf_counter()-started)),seed=42,
  trip_groups=proposal,partial_groups=True,compress_routes=True,diagnostics_path=target/'physical.mps.gz')
 (target/'certificate.json').write_text(json.dumps(r,indent=2)+'\n')
 entry=dict(removed=group,remaining=len(proposal),status=r['status'],accepted=bool(r['subproblem_infeasible'] and r['status']==3),runtime=r['runtime'])
 history.append(entry)
 if entry['accepted']:current=proposal;accepted=i
 print(i,len(current),r['status'],r['runtime'],flush=True)
summary=dict(original_groups=len(original),retained_groups=current,remaining=len(current),last_accepted_trial=accepted,
 deletion_results=history,runtime=time.perf_counter()-started,minimality_claim=False,full_proven_infeasible=False)
(out/'summary.json').write_text(json.dumps(summary,indent=2)+'\n')
c=json.loads((HERE/'checkpoint.json').read_text());c.update(status='reduce46_pending_independent_validation',active_process=None);(HERE/'checkpoint.json').write_text(json.dumps(c,indent=2)+'\n')
