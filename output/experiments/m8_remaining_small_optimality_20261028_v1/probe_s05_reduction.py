"""Check whether the improved S05 upper bound certifies a smaller domain."""
import json
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT))
from run_case import derive_reduction
from models.common.cycle import load_instance
from tools.configuration.loader import load_routing_case

p = load_instance(*load_routing_case(
    ROOT / 'data/formal_benchmark27_uniform_7_20_50_v1/model_8/small/S05/case.json',
    use_pool_config=False,
))
out = HERE / 'S05' / 'refinement_probe'
out.mkdir(exist_ok=False)
witness = json.loads((HERE / 'S05/native_result.json').read_text(encoding='utf-8-sig'))
certificate = derive_reduction(p, witness, out)
print(json.dumps({key: certificate[key] for key in (
    'retained_K', 'N_min', 'N_max', 'audited_upper_bound', 'global_lower_bound',
)}), flush=True)
