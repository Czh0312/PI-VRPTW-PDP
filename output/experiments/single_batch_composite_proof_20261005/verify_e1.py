"""Reproduce the pre-timing independent fixed-integer-path and partition check."""
import json,sys,time
from pathlib import Path
from fractions import Fraction
out=Path(__file__).resolve().parent
sys.path.insert(0,str(out/'candidate_source'))
from tests.routing.test_single_batch_composite_proof import independent_partition
from models.routing.solvers.exact.single_batch_composite_proof import e1_integer_cover,schedule_costs
from configuration.loader import load_routing_case
from models.routing.common.cycle import load_instance
records=[]
for c in json.loads((out/'preparation.json').read_text())['cases']:
 start=time.perf_counter();cfg,f=load_routing_case(c['input']);p=load_instance(cfg,f)
 expected=independent_partition(p);actual=e1_integer_cover(p)
 assert expected == Fraction(*actual['exact_cost'])
 costs=schedule_costs(p,json.loads(Path(c['start']).read_text()))
 assert expected == costs['e1']
 records.append(dict(case=c['case'],expected=str(expected),cost=float(expected),status='INDEPENDENT_INTEGER_PATH_AND_COVER_MATCH',runtime=time.perf_counter()-start,production=actual))
 print(c['case'],float(expected),flush=True)
# Refuse to overwrite original results; run in a copied experiment directory to reproduce.
target=out/'e1_independent.json'
assert not target.exists()
target.write_text(json.dumps(records,indent=2)+'\n')
