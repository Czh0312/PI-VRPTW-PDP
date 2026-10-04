"""Apply registered confirmation and withdrawal rules without retiming."""
from pathlib import Path
import json,subprocess,sys
OUT=Path(__file__).resolve().parent
subprocess.run([sys.executable,str(OUT/'summarize.py')],check=True)
comparison=json.loads((OUT/'quality_comparison.json').read_text())
rows=json.loads((OUT/'quality/summary.json').read_text())
candidate=[r for r in rows if r['source']=='after_source']
vetoes={name:sum(r.get('late_pickup_vetoes',0) or 0 for r in candidate if r['budget']==budget)
        for name,budget in [('short',30),('long',60)]}
mechanism=all(v>=1 for v in vetoes.values())
eligible=comparison['eligible'] and mechanism
decision=dict(decision='CONFIRMATION_PASSED_PENDING_CLOSURE' if eligible else 'WITHDRAW_PILOT_PENDING_RESTORE',
              quality_gate_passed=comparison['eligible'],mechanism_gate_passed=mechanism,actual_vetoes=vetoes,
              reversal_required=not eligible,verification='PENDING',
              scope='Original pilot5/15s results remain historical. New30/60s allgates required to retain; no retries, seed changes or subset retention.')
(OUT/'decision.json').write_text(json.dumps(decision,indent=2));print(json.dumps(decision,indent=2))
