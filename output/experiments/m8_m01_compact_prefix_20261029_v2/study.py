"""One M01 / seed-42 / 1200-second complete compact-arc experiment."""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import sys
import time
import traceback
import zipfile

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0,str(ROOT))

from experiments.runs.model8_compact_prefix import transport_certificate, build_prefix, extract_prefix, supply_start
from experiments.runs.model8_frontier import stock_frontier, normalize_vehicle_ids, add_frontier_cuts
from models.common.cycle import load_instance
from models.common.cycle.audit import audit_solution
from models.common.solver_config import read_milp_info
from tools.configuration.loader import load_routing_case

CASE = ROOT / 'data/formal_benchmark27_uniform_7_20_50_v1/model_8/medium/M01/case.json'
WITNESS = ROOT / 'output/experiments/m8_medium_optimized_20261029_v2/initial_audited_result.json'


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def save(name,value):
    path = HERE/name
    pending = path.with_suffix(path.suffix+'.tmp')
    pending.write_text(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
    for attempt in range(5):
        try:
            pending.replace(path)
            return
        except PermissionError:
            if attempt == 4:
                raise
            time.sleep(.02)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def finite(value):
    return float(value) if math.isfinite(value) and abs(value)<1e90 else None


def prepare():
    if (HERE/'manifest.json').exists():
        raise FileExistsError('Do not overwrite a registered experiment')
    cfg,fleet = load_routing_case(CASE,use_pool_config=False)
    p = load_instance(cfg,fleet)
    witness = read(WITNESS)
    audit = audit_solution(p,witness)
    assert audit['valid'] and audit['periodic_replay']['valid']
    files = [CASE,WITNESS,Path(__file__),ROOT/'experiments/runs/model8_compact_prefix.py', ROOT/'experiments/runs/model8_frontier.py', ROOT/'experiments/runs/model8_stock_relaxation.py']
    for key in ('data','routing_config','economic_config','orders','operating_policy','parameter_contract',
                'batch_count_certificate','dataset_metadata'):
        if isinstance(cfg.get(key),str):
            files.append((ROOT/cfg[key]).resolve())
    for folder in ('models/common','models/cost','models/routing/model_8/exact','tools/configuration'):
        files += list((ROOT/folder).rglob('*.py'))
    manifest = dict(case='M01',customers=len(p.orders),original_K=list(p.batch_count_candidates),
        registered_at=datetime.now(timezone.utc).isoformat(),seed=42,threads=1,budget_seconds=1200,
        historical_feasible_plan_used=True,mip_start_supplied=True,historical_lower_bound_used=False,
        original_time_tick_seconds=p.operating_policy.time_tick_seconds,
        original_time_tick_subdivision=p.operating_policy.time_tick_subdivision,
        initial_audited_cost=audit['objective'],full_route_library_used=False,
        timing_scope='Reload/audit, fresh transport certificate, complete prefix arc model construction, '
                     'audited MIP-start injection, optimize, extraction and original full-domain audit; '
                     'launcher/preflight, source freezing and subsequent validation outside algorithm budget',
        source_and_input_hashes={str(path.resolve()):sha(path) for path in sorted(set(files))})
    save('manifest.json',manifest)
    save('initial_audit.json',audit)
    save('initial_result.json',witness)
    with zipfile.ZipFile(HERE/'frozen_sources_inputs.zip','x',compression=zipfile.ZIP_DEFLATED) as archive:
        for path in manifest['source_and_input_hashes']:
            archive.write(path,Path(path).relative_to(ROOT).as_posix())
    print(dict(customers=len(p.orders),K=list(p.batch_count_candidates),upper=audit['objective'],seconds=1200),flush=True)


def run():
    import gurobipy as gp
    manifest = read(HERE/'manifest.json')
    if (HERE/'started.json').exists():
        raise FileExistsError('An existing attempt is immutable')
    assert all(sha(path)==value for path,value in manifest['source_and_input_hashes'].items())
    begun = time.perf_counter()
    deadline = begun+manifest['budget_seconds']
    save('started.json',dict(at=datetime.now(timezone.utc).isoformat()))
    p = load_instance(*load_routing_case(CASE,use_pool_config=False))
    best = read(HERE/'initial_result.json')
    audit = audit_solution(p,best)
    assert audit['valid'] and audit['periodic_replay']['valid']
    upper = audit['objective']
    mark = time.perf_counter()
    certificate = transport_certificate(p,upper)
    certificate_seconds = time.perf_counter()-mark
    save('transport_certificate_initial.json',certificate)
    frontier = stock_frontier(p,certificate,deadline=min(begun+120,deadline-300),per_pair_seconds=3,
        on_progress=lambda value: save('progress.json',dict(elapsed=time.perf_counter()-begun,**value)))
    save('stock_frontier.json',frontier)
    certificate['initial_retained_K'] = certificate['retained_K']
    certificate['retained_K'] = frontier['retained_K']
    certificate['stock_global_lower_bound'] = frontier['global_lower_bound']
    save('transport_certificate.json',certificate)
    assert (best['cycle_batch_count'],best['container_pool_size']) in {(r['K'],r['N']) for r in frontier['retained_pairs']}
    print(dict(stage='certificate',retained_K=certificate['retained_K'],seconds=certificate_seconds),flush=True)
    save('progress.json',dict(stage='model_construction',elapsed=time.perf_counter()-begun,
                              retained_K=certificate['retained_K'],upper=upper))
    mark = time.perf_counter()
    m,ctx = build_prefix(p,certificate['retained_K'],certificate=certificate,deadline=deadline,vehicle_symmetry=True)
    strengthening = add_frontier_cuts(p,m,ctx,frontier,certificate)
    save('strengthening.json',strengthening)
    build_seconds = time.perf_counter()-mark
    events = []
    last_progress = [-20.]
    best_native = [None]
    try:
        best = normalize_vehicle_ids(p,best)
        save('normalized_initial_result.json',best)
        start_info = supply_start(p,m,ctx,best)
        save('mip_start.json',start_info)
        m.Params.Threads = 1
        m.Params.Seed = 42
        m.Params.MIPGap = 0
        m.Params.MIPGapAbs = 0
        m.Params.Method = 2
        m.Params.DegenMoves = 0
        m.Params.CutPasses = 3
        m.Params.Cuts = 1
        m.Params.MIPFocus = 1
        m.Params.Heuristics = .2
        save('solver_parameters.json',dict(Threads=1,Seed=42,MIPGap=0,MIPGapAbs=0,Method=2,
            DegenMoves=0,CutPasses=3,Cuts=1,MIPFocus=1,Heuristics=.2))
        m.Params.TimeLimit = max(.01,deadline-time.perf_counter())
        m.Params.LogFile = os.path.relpath(HERE/'gurobi.log',ROOT)
        m.Params.OutputFlag = 1
        m.Params.LogToConsole = 0
        metadata = dict(variables=m.NumVars,binary_variables=m.NumBinVars,
            constraints=m.NumConstrs+m.NumGenConstrs,build_seconds=build_seconds,
            original_K=list(p.batch_count_candidates),retained_K=certificate['retained_K'],
            original_clock_unchanged=True,physical_E2_slots=len(ctx['e2']),physical_E1_slots=len(ctx['e1']))
        save('model_size.json',metadata)
        print(dict(stage='optimization',**metadata),flush=True)

        def observe(model,where):
            if where == gp.GRB.Callback.MIPSOL:
                value = finite(model.cbGet(gp.GRB.Callback.MIPSOL_OBJ))
                elapsed = model.cbGet(gp.GRB.Callback.RUNTIME)
                if value is not None and (best_native[0] is None or value < best_native[0]-1e-8):
                    best_native[0] = value
                    events.append(dict(native_seconds=elapsed,objective=value,
                                       independently_audited=False))
                    print(dict(stage='native_incumbent',seconds=elapsed,objective=value),flush=True)
            elif where == gp.GRB.Callback.MIP:
                elapsed = model.cbGet(gp.GRB.Callback.RUNTIME)
                if elapsed-last_progress[0] >= 10:
                    last_progress[0] = elapsed
                    bound = finite(model.cbGet(gp.GRB.Callback.MIP_OBJBND))
                    incumbent = finite(model.cbGet(gp.GRB.Callback.MIP_OBJBST))
                    try:
                        save('progress.json',dict(stage='optimization',elapsed=time.perf_counter()-begun,
                            native_seconds=elapsed,native_incumbent=incumbent,native_lower_bound=bound,
                            known_audited_upper=upper,nodes=model.cbGet(gp.GRB.Callback.MIP_NODCNT)))
                    except OSError:
                        pass

        m.optimize(observe)
        info = read_milp_info(m,proof_scope='complete_original_M8_competitive_domain_optional_prefix_arc_MILP',
                              build_time=build_seconds)
        fallback = max(min(certificate['per_K_lower_bound'].values()),frontier['global_lower_bound'])
        native_bound = finite(m.ObjBound)
        lower = max(fallback,native_bound) if native_bound is not None else fallback
        if m.Status in (gp.GRB.INFEASIBLE,gp.GRB.INF_OR_UNBD):
            raise RuntimeError('Complete prefix model contradicts the audited original witness')
        if m.SolCount:
            candidate = extract_prefix(p,m,ctx,info)
            save('native_result.json',candidate)
            if candidate['audit']['objective'] <= upper+1e-6:
                best = candidate
        audit = audit_solution(p,best)
        assert audit['valid'] and audit['periodic_replay']['valid']
        upper = audit['objective']
        assert lower <= upper+1e-5
        row = dict(case='M01',customers=20,seed=42,threads=1,status=info['status'],
            proven_optimal=upper-lower<=1e-6,audited_upper_bound=upper,global_lower_bound=lower,
            absolute_gap=max(0.,upper-lower),gap_over_upper=max(0.,upper-lower)/abs(upper),
            selected_K=best['cycle_batch_count'],selected_N=best['container_pool_size'],
            original_K=list(p.batch_count_candidates),retained_K=certificate['retained_K'],
            budget_seconds=1200,total_wall_seconds=time.perf_counter()-begun,
            certificate_seconds=certificate_seconds,frontier_seconds=frontier['seconds'],build_seconds=build_seconds,
            native_runtime=m.Runtime,native_status=int(m.Status),native_objective=m.ObjVal if m.SolCount else None,
            native_lower_bound=native_bound,variables=m.NumVars,binary_variables=m.NumBinVars,
            constraints=m.NumConstrs+m.NumGenConstrs,sol_count=m.SolCount,nodes=m.NodeCount,
            mip_start_supplied=True,initial_audited_cost=manifest['initial_audited_cost'],
            full_route_library_used=False,business_audit_valid=True,periodic_replay_valid=True,
            sources_and_inputs_unchanged=all(sha(path)==value for path,value in manifest['source_and_input_hashes'].items()))
        save('best_audited_result.json',best)
        save('audit.json',audit)
        save('native_info.json',info)
        save('incumbents.json',events)
        save('summary.json',row)
        save('progress.json',dict(stage='complete',**row))
        print(row,flush=True)
    except Exception as error:
        save('error.json',dict(error=str(error),traceback=traceback.format_exc()))
        raise
    finally:
        m.dispose()


if __name__ == '__main__':
    if sys.argv[1] == 'prepare':
        prepare()
    elif sys.argv[1] == 'run':
        run()
    else:
        raise ValueError('Use prepare or run')
