"""Freeze current context and register one bounded soft-window trial."""
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
import hashlib
import json
import shutil
import subprocess

OUT=Path(__file__).resolve().parent
ROOT=OUT.parents[2]
OLD=OUT.parent/'alns_single_batch_diverse_descent_20261005'
MODULE=Path('models/routing/solvers/alns/single_batch.py')

def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def write(p,x):p.write_text(json.dumps(x,ensure_ascii=False,indent=2)+'\n')
def manifest(folder):
    return {str(p.relative_to(folder)):sha(p) for p in sorted(folder.rglob('*'))
            if p.is_file() and '__pycache__' not in p.parts and '.pytest_cache' not in p.parts}

def freeze():
    files=[p for base in ('models','configuration','config','tests') for p in sorted((ROOT/base).rglob('*'))
           if p.is_file() and p.suffix in ('.py','.json','.md') and '__pycache__' not in p.parts]
    before={str(p.relative_to(ROOT)):sha(p) for p in files}
    target=OUT/'before_source';target.mkdir()
    for p in files:
        dest=target/p.relative_to(ROOT);dest.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(p,dest)
    checks={r:sha(ROOT/r)==h==sha(target/r) for r,h in before.items()}
    write(OUT/'freeze_verification.json',dict(passed=all(checks.values()),checks=checks))
    assert all(checks.values())
    write(OUT/'before_source_manifest.json',before)
    (OUT/'initial_git_status.log').write_text(subprocess.check_output(['git','status','--short'],cwd=ROOT,text=True))
    shutil.copytree(OLD/'benchmark_inputs',OUT/'benchmark_inputs')
    for p in (OUT/'benchmark_inputs').rglob('case.json'):
        p.write_text(p.read_text().replace(str(OLD/'benchmark_inputs'),str(OUT/'benchmark_inputs')))
    write(OUT/'input_hashes.json',manifest(OUT/'benchmark_inputs'))
    families=['zhou_Set2_100-5-3_subset50_scenario02','zhou_Set2_100-5-3_scenario02','zhou_Set2_100-10-2_scenario02']
    def job(f,m,s,b,n):
        return dict(family=f,case=str(OUT/'benchmark_inputs'/f/'case.json'),model=m,solver='alns',seed=s,budget=b,iterations=n,size=50 if 'subset50' in f else 100)
    for phase,seed,short,long in [('pilot',561,30,60),('confirmation',571,60,120)]:
        jobs=[job(f,3,s,short,1000000) for f in families[:2] for s in range(seed,seed+3)]
        jobs += [job(families[2],3,s,long,1000000) for s in range(seed,seed+2)]
        write(OUT/(phase+'_manifest.json'),dict(short_budget=short,long_budget=long,jobs=jobs))
    write(OUT/'verification_manifest.json',dict(jobs=[job(f,m,560,1000000,500) for f in families[1:] for m in (1,2,3,4)]))
    write(OUT/'unaffected_manifest.json',dict(jobs=[job(f,m,560,1000000,500) for f in families[1:] for m in (1,2,4)]))
    protocol=dict(registered=datetime.now(ZoneInfo('Asia/Shanghai')).isoformat(),
        hypothesis='Allow bounded E2 time-window infeasible intermediate states to cross barriers that the original 1e6 violation penalty numerically closes. Best/output must remain fully feasible/audited.',
        scope='M3 only: problem.model_number==3 and no assignment_search. Private option defaults true in candidate only. M1/M2/M4 exact fixed-work behavior and disabled M1-M4 equivalence required. No changes to decoder, business constraints, audit, inputs, lazy pickup prefix, restart attribution, descent selector or late pickup control flow.',
        rule='Wrap solve-local evaluation context. If the unique issue set is exactly E2_TIME_WINDOW, decoded objective finite, and every positive lateness <=300 seconds, search score = original distance +10*sum(lateness_seconds)/60. Include every E2 departure beyond cycle_end, event beyond its decoder due (delivery min(window end,cutoff-service)), and return beyond cycle_end-turnaround. Zero/inconsistent/nonfinite severity keeps original score. All other issue combinations retain original score. Violations and decoded result unchanged.',
        parameter_choice='Single engineering choice: coefficient10 distance units/minute; maximum lateness300 seconds. Not estimated optimum, no parameter sweep or post-result tuning. Score constant across solve; no changing coefficient/cache invalidation or restoration mechanism added.',
        safety='Unchanged incumbent update requires zero decoder issues and full independent audit. Final result is audited best, never the current infeasible state. Hard/structural issues keep original penalties. Necessary-condition rejection unchanged. No true GAP or optimality claim.',
        correctness_gate='Time-unit invariance, max-lateness boundary, hard issue isolation, immutable cache result, real acceptance/recovery and final-best guard. Existing relevant tests. Disabled2sources x M1-M4 fixed500 exact; enabled M1/M2/M4 fixed500 exact. Public audits and uncached reconstruction.',
        quality_gate='Pilot M3 8pairs30/60s, then only if passed confirmation8pairs60/120s. Both phases require short/long/short50/short100 mean distance change<=0%; every worsening<=5%; short>=3/6 strict wins, long>=1/2. All results fresh uncached decode/full audit/replay. At least one accepted infeasible transition and one return to feasible in EACH short/long stratum. Any failed gate rejects entire candidate and cancels remaining phases; no subgroup adoption.',
        timing='Serial alternating versions; no own concurrent profiling/testing/heavy load during quality. Actual budget includes initialization, wrapper scoring and final audit. Iteration cap1e6; timeout budget+30 for quality. CPU/load recorded, external load not controlled. Fixed-work checks use large wall cap, outer timeout120.',
        adoption='Only after both quality phases and final regression/integration pass. Until then all candidate edits confined to snapshots. If production drift in owned modules, merge narrowly and verify without overwriting others.',
        interpretation='Previously used nested geometries and scenario orders; new seeds not a new independent dataset. Limited pairs, descriptive only. Preserve all failures/negative outcomes. No significance/stable improvement claims.')
    write(OUT/'protocol.json',protocol)
    write(OUT/'registration_hashes.json',{p.name:sha(p) for p in OUT.glob('*.json')})
    shutil.copytree(target,OUT/'after_source')
    write(OUT/'checkpoint.json',dict(status='registered_before_candidate_implementation',active_process=None))
    print('Registered',len(files),'source files')

if __name__=='__main__':freeze()
