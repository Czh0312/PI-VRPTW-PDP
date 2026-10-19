"""Controlled, logged MILP diagnostics. Production solver code is not changed."""
from datetime import datetime, timezone
from importlib import import_module
import argparse
import hashlib
import json
import math
from pathlib import Path
import subprocess
import sys
import time
import traceback
import zipfile

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[2]
sys.path.insert(0, str(ROOT))


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def now():
    return datetime.now(timezone.utc).isoformat()


def problem_for(model):
    from models.common.cycle import load_instance
    from tools.configuration.loader import load_routing_case
    case = ROOT/f'data/formal_benchmark27_v1/model_{model}/small/S04/case.json'
    return load_instance(*load_routing_case(case, use_pool_config=False))


def get_witness(model):
    data = ROOT/'data/formal_benchmark27_v1/validation'
    certificate = read(data/'feasibility.json')
    with zipfile.ZipFile(data/'witnesses.zip') as archive:
        name = f'S04/model_{model}.json'
        payload = archive.read(name)
        assert hashlib.sha256(payload).hexdigest() == certificate['witness_sha256'][name]
        return json.loads(payload)


def constrain_witness(m, ctx, witness):
    """Fix only physical business decisions; leave auxiliary variables free."""
    import gurobipy as gp
    bindings = []
    def pin(handle, value, semantic):
        if isinstance(handle, (int, float)):
            assert abs(handle-value) < 1e-7, (semantic, handle, value)
            return
        name = f'diag_pin_{len(bindings)}'
        m.addConstr(handle == value, name=name)
        bindings.append(dict(name=name, semantic=semantic, value=value))
    pin(ctx['pool'], witness['container_pool_size'], 'pool')
    for (order, batch), variable in ctx['assignments'].items():
        pin(variable, int(witness['order_batch_assignment'][order] == batch), f'assign:{order}:{batch}')
    for batch, cutoff in ctx['cutoffs'].items():
        pin(cutoff, witness['batch_cutoffs'][batch], f'cutoff:{batch}')
    for batch in witness['batches']:
        pin(ctx['batch_starts'][batch['id']], batch['start'], f"batch_start:{batch['id']}")
    for layer in ('e1', 'e2'):
        trips = {trip['trip_id']: trip for trip in witness[layer+'_trips']}
        matched = set()
        for route in ctx[layer]:
            trip = trips.get(route['label'])
            pin(route['used'], int(trip is not None), f"used:{route['label']}")
            lookup = {(node['site'] if layer == 'e1' else node['order'].id, node['kind']): i
                      for i, node in enumerate(route['nodes'])}
            order = []
            if trip is not None:
                matched.add(trip['trip_id'])
                pin(route['start'], trip['departure'], f"start:{route['label']}")
                pin(route['end'], trip['return_complete'], f"end:{route['label']}")
                for event in trip['events']:
                    key = (event['site_id' if layer == 'e1' else 'order_id'], event['kind'])
                    index = lookup[key]
                    order.append(index)
                    pin(route['times'][index], event['start'], f"time:{route['label']}:{key}")
                    pin(route['nodes'][index]['quantity'], event['quantity'], f"quantity:{route['label']}:{key}")
            chain = [-1]+order+[-2]
            selected = set(zip(chain, chain[1:])) if trip is not None else set()
            assert selected <= set(route['x']), ('missing_legal_arc', route['label'], selected-set(route['x']))
            for edge, variable in route['x'].items():
                pin(variable, int(edge in selected), f"arc:{route['label']}:{edge}")
            visited = set(order)
            for index, variable in route['visit'].items():
                pin(variable, int(index in visited), f"visit:{route['label']}:{index}")
        assert matched == set(trips), ('unmapped_trips', set(trips)-matched)
    return bindings


def attach_and_audit(parent, child, m, ctx, info, count):
    from models.common.cycle.audit import audit_solution
    from models.common.cycle.unified_milp import _identity
    from models.common.solver_config import accept_milp_audit
    extract = import_module(f'models.routing.model_{parent.model_number}.exact.solution').extract_solution
    result = extract(child, m, ctx, info)
    if parent.model_number == 6:
        from models.common.cycle.uniform import attach_uniform_decisions
        attach_uniform_decisions(parent, result, count)
    else:
        from models.common.cycle.calendar import attach_calendar_decisions
        boundaries = [batch['start'] for batch in result['batches']]+[child.operating_policy.cycle_end]
        attach_calendar_decisions(parent, result, count, boundaries)
    _identity(parent, result)
    result['audit'] = audit_solution(parent, result)
    accept_milp_audit(result, float(m.ObjVal))
    return result


def worker(job):
    import gurobipy as gp
    from models.common.cycle.audit import audit_solution
    from models.common.solver_config import read_milp_info
    if job.get('encoding') == 'perspective_linear':
        import models.common.cycle.unified_milp as assembly
        from perspective_assembly import append_block
        assembly.append_block = append_block  # This diagnostic worker process only.
    path = OUT/'runs'/job['id']
    if path.exists():
        raise RuntimeError('Refusing to overwrite an existing diagnostic attempt')
    path.mkdir(parents=True)
    write(path/'job.json', job)
    parent = problem_for(job['model'])
    begun = time.perf_counter()
    compact = job['formulation'] == 'compact'
    unified = job['scope'] == 'unified'
    builder = import_module(f"models.routing.model_{job['model']}.exact.model").build_model
    build_limit = 180 if job['kind'] == 'consistency' else 300
    m, outer = builder(parent, batch_count=None if unified else job['count'],
                       verbose=False, compact_e2=compact, deadline=begun+build_limit)
    try:
        build_seconds = time.perf_counter()-begun
        # Gurobi 11's native file API does not accept this Windows Unicode
        # absolute path. An ASCII path relative to the project works unchanged.
        m.Params.LogFile = (path/'gurobi.log').relative_to(ROOT).as_posix()
        m.Params.OutputFlag = 1
        m.Params.Threads = 1
        m.Params.Seed = 42
        original_size = dict(variables=m.NumVars, binaries=m.NumBinVars,
                             linear_constraints=m.NumConstrs, indicators=m.NumGenConstrs)
        witness = None
        child = None
        context = None
        if job['kind'] == 'consistency':
            witness = get_witness(job['model'])
            source_audit = audit_solution(parent, witness)
            assert source_audit['valid'], source_audit.get('errors')
            if unified:
                m.addConstr(outer['selectors'][1] == 1, name='diag_select_witness_K1')
                block = outer['blocks'][1]
                child, context = block['problem'], block['context']
            else:
                child, context = parent.for_count(job['count']), outer
            bindings = constrain_witness(m, context, witness)
            write(path/'witness_bindings.json', bindings)
            m.Params.TimeLimit = 60
        else:
            m.Params.TimeLimit = max(0., 300-build_seconds)
        m.update()
        assert m.NumStart == 0
        m.optimize()
        scope = 'fixed_physical_witness_consistency_only' if witness else (
            'full_input_K_domain' if unified else f"fixed_K_{job['count']}_diagnostic_only")
        info = read_milp_info(m, proof_scope=scope, build_time=build_seconds)
        result = None
        chosen = None
        if m.SolCount:
            if unified:
                chosen = next(k for k, variable in outer['selectors'].items() if variable.X > .5)
                child, context = outer['blocks'][chosen]['problem'], outer['blocks'][chosen]['context']
            else:
                chosen = job['count']
                child, context = parent.for_count(chosen), outer
            result = attach_and_audit(parent, child, m, context, info, chosen)
            info = result['info']
            write(path/'result.json', result)
            if witness:
                assert result['audit']['valid']
                assert math.isclose(result['audit']['objective'], source_audit['objective'], abs_tol=1e-5)
        if witness and m.Status == gp.GRB.INFEASIBLE:
            m.Params.TimeLimit = 60
            m.computeIIS()
            m.write(str(path/'witness_conflict.ilp'))
        row = dict(**job, status=info['status'], valid=bool(result and result.get('audit', {}).get('valid')),
                   proven_optimal=bool(info['proven_optimal']), proven_infeasible=bool(info['proven_infeasible']),
                   objective=info.get('objective'), bound=info.get('best_bound'), gap=info.get('mip_gap'),
                   selected_count=chosen, counts=list(parent.batch_count_candidates), **original_size,
                   build_seconds=build_seconds, mip_seconds=info['mip_runtime'], nodes=info['node_count'],
                   solutions=info['sol_count'], elapsed=time.perf_counter()-begun,
                   proof_scope=scope, settings=dict(threads=1, seed=42, mip_start=False, custom_callback=False),
                   completed_at=now())
        write(path/'row.json', row)
        print(json.dumps(dict(event='done', **row), ensure_ascii=False), flush=True)
        return row
    finally:
        m.dispose()


def jobs():
    result = []
    for scope in ('fixed', 'unified'):
        for model in (6, 7, 8):
            for formulation in ('expanded', 'compact'):
                result.append(dict(id=f'consistency_m{model}_{scope}_{formulation}', kind='consistency',
                                   model=model, scope=scope, count=1, formulation=formulation))
    for count in (1, 3):
        for formulation in ('expanded', 'compact'):
            result.append(dict(id=f'free_m6_K{count}_{formulation}', kind='performance',
                               model=6, scope='fixed', count=count, formulation=formulation))
    for model, formulations in ((6, ('expanded', 'compact')), (7, ('compact',)), (8, ('compact',))):
        for formulation in formulations:
            result.append(dict(id=f'free_m{model}_unified_{formulation}', kind='performance',
                               model=model, scope='unified', count=None, formulation=formulation))
    return result


def controller():
    planned = jobs()
    source_paths = sorted((ROOT/'models').rglob('*.py'))
    manifest_path = OUT/'manifest.json'
    if manifest_path.exists():
        manifest = read(manifest_path)
        assert manifest['jobs'] == planned
        assert all(hashlib.sha256((ROOT/name).read_bytes()).hexdigest() == value
                   for name, value in manifest['source_hashes'].items())
        manifest.setdefault('resumed_at', []).append(now())
        write(manifest_path, manifest)
    else:
        write(manifest_path, dict(started_at=now(), interpreter=sys.executable, python=sys.version,
          commit=subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
          source_hashes={p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in source_paths},
          case='S04', case_customers=10, jobs=planned, completed=0, workers=1,
          performance_budget_seconds=300, consistency_solve_seconds=60,
          consistency_build_limit_seconds=180, unchanged_business_domain=True))
    rows = []
    for job in planned:
        row_file = OUT/'runs'/job['id']/'row.json'
        if row_file.exists():
            rows.append(read(row_file))
            continue
        print(json.dumps(dict(event='start', **job), ensure_ascii=False), flush=True)
        log = OUT/'console'/f"{job['id']}.log"
        log.parent.mkdir(exist_ok=True)
        with log.open('w', encoding='utf-8') as stream:
            completed = subprocess.run([sys.executable, '-X', 'utf8', str(__file__), 'worker', '--job', job['id']],
                                       cwd=ROOT, stdout=stream, stderr=subprocess.STDOUT, timeout=480)
        row_file = OUT/'runs'/job['id']/'row.json'
        if completed.returncode or not row_file.exists():
            write(OUT/'attention.json', dict(job=job, exit_code=completed.returncode, log=str(log)))
            raise RuntimeError(f"Diagnostic needs inspection: {job['id']}; see {log}")
        row = read(row_file)
        rows.append(row)
        write(OUT/'summary.json', rows)
        manifest = read(OUT/'manifest.json')
        manifest['completed'] = len(rows)
        write(OUT/'manifest.json', manifest)
        print(json.dumps(dict(event='complete', **row), ensure_ascii=False), flush=True)
        if job['kind'] == 'consistency' and not row['valid']:
            raise RuntimeError('Witness mapping failed: inspect before performance comparisons')
    manifest['completed_at'] = now()
    write(OUT/'manifest.json', manifest)
    if (OUT/'attention.json').exists():
        attention = read(OUT/'attention.json')
        attention.update(resolved=True, resolved_at=now())
        write(OUT/'attention.json', attention)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('mode', choices=('controller', 'worker'))
    parser.add_argument('--job')
    args = parser.parse_args()
    if args.mode == 'worker':
        worker(next(job for job in jobs() if job['id'] == args.job))
    else:
        controller()
