"""Explain certificate rejection from saved evidence, without re-solving."""
from pathlib import Path
from fractions import Fraction
from collections import Counter
import json
out=Path(__file__).resolve().parent
records=[]
for p in sorted((out/'runs').rglob('result.json')):
 c=json.loads(p.read_text())['info'].get('component_certificate')
 if not c:continue
 sites=[]
 for site,x in c['sites'].items():
  causes=[];delta=None
  if x.get('complete'):
   delta=Fraction(*c['target_costs'][site])-Fraction(*x['exact_objective'])
   if delta:causes.append('exact_raw_coefficient_objective_mismatch')
   if not x['native_bound_equals_objective']:causes.append('native_float_bound_not_equal')
   if x['max_violation']>1e-7:causes.append('native_feasibility_violation')
  else:causes.append('site_not_solved_to_optimality')
  sites.append(dict(site=site,status=x['status'],causes=causes,exact_objective_delta=str(delta) if delta is not None else None,
   native_float_gap=x['objective']-x['raw_bound'] if 'objective' in x else None,raw_bound=x.get('raw_bound'),
   native_objective=x.get('objective'),component_seconds=x['runtime']))
 records.append(dict(run=str(p.parent),closed=c['closed'],status=c['status'],component_seconds=c['runtime'],sites=sites))
assert len(records)==12
counts=Counter(cause for r in records for s in r['sites'] for cause in s['causes'])
(out/'diagnosis.json').write_text(json.dumps(dict(records=records,causes=dict(counts)),indent=2)+'\n')
print(json.dumps(dict(counts),indent=2))
