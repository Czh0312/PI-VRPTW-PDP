"""Run all nine M8 cases from raw data until native global optimality, one seed."""
import argparse
from datetime import datetime,timezone
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

from protocol import HERE,ROOT,SOURCE,read,save,sha,WorkspaceReadGuard

CASES = [f'S{i:02}' for i in range(1,10)]
SEED = 42
# Public solver validation requires a finite limit. 1e12 seconds is over 30,000
# years: no practical cutoff, no objective target, and no historical certificate.
UNBOUNDED_REQUEST_SECONDS = 1e12
OPTIONS = dict(strengthening='physical_time',preprocessing=True,vehicle_symmetry=True,
               milp_options=dict(formulation='compact',threads=1))


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def source_hashes():
    return {p.relative_to(SOURCE).as_posix():sha(p) for p in SOURCE.rglob('*.py')}


def prepare():
    assert not (HERE/'plan.json').exists() and not SOURCE.exists()
    shutil.copytree(ROOT/'models',SOURCE/'models',ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
    shutil.copytree(ROOT/'tools/configuration',SOURCE/'tools/configuration',
                    ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
    top = ROOT/'tools/__init__.py'
    if top.exists():
        shutil.copy2(top,SOURCE/'tools/__init__.py')
    else:
        (SOURCE/'tools/__init__.py').write_text('',encoding='utf-8')
    inputs,case_paths = {},{}
    for case in CASES:
        path = ROOT/f'data/formal_benchmark27_uniform_7_20_50_v1/model_8/small/{case}/case.json'
        case_paths[case] = str(path)
        files,config = [path],read(path)
        for key in ('data','routing_config','orders','operating_policy','economic_config',
                    'dataset_metadata','parameter_contract','batch_count_certificate'):
            if isinstance(config.get(key),str):
                files.append(ROOT/config[key])
        for path in files:
            inputs[str(path)] = sha(path)
            target = HERE/'inputs'/path.relative_to(ROOT)
            target.parent.mkdir(parents=True,exist_ok=True)
            shutil.copy2(path,target)
    plan = dict(created_utc=utc_now(),cases=CASES,case_paths=case_paths,seed=SEED,threads=1,
        concurrency=1,options=OPTIONS,stopping_rule='native OPTIMAL plus independent full original-domain audit',
        user_requested_continue_until_optimal=True,practical_time_cap=None,
        finite_api_limit_seconds=UNBOUNDED_REQUEST_SECONDS,original_K=list(range(1,8)),
        external_initial_solution=False,historical_solution_read=False,known_optimum_target_used=False,
        budget_scope='raw input load + precheck + full unified model build + native optimize + internal extraction/audit',
        independent_final_audit_timed_separately=True,source_hashes=source_hashes(),input_hashes=inputs,
        driver_hashes={name:sha(HERE/name) for name in ('bench.py','protocol.py')},
        observation='only in-memory event recording, progress/log writes; no solutions/cuts/parameters submitted')
    save(HERE/'plan.json',plan)
    print(json.dumps(dict(prepared=True,cases=CASES,root_seed=SEED,source_files=len(plan['source_hashes']),
                          input_files=len(inputs),practical_time_cap=None),ensure_ascii=False),flush=True)


def finite(value):
    return float(value) if math.isfinite(value) and abs(value)<1e90 else None


def install_observer(gp,folder,clock,cpu_clock,case):
    native = gp.Model
    events,calls,progress = [],[],[]
    first = [None]
    last_write = [-100.]

    class ObservedModel(native):
        def optimize(self,callback=None):
            assert callback is None
            self.update()
            assert self.NumStart == 0
            assert self.Params.Threads == 1 and self.Params.Seed == SEED
            assert self.Params.MIPGap == self.Params.MIPGapAbs == 0
            # Relative ASCII path also works with Gurobi 11's native Windows IO.
            self.Params.LogFile = os.path.relpath(folder/'gurobi.log',ROOT)
            self.Params.LogToConsole = 0
            self.Params.OutputFlag = 1
            calls.append(dict(NumStart=self.NumStart,threads=self.Params.Threads,seed=self.Params.Seed,
                variables=self.NumVars,constraints=self.NumConstrs+self.NumGenConstrs,
                time_limit=finite(self.Params.TimeLimit),MIPGap=self.Params.MIPGap,MIPGapAbs=self.Params.MIPGapAbs))
            save(folder/'live.json',dict(case=case,stage='optimizing',pid=os.getpid(),elapsed_seconds=clock(),
                native_incumbent=None,best_bound=None,first_integer_solution_seconds=None,
                build_finished_seconds=clock(),model=calls[-1],updated_utc=utc_now()))

            def observe(model,where):
                elapsed = clock()
                if where == gp.GRB.Callback.MIPSOL:
                    value = finite(model.cbGet(gp.GRB.Callback.MIPSOL_OBJ))
                    if value is not None:
                        if first[0] is None:
                            first[0] = elapsed
                        events.append(dict(elapsed_seconds=elapsed,objective=value,
                            native_runtime=model.cbGet(gp.GRB.Callback.RUNTIME),
                            best_bound=finite(model.cbGet(gp.GRB.Callback.MIPSOL_OBJBND))))
                if where in (gp.GRB.Callback.MIP,gp.GRB.Callback.MIPSOL) and (
                        elapsed-last_write[0]>=15. or where == gp.GRB.Callback.MIPSOL):
                    if where == gp.GRB.Callback.MIP:
                        incumbent = finite(model.cbGet(gp.GRB.Callback.MIP_OBJBST))
                        bound = finite(model.cbGet(gp.GRB.Callback.MIP_OBJBND))
                        nodes = model.cbGet(gp.GRB.Callback.MIP_NODCNT)
                    else:
                        incumbent = min(e['objective'] for e in events) if events else None
                        bound = finite(model.cbGet(gp.GRB.Callback.MIPSOL_OBJBND))
                        nodes = model.cbGet(gp.GRB.Callback.MIPSOL_NODCNT)
                    gap = max(0.,incumbent-bound)/max(1e-9,abs(incumbent)) if incumbent is not None and bound is not None else None
                    row = dict(case=case,stage='optimizing',pid=os.getpid(),elapsed_seconds=elapsed,cpu_seconds=cpu_clock(),
                        native_incumbent=incumbent,best_bound=bound,native_gap_percent=None if gap is None else 100*gap,
                        first_integer_solution_seconds=first[0],nodes=nodes,updated_utc=utc_now())
                    progress.append(row)
                    save(folder/'live.json',row)
                    save(folder/'incumbents.json',events)
                    save(folder/'progress.json',progress)
                    last_write[0] = elapsed
                # Only an explicit user stop creates this marker; existence is
                # checked without reading any result, route or historical data.
                if (HERE/'STOP_REQUESTED').exists():
                    model.terminate()
            return super().optimize(observe)

    gp.Model = ObservedModel
    return events,calls,progress


def worker(case):
    process_begun = time.perf_counter()
    plan = read(HERE/'plan.json')
    folder = HERE/'runs'/case
    folder.mkdir(parents=True,exist_ok=True)
    assert not (folder/'summary.json').exists()
    guard = WorkspaceReadGuard(plan['input_hashes'])
    sys.addaudithook(guard)
    sys.path[:0] = [str(SOURCE)]
    assert not any(n=='models' or n.startswith('models.') for n in sys.modules)
    import gurobipy as gp
    from models.common.cycle import load_instance
    from models.common.cycle.audit import audit_solution
    from models.routing.model_8.exact.solver import solve_exact
    from models.routing.model_8.exact import formulation,constraints,preprocessing,solution
    from tools.configuration import loader
    loader.PROJECT_ROOT = ROOT
    import_seconds = time.perf_counter()-process_begun
    begun,cpu_begun = time.perf_counter(),time.process_time()
    clock = lambda:time.perf_counter()-begun
    cpu_clock = lambda:time.process_time()-cpu_begun
    save(folder/'live.json',dict(case=case,stage='loading',pid=os.getpid(),elapsed_seconds=0.,updated_utc=utc_now()))
    events,calls,progress = install_observer(gp,folder,clock,cpu_clock,case)
    problem = load_instance(*loader.load_routing_case(plan['case_paths'][case],use_pool_config=False))
    assert list(problem.batch_count_candidates) == plan['original_K']
    load_seconds = clock()
    save(folder/'live.json',dict(case=case,stage='building',pid=os.getpid(),elapsed_seconds=clock(),updated_utc=utc_now()))
    result = solve_exact(problem,time_limit=UNBOUNDED_REQUEST_SECONDS,seed=SEED,verbose=False,**plan['options'])
    wall_seconds,cpu_seconds = clock(),cpu_clock()
    info = result['info']
    assert len(calls) == 1 and calls[0]['NumStart'] == 0
    assert not guard.blocked
    assert not info.get('mip_start_supplied',False) and not info['solver_config']['mip_start']
    assert not any(n.startswith('models.routing.model_8.alns') for n in sys.modules)
    audit_begun = time.perf_counter()
    audit = audit_solution(problem,result) if info.get('objective') is not None else dict(valid=False,errors=[])
    audit_seconds = time.perf_counter()-audit_begun
    valid = bool(audit['valid'] and audit.get('periodic_replay',{}).get('valid'))
    optimal = bool(valid and info['status']=='OPTIMAL' and info.get('proven_optimal') and info['complete_count_domain'])
    if optimal:
        assert abs(audit['objective']-info['objective']) <= 1e-6
        assert abs(audit['objective']-info['best_bound']) <= max(1e-6,abs(audit['objective'])*1e-7)
    summary = dict(case=case,seed=SEED,status=info['status'],proven_optimal=optimal,
        objective=audit.get('objective') if valid else None,best_bound=info.get('best_bound'),
        native_mip_gap=info.get('mip_gap'),valid=valid,periodic_valid=valid,
        input_loading_seconds=load_seconds,module_initialization_seconds=import_seconds,
        total_cold_solve_seconds=wall_seconds,cpu_seconds=cpu_seconds,build_seconds=info.get('build_time'),
        native_optimization_seconds=info.get('mip_runtime'),final_independent_audit_seconds=audit_seconds,
        first_native_integer_solution_seconds=events[0]['elapsed_seconds'] if events else None,
        K=result.get('cycle_batch_count'),N=result.get('container_pool_size'),original_K=list(problem.batch_count_candidates),
        external_initial_solution=False,historical_solution_read=False,known_optimum_target_used=False,
        input_read_paths=sorted(guard.read_inputs),blocked_read_count=len(guard.blocked),optimize_calls=calls,
        completion_utc=utc_now())
    save(folder/'result.json',result)
    save(folder/'audit.json',audit)
    save(folder/'incumbents.json',events)
    save(folder/'progress.json',progress)
    save(folder/'summary.json',summary)
    save(folder/'live.json',dict(case=case,stage='complete' if optimal else 'not_proven',**{k:summary[k]
         for k in ('status','proven_optimal','objective','best_bound','total_cold_solve_seconds','completion_utc')}))
    print(json.dumps(summary,ensure_ascii=False),flush=True)
    return 0 if optimal else 2


def run():
    plan = read(HERE/'plan.json')
    assert all(sha(HERE/name)==digest for name,digest in plan['driver_hashes'].items())
    assert source_hashes()==plan['source_hashes']
    assert all(sha(p)==digest for p,digest in plan['input_hashes'].items())
    started = time.perf_counter()
    completed = []
    save(HERE/'batch_status.json',dict(stage='running',started_utc=utc_now(),pid=os.getpid(),completed=[],current=None,
        stopping_rule=plan['stopping_rule']))
    for case in plan['cases']:
        folder = HERE/'runs'/case
        summary_path = folder/'summary.json'
        if summary_path.exists():
            assert read(summary_path)['proven_optimal'],case
            completed.append(case)
            continue
        if (HERE/'STOP_REQUESTED').exists():
            return
        folder.mkdir(parents=True,exist_ok=True)
        save(HERE/'batch_status.json',dict(stage='running',pid=os.getpid(),completed=completed,current=case,
            elapsed_batch_seconds=time.perf_counter()-started,updated_utc=utc_now()))
        environment = {**os.environ,'PYTHONHASHSEED':'0',**{k:'1' for k in
            ('OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS','NUMEXPR_NUM_THREADS')}}
        print(json.dumps(dict(stage='start',case=case,completed=len(completed),seed=SEED),ensure_ascii=False),flush=True)
        with (folder/'worker.log').open('w',encoding='utf-8') as log:
            process = subprocess.run([sys.executable,'-X','utf8',str(Path(__file__).resolve()),'worker','--case',case],
                cwd=ROOT,env=environment,stdout=log,stderr=subprocess.STDOUT)
        if process.returncode:
            save(HERE/'batch_status.json',dict(stage='needs_attention',completed=completed,current=case,
                exit_code=process.returncode,updated_utc=utc_now()))
            raise RuntimeError(f'{case} did not finish with audited global optimality; inspect {folder}/worker.log')
        row = read(summary_path)
        assert row['proven_optimal'] and row['valid'] and row['periodic_valid']
        completed.append(case)
        print(json.dumps(row,ensure_ascii=False),flush=True)
    assert source_hashes()==plan['source_hashes']
    assert all(sha(p)==digest for p,digest in plan['input_hashes'].items())
    save(HERE/'batch_status.json',dict(stage='complete',completed=completed,current=None,
        elapsed_batch_seconds=time.perf_counter()-started,completed_utc=utc_now(),source_and_input_hashes_unchanged=True))


if __name__=='__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['prepare','run','worker'])
    parser.add_argument('--case',choices=CASES)
    args = parser.parse_args()
    if args.action=='prepare':
        prepare()
    elif args.action=='run':
        run()
    else:
        sys.exit(worker(args.case))
