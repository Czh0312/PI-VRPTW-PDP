"""Stop only this experiment's obsolete uncompleted solve; keep its log."""
from pathlib import Path
import psutil,os,json,datetime
OUT=Path(__file__).resolve().parent
ROOT=OUT.parents[2]
target=(OUT/'solve_k2.py').resolve()
matches=[]
for p in psutil.process_iter(['pid','ppid','name','cmdline']):
    try:
        cmd=p.info['cmdline'] or []
        is_target=any((ROOT/arg).resolve()==target for arg in cmd if isinstance(arg,str) and arg.endswith('solve_k2.py'))
        if is_target and 'solve' in cmd and p.pid!=os.getpid():matches.append(p)
    except (psutil.Error,OSError,ValueError):pass
assert matches, 'No owned experimental solve found'
leaf=[p for p in matches if not any(q.info['ppid']==p.pid for q in matches)]
assert len(leaf)==1,[(p.pid,p.info['cmdline']) for p in matches]
p=leaf[0]
record=dict(status='INTERRUPTED_FOR_CERTIFIED_RESTART',time_utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),
    reason='Verified S01 optimum-preserving certificate excludes dominated pool sizes and impossible order assignments; restart with certificate instead of continuing redundant branches',
    pid=p.pid,command=p.info['cmdline'],planned_seconds=1800,completed_budget=False,
    original_log_preserved='solve_gurobi.log',replacement='solve_certified.py')
(OUT/'solve_interruption.json').write_text(json.dumps(record,ensure_ascii=False,indent=2),encoding='utf-8')
p.terminate();p.wait(timeout=20)
print(json.dumps(dict(stopped_pid=p.pid,status=record['status']),ensure_ascii=False))
