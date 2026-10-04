"""Original quality gates plus a preregistered actual-use requirement."""
from pathlib import Path
import json, subprocess, sys
OUT=Path(__file__).resolve().parent
subprocess.run([sys.executable,str(OUT/'summarize.py')],check=True)
comparison=json.loads((OUT/'quality_comparison.json').read_text())
rows=json.loads((OUT/'quality/summary.json').read_text())
candidate=[r for r in rows if r['source']=='after_source']
uses={budget:sum(r.get('late_pickup_vetoes',0) or 0 for r in candidate if r['budget']==time)
      for budget,time in [('short',5),('long',15)]}
mechanism=all(v>=1 for v in uses.values())
decision=dict(decision='QUALITY_PASSED_PENDING_VALIDATION' if comparison['eligible'] and mechanism else 'REJECTED',
              quality_gate_passed=comparison['eligible'], actual_vetoes=uses, mechanism_gate_passed=mechanism,
              verification='PENDING', reason='Original four strata gates and at least one actual veto in each budget; no retries or gate changes')
(OUT/'decision.json').write_text(json.dumps(decision,indent=2))
print(json.dumps(decision,indent=2))
