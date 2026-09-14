"""Run remaining regression and bounded candidate probes after the serial batch."""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT/'output/experiments/solver_optimization_20260913'
ENV = {**os.environ,'PYTHONHASHSEED':'0'}


def main():
    while json.loads((OUT/'campaign_progress.json').read_text())['status'] != 'complete':
        time.sleep(10)
    def run(name, cmd):
        (OUT/'post_progress.json').write_text(json.dumps(dict(stage=name,status='running')),encoding='utf-8')
        log = OUT/f'{name}.log'
        print('START',name,flush=True)
        with log.open('w',encoding='utf-8') as f:
            subprocess.run([sys.executable,*cmd],cwd=ROOT,env=ENV,stdout=f,stderr=subprocess.STDOUT,check=True)
        print('COMPLETE',name,flush=True)
    run('timing_repeats',['experiments/runs/repeat_timing_anomalies_20260913.py'])
    run('final_pytest',['-c',r"import sys;sys.path.append(r'C:\gurobi\win64\python311\lib');import pytest;sys.exit(pytest.main(['-q']))"])
    cases = ['Set2_100-5-1','Set2_100-10-1','Set2_200-10-1']
    def probe(name,model,options,source=None):
        args = ['experiments/runs/optimize_solvers_20260913.py','--output',str((OUT/name).relative_to(ROOT)),
                '--method','alns','--scale','large','--models',str(model),'--seconds','5',
                '--seeds','42','43','--instances',*cases,'--options',json.dumps(options)]
        if source:
            args += ['--source-root',str(source)]
        run(name,args)
    probe('m2_seed_route_control',2,{})
    probe('m2_seed_route_candidate',2,{'route_seed_fraction':.2})
    for model,ratio in [(6,.8),(7,.75)]:
        probe(f'm{model}_stage_ratio_control',model,{},OUT/'final_source')
        options = dict(stage1_time_ratio=.9,transshipment_time_budget=1 if model==6 else 1.25,
                       transshipment_sample_pickups=12,transshipment_sample_locations=6,
                       transshipment_sample_carriers=4,transshipment_position_top_k=4)
        probe(f'm{model}_stage_ratio_candidate',model,options,OUT/'final_source')
    (OUT/'post_progress.json').write_text(json.dumps(dict(status='complete')),encoding='utf-8')
    print('POST CAMPAIGN COMPLETE',flush=True)


if __name__=='__main__':
    main()
