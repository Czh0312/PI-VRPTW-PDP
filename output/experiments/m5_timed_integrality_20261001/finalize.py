from pathlib import Path
from dataclasses import asdict
import hashlib,json,shutil,sys
HERE=Path(__file__).resolve().parent;ROOT=HERE.parents[2]
sys.path.insert(0,str(ROOT))
from configuration.loader import load_routing_case
from models.routing.common.cycle import load_instance
from models.routing.common.cycle.audit import audit_solution
def read(p):return json.loads(p.read_text())
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
validation=read(HERE/'validation.json')
assert validation['formal_runs']==26 and validation['all_returned_full_schedules_reaudited']
source=HERE/'verified_50_180s/00_42_service.json'
r=read(source);p=load_instance(*load_routing_case(r['job']['case']))
initial=read(Path(r['job']['initial_result']))
full=audit_solution(p,initial)
assert full['valid'] and full['periodic_replay']['valid']
assert not initial['info']['proven_optimal']
initial['info']['best_bound']=None
(HERE/'best_full_result.json').write_text(json.dumps(initial,ensure_ascii=False,indent=2,allow_nan=False)+'\n')
checks=validation['joint_checks']
check=next(c for c in checks if c['path']==str(source.parent/'00_42_service_models/joint/joint_inventory.mps.gz'))
assert check['reported_bound_supported'] and check['full_schedule_projection']
lower=r['bound'];upper=full['objective']
cert=dict(lower_bound=lower,upper_bound=upper,gap=(upper-lower)/upper,proven_optimal=False,
 proven_infeasible=False,scope='M5_joint_service_timed_relaxation_lower_bound_and_audited_full_incumbent',
 lower_source=str(source.relative_to(HERE)),upper_source='best_full_result.json',
 independent_lower_replay=check,full_solution_audit=full,
 note='This lower/upper pair was already returned in the 180-second seed-42 run. Seed 43 matches; seed 44 was weaker. Service envelopes remain opt-in. No complete M5 optimality proof.')
(HERE/'combined_certificate.json').write_text(json.dumps(cert,ensure_ascii=False,indent=2,allow_nan=False)+'\n')
ours=['models/routing/solvers/exact/multibatch_joint_inventory.py','models/routing/solvers/exact/multibatch_route_timing.py','tests/routing/test_multibatch_route_service.py','tools/audit/benchmark_multibatch_route_service.py','models/routing/variants/model_5/exact/README.md','docs/exact_solver_architecture.md','tools/README.md','docs/m5_route_service_20261001.md']
for name in ours:
 target=HERE/'final_source_snapshot'/name;target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(ROOT/name,target)
for name in ('models/routing/solvers/exact/multibatch_joint_inventory.py','models/routing/solvers/exact/multibatch_route_timing.py','tools/audit/benchmark_multibatch_route_service.py'):
 assert sha(ROOT/name)==sha(HERE/'verified_50_180s/source_snapshot'/name)
validation['final_stage_source_sha256']={name:sha(ROOT/name) for name in ours}
validation['final_artifact_sha256']={name:sha(HERE/name) for name in ('best_full_result.json','combined_certificate.json','changes.patch','pytest.log','pytest_final.log','unit_final.log','finalize.py')}
(HERE/'validation.json').write_text(json.dumps(validation,ensure_ascii=False,indent=2,allow_nan=False)+'\n')
print(json.dumps(dict(lower=lower,upper=upper,gap_percent=100*cert['gap'],formal_runs=26,joint_replays=len(checks),service_checks=len(validation['service_checks']),full_tests=validation['test_log']),ensure_ascii=False),flush=True)
