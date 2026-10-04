import json,subprocess,sys
from pathlib import Path
p=Path(__file__).resolve().parent
a=json.loads((p/'planned_inputs.json').read_text())
tool='tools/audit/benchmark_multibatch_route_service.py'
def run(name,pairs,budget,joint,enum,seeds,variants=('timed','service')):
 cmd=[sys.executable,tool,'--output',str(p/name),'--cases',*[x[0] for x in pairs],'--initial-results',*[x[1] for x in pairs],'--time',str(budget),'--joint-time',str(joint),'--enumeration-time',str(enum),'--recovery-time',str(3 if budget>5 else 1),'--e1-time',str(10 if budget>5 else 1),'--integer-time',str(15 if budget>5 else 1),'--seeds',*map(str,seeds),'--variants',*variants]
 subprocess.run(cmd,check=True)
run('verified_50_180s',a['large'][2:],180,85,50,[42,43,44])
run('verified_medium_180s',a['large'][:2],180,85,50,[42])
run('verified_small_5s',a['small'],5,1,1,[42])
run('verified_fallback_180s',a['large'][1:],180,85,50,[42],('fallback',))
