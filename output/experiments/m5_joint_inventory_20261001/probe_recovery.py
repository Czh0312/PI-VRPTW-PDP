"""Test actual schedule recovery from the exploratory joint relaxed cover."""
import json
from pathlib import Path
import sys
import time
here = Path(__file__).resolve().parent
root = here.parents[2]
sys.path.insert(0, str(root))
from configuration.loader import load_routing_case
from models.routing.common.cycle import load_instance
from models.routing.solvers.exact.multibatch_joint_inventory import recover_joint_schedule
previous = here.parent/'m5_integer_cover_20261001/final_60s'
payload = json.loads((previous/'02_42_integer.json').read_text())
problem = load_instance(*load_routing_case(payload['job']['case']))
initial = json.loads(Path(payload['job']['initial_result']).read_text())
joint = json.loads((here/'probe.json').read_text())[-1]['joint']
result, metadata = recover_joint_schedule(problem, initial, joint, deadline=time.perf_counter()+10,
    verbose=True, diagnostics_dir=here/'probe_recovery_models')
(here/'probe_recovery.json').write_text(json.dumps(dict(result=result, metadata=metadata), indent=2)+'\n')
print(json.dumps(metadata), flush=True)
