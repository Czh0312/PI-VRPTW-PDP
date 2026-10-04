"""One frozen complete original integer solve for the second source start."""
from pathlib import Path
from datetime import datetime,timezone
import hashlib
import json
import os
import shutil
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[4];OUT=Path(__file__).resolve().parent;NIGHT=OUT.parent
def read(p):return json.loads(p.read_text())
def write(p,v):p.write_text(json.dumps(v,ensure_ascii=False,indent=2,allow_nan=False)+'\n')
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()

def main():
    folder=OUT/'independent_setup20'
    if len(sys.argv)>1:
        sys.path.insert(0,str(folder/'source_snapshot'))
        from configuration.loader import load_routing_case
        from models.routing.common.cycle import load_instance
        from models.routing.common.cycle.audit import audit_solution
        from models.routing.solvers.exact.solver import solve_exact
        cfg,fleet=load_routing_case(folder/'case.json');p=load_instance(cfg,fleet)
        r=solve_exact(p,time_limit=20,seed=42,verbose=True);write(folder/'result.json',r)
        a=audit_solution(p,r);write(folder/'audit.json',a)
        assert a['valid'] and a['periodic_replay']['valid'],'Preserve missing feasible start, no retry'
        return
    assert read(NIGHT/'checkpoint.json')['active_process'] is None,'No own timing overlap'
    folder.mkdir();sys.path.insert(0,str(ROOT))
    from configuration.loader import load_routing_case,resolve_project_path
    from experiments.runs.run_multibatch_cycle import freeze_inputs,source_snapshot,INPUT_KEYS
    cfg,fleet=load_routing_case(ROOT/'data/single_batch/medium/zhou_Set2_100-5-2_subset20/case.json')
    cfg['model']=3;cfg['solver']='exact';cfg['exact_backend']='multibatch_indexed'
    sources={resolve_project_path(cfg[k]).resolve() for k in INPUT_KEYS if isinstance(cfg.get(k),str)}
    variants=[dict(config=cfg,_source_hashes={str(p):sha(p) for p in sources})]
    inputs=freeze_inputs(variants,folder);source=source_snapshot(folder);write(folder/'case.json',cfg)
    shutil.copy2(Path(__file__),folder/'prepare_frozen.py')
    write(folder/'manifest.json',dict(input_sha256=inputs,source_sha256=source,driver_sha256=sha(Path(__file__)),
        purpose='Initial integer schedule only; outside root comparison budget',budget=20,seed=42,threads=1,
        begun_utc=datetime.now(timezone.utc).isoformat(),load=os.getloadavg()))
    cp=read(NIGHT/'checkpoint.json');cp.update(status='PREPARING_INDEPENDENT_ORIGINAL_INITIAL_SCHEDULE',
        active_process=dict(conductor_pid=os.getpid(),stage='independent_setup20'),current_driver=str(Path(__file__)))
    write(NIGHT/'checkpoint.json',cp)
    with (folder/'solver.log').open('w') as log:
        r=subprocess.run([sys.executable,str(Path(__file__)),'--worker'],cwd=folder/'source_snapshot',
            stdout=log,stderr=subprocess.STDOUT,timeout=60)
    write(folder/'completion.json',dict(returncode=r.returncode,load=os.getloadavg(),finished_utc=datetime.now(timezone.utc).isoformat()))
    cp=read(NIGHT/'checkpoint.json');cp.update(status='INDEPENDENT_INITIAL_SETUP_COMPLETE' if not r.returncode else 'INDEPENDENT_INITIAL_SETUP_FAILED',active_process=None)
    write(NIGHT/'checkpoint.json',cp)
    assert r.returncode==0,'Retain setup failure; no retry'

if __name__=='__main__':main()
