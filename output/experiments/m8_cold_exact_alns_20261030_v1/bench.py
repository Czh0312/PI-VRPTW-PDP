"""Matched, single-seed cold starts; this driver never reads reference solutions."""
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
SOURCE = HERE/'source'
CASES = [f'S{i:02}' for i in range(1, 10)]
SEED = 42
EXACT_OPTIONS = dict(strengthening='physical_time', preprocessing=True, vehicle_symmetry=True,
                     milp_options=dict(formulation='compact', threads=1))


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def source_hashes():
    return {p.relative_to(SOURCE).as_posix(): sha(p) for p in SOURCE.rglob('*.py')}


def prepare():
    if (HERE/'plan.json').exists() or SOURCE.exists():
        raise FileExistsError('The cold-start experiment is already frozen')
    shutil.copytree(ROOT/'models', SOURCE/'models', ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    shutil.copytree(ROOT/'tools/configuration', SOURCE/'tools/configuration',
                    ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    top = ROOT/'tools/__init__.py'
    if top.exists():
        shutil.copy2(top, SOURCE/'tools/__init__.py')
    else:
        (SOURCE/'tools/__init__.py').write_text('', encoding='utf-8')
    inputs, case_paths = {}, {}
    for case in CASES:
        path = ROOT/f'data/formal_benchmark27_uniform_7_20_50_v1/model_8/small/{case}/case.json'
        case_paths[case] = str(path)
        files = [path]
        config = read(path)
        for key in ('data','routing_config','orders','operating_policy','economic_config',
                    'dataset_metadata','parameter_contract','batch_count_certificate'):
            if isinstance(config.get(key), str):
                files.append(ROOT/config[key])
        for path in files:
            relative = path.relative_to(ROOT)
            inputs[str(path)] = sha(path)
            target = HERE/'inputs'/relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
    jobs = []
    for phase_index, seconds in enumerate((5, 30)):
        for case_index, case in enumerate(CASES):
            methods = ('alns','exact') if (case_index+phase_index)%2 == 0 else ('exact','alns')
            jobs.extend(dict(case=case, method=method, seconds=seconds) for method in methods)
    plan = dict(cases=CASES, case_paths=case_paths, seed=SEED, budgets_seconds=[5,30], jobs=jobs,
        exact_options=EXACT_OPTIONS, alns_options=dict(max_iterations=10**9, budget_strategy='adaptive'),
        threads=1, concurrency=1, external_initial_solution=False, historical_data_read=False,
        offline_references_read_by_worker=False, original_K=list(range(1,8)), source_hashes=source_hashes(),
        input_hashes=inputs, driver_sha256=sha(Path(__file__)),
        budget_scope='input load + precheck + construction + search/optimization + internal audit + polishing',
        excluded='interpreter/module startup and final independent audit; both separately timed',
        observer='Gurobi MIPSOL callback only records time/objective; no starts, hints, cuts or parameter changes')
    save(HERE/'plan.json', plan)
    print(json.dumps(dict(frozen_source_files=len(plan['source_hashes']), input_files=len(inputs),
                          jobs=len(jobs), root_seed=SEED, nominal_solver_seconds=630)))


def path_key(path):
    return os.path.normcase(os.path.abspath(os.fsdecode(path)))


class WorkspaceReadGuard:
    """Reject workspace data reads outside the frozen raw-input allowlist."""
    def __init__(self, allowed):
        self.root = path_key(ROOT)+os.sep
        self.source = path_key(SOURCE)+os.sep
        self.allowed = {path_key(p) for p in allowed}
        self.read_inputs, self.blocked = set(), []
        self.enabled = True

    def __call__(self, event, args):
        if not self.enabled or event != 'open' or isinstance(args[0], int):
            return
        path, mode, flags = args
        # A write-only result/log operation cannot supply a historical solution.
        if flags & os.O_WRONLY:
            return
        key = path_key(path)
        if not key.startswith(self.root):
            return
        if key in self.allowed:
            self.read_inputs.add(key)
            return
        if key.startswith(self.source) and Path(key).suffix in ('.py','.pyc','.pyd','.dll'):
            return
        self.blocked.append(key)
        raise PermissionError('Cold-start data-read allowlist rejected: '+key)


def install_model_observer(gp, clock):
    """Keep native optimize behavior, adding only an in-memory event recorder."""
    native = gp.Model
    events, calls = [], []

    class ObservedModel(native):
        def optimize(self, callback=None):
            assert callback is None, 'Unexpected algorithm callback'
            self.update()
            assert self.NumStart == 0, 'External MIP start detected'
            assert self.Params.Threads == 1 and self.Params.Seed == SEED
            calls.append(dict(NumStart=self.NumStart, threads=self.Params.Threads,
                              seed=self.Params.Seed, variables=self.NumVars,
                              constraints=self.NumConstrs+self.NumGenConstrs))

            def observe(model, where):
                if where == gp.GRB.Callback.MIPSOL:
                    value = model.cbGet(gp.GRB.Callback.MIPSOL_OBJ)
                    if math.isfinite(value) and abs(value) < 1e90:
                        events.append(dict(elapsed=clock(), objective=value,
                            native_runtime=model.cbGet(gp.GRB.Callback.RUNTIME),
                            scope='native incumbent; only final plan independently audited'))
            return super().optimize(observe)

    gp.Model = ObservedModel
    return events, calls


def worker(args):
    process_begun = time.perf_counter()
    plan = read(HERE/'plan.json')
    guard = WorkspaceReadGuard(plan['input_hashes'])
    sys.addaudithook(guard)
    sys.path[:0] = [str(SOURCE)]
    assert not any(name == 'models' or name.startswith('models.') for name in sys.modules)
    from models.common.cycle import load_instance
    from models.common.cycle.audit import audit_solution
    from tools.configuration import loader
    # The copied loader resolves the unchanged original raw paths for both methods.
    loader.PROJECT_ROOT = ROOT
    incumbent_events, optimize_calls = [], []
    if args.method == 'exact':
        import gurobipy as gp
        from models.routing.model_8.exact.solver import solve_exact
        from models.routing.model_8.exact import formulation, constraints, preprocessing, solution
        import models.common.cycle.unified_milp
    else:
        from models.routing.model_8.alns.solver import solve_alns
        from models.routing.model_8.alns import search, fixed_calendar_search, calendar_polish, chain_repair
    import_seconds = time.perf_counter()-process_begun
    begun, cpu_begun = time.perf_counter(), time.process_time()
    if args.method == 'exact':
        incumbent_events, optimize_calls = install_model_observer(gp, lambda: time.perf_counter()-begun)
    problem = load_instance(*loader.load_routing_case(plan['case_paths'][args.case], use_pool_config=False))
    load_seconds = time.perf_counter()-begun
    assert list(problem.batch_count_candidates) == plan['original_K']
    remaining = args.seconds-load_seconds
    assert remaining > 0
    if args.method == 'exact':
        result = solve_exact(problem, time_limit=remaining, seed=SEED, verbose=False, **plan['exact_options'])
    else:
        result = solve_alns(problem, max_time=remaining, max_iterations=10**9, seed=SEED,
                            initial_result=None, budget_strategy='adaptive')
    wall_seconds, cpu_seconds = time.perf_counter()-begun, time.process_time()-cpu_begun
    info = result['info']
    if args.method == 'alns':
        assert info.get('initial_incumbent_cost') is None
        assert not info['proven_optimal'] and info.get('best_bound') is None
        assert not any(n.startswith('gurobipy') or (n.startswith('models.') and '.exact' in n) for n in sys.modules)
        for visit in result.get('count_search_visits', []):
            incumbent_events.extend(dict(elapsed=load_seconds+visit['started']+h['time'], objective=h['objective'],
                scope='ALNS saved candidate after internal full audit')
                for h in visit.get('local_history', []) if h.get('objective') is not None)
    else:
        assert not info.get('mip_start_supplied', False)
        assert not info['solver_config']['mip_start']
        assert len(optimize_calls) <= 1
        assert not any(n.startswith('models.routing.model_8.alns') for n in sys.modules)
    audit_begun = time.perf_counter()
    has_solution = info.get('objective') is not None
    audit = audit_solution(problem, result) if has_solution else dict(valid=False, errors=[], reason='no incumbent returned')
    audit_seconds = time.perf_counter()-audit_begun
    valid = has_solution and audit['valid'] and audit['periodic_replay']['valid']
    if has_solution:
        assert valid, audit
        assert abs(audit['objective']-info['objective']) <= 1e-6
    assert not guard.blocked
    folder = HERE/'runs'/f'b{int(args.seconds):02}'/args.case/args.method
    assert not folder.exists()
    events = sorted(incumbent_events, key=lambda e:e['elapsed'])
    summary = dict(case=args.case, method=args.method, budget_seconds=args.seconds, seed=SEED,
        input_loading_seconds=load_seconds, solver_wall_seconds=wall_seconds, cpu_seconds=cpu_seconds,
        module_initialization_seconds=import_seconds, final_independent_audit_seconds=audit_seconds,
        valid=bool(valid), periodic_valid=bool(valid), objective=audit.get('objective') if valid else None,
        status=info['status'], proven_optimal=bool(valid and info.get('proven_optimal')),
        proven_infeasible=bool(info.get('proven_infeasible')), best_bound=info.get('best_bound'),
        native_mip_gap=info.get('mip_gap'), K=result.get('cycle_batch_count'), N=result.get('container_pool_size'),
        build_seconds=info.get('build_time'), mip_seconds=info.get('mip_runtime'),
        first_native_or_saved_candidate_seconds=events[0]['elapsed'] if events else None,
        original_K=list(problem.batch_count_candidates), external_initial_solution=False,
        historical_data_read=False, exact_reference_used=False, read_guard_blocked_count=len(guard.blocked),
        input_read_paths=sorted(guard.read_inputs), optimize_calls=optimize_calls,
        physical_time_tick_seconds=problem.operating_policy.time_tick_seconds,
        economic_profile_hash=getattr(problem.cost_profile, 'hash', None))
    save(folder/'result.json', result)
    save(folder/'audit.json', audit)
    save(folder/'events.json', events)
    save(folder/'summary.json', summary)
    print('COLD_RUN '+json.dumps(summary, ensure_ascii=False), flush=True)


def run():
    plan = read(HERE/'plan.json')
    assert plan['driver_sha256'] == sha(Path(__file__))
    assert source_hashes() == plan['source_hashes']
    assert all(sha(p) == digest for p,digest in plan['input_hashes'].items())
    completed_jobs = []
    for job in plan['jobs']:
        folder = HERE/'runs'/f"b{job['seconds']:02}"/job['case']/job['method']
        if (folder/'summary.json').exists():
            completed_jobs.append(job)
            continue
        environment = {**os.environ, 'PYTHONHASHSEED':'0', **{k:'1' for k in
            ('OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS','NUMEXPR_NUM_THREADS')}}
        started = time.perf_counter()
        result = subprocess.run([sys.executable,'-X','utf8',str(Path(__file__).resolve()),'worker',
            '--case',job['case'],'--method',job['method'],'--seconds',str(job['seconds'])], cwd=ROOT,
            env=environment, capture_output=True, text=True, encoding='utf-8', timeout=job['seconds']+120)
        process_seconds = time.perf_counter()-started
        log = HERE/'logs'/f"b{job['seconds']:02}_{job['case']}_{job['method']}.log"
        log.parent.mkdir(exist_ok=True)
        log.write_text(result.stdout+result.stderr, encoding='utf-8')
        if result.returncode:
            raise RuntimeError(result.stdout+result.stderr)
        row = read(folder/'summary.json')
        row['worker_process_wall_seconds'] = process_seconds
        save(folder/'summary.json',row)
        print(json.dumps({k:row[k] for k in ('budget_seconds','case','method','status','objective',
              'solver_wall_seconds','valid','proven_optimal','build_seconds','mip_seconds')}, ensure_ascii=False), flush=True)
        completed_jobs.append(job)
        save(HERE/'progress.json',dict(completed=len(completed_jobs),total=len(plan['jobs']),last=job))
    assert source_hashes() == plan['source_hashes']
    assert all(sha(p) == digest for p,digest in plan['input_hashes'].items())
    assert sha(Path(__file__)) == plan['driver_sha256']
    save(HERE/'completion.json',dict(jobs=len(completed_jobs),source_and_input_hashes_unchanged=True))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['prepare','run','worker'])
    parser.add_argument('--case',choices=CASES)
    parser.add_argument('--method',choices=['alns','exact'])
    parser.add_argument('--seconds',type=float,default=5.)
    args = parser.parse_args()
    if args.action == 'prepare':
        prepare()
    elif args.action == 'run':
        run()
    else:
        worker(args)
