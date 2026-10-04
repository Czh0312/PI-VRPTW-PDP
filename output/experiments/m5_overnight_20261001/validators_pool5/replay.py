from pathlib import Path
import json
import sys

HERE=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(HERE/'source_snapshot_vehicle4'))
from configuration.loader import load_routing_case
from models.routing.common.cycle import load_instance
from validation_pool import replay_pool

problem=load_instance(*load_routing_case(str(HERE/'inputs/case.json')))
proof=replay_pool(problem,HERE/'pool_after_vehicle4/models/pass_0')
proof.update(independent_physical_slots_used=True,production_extractor_not_called=True,
             scope='restricted_pool_reconstruction_of_a_full_feasible_upper')
(HERE/'pool_after_vehicle4/validation/pool_replay.json').write_text(json.dumps(proof,indent=2)+'\n')
print(json.dumps(proof,indent=2))
