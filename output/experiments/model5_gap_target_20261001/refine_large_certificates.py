"""Refine unfinished 100-customer pricing with the same frozen implementation."""
import json
from pathlib import Path
import subprocess
import sys

OUT=Path(__file__).resolve().parent
ROOT=OUT.parents[2]
old=json.loads((OUT/'diagnostic_60s/summary.json').read_text())[2:]
command=[sys.executable,str(OUT/'diagnostic_60s/source_snapshot/tools/audit/benchmark_multibatch_e1_polish.py'),
    '--output',str(OUT/'large_certificate_100s'),'--time','100','--polish-time','5',
    '--seeds','42','--variants','cg','--cases',*[r['job']['case'] for r in old],
    '--initial-results',*[str(OUT/'warm_60s_plus_polish_5s'/(r['job']['id']+'.json')) for r in old]]
(OUT/'certificate_refinement_command.json').write_text(json.dumps(command,indent=2)+'\n')
raise SystemExit(subprocess.run(command,cwd=ROOT).returncode)
