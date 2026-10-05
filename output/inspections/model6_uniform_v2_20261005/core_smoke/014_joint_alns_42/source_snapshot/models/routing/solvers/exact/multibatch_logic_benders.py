"""Budgeted, resumable conditional physical-site separation for original M5.

The master always has internally enumerated COMPLETE route domains. Its
integer task partitions are hypotheses, never executable schedules. Physical
oracles retain all original paths/vehicles and relax E1 and local box stock.
Only independently replayed certificates enter actual-cost epigraph cuts.
Full-M5 proof flags remain false, including when a relaxation is optimal.
"""
from contextlib import ExitStack
from copy import deepcopy
from dataclasses import asdict
import fcntl
import hashlib
import json
import math
from pathlib import Path
import tempfile
import time
import uuid

from ...common.cycle.audit import audit_solution
from .multibatch_joint_inventory import build_joint_inventory, set_joint_projection
from .multibatch_physical_cuts import (
    add_physical_cut, lift_physical_costs, project_physical_costs)
from .multibatch_physical_site import normalize_trip_groups, physical_site_certificate


def _encoded(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'),
                      ensure_ascii=False, allow_nan=False).encode('utf-8')


def _digest(value):
    return hashlib.sha256(_encoded(value)).hexdigest()


def _file_hash(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024*1024), b''):
            digest.update(block)
    return digest.hexdigest()


def business_fingerprint(problem):
    """Hash effective directed metrics, every order/policy and physical fleet."""
    ids = sorted(problem.network.nodes)
    names = ('e1_count', 'e2_per_sat_count', 'e1_weight_cap', 'e1_volume_cap',
             'e2_weight_cap', 'e2_volume_cap', 'container_weight', 'container_volume')
    return _digest(dict(model=problem.model_number, depot=problem.depot,
        sites=sorted(problem.satellites), orders=[asdict(o) for o in sorted(problem.orders, key=lambda o:o.id)],
        policy=asdict(problem.operating_policy), fleet={n:getattr(problem.fleet,n) for n in names},
        services={s:problem.service(s) for s in problem.satellites}, nodes=ids,
        distances=[[float(problem.distance(a,b)).hex() for b in ids] for a in ids],
        travel_ticks=[[problem.travel(a,b) for b in ids] for a in ids]))


def source_fingerprint():
    # Conservative invalidation also covers imported audit/formulation rules.
    root = Path(__file__).resolve().parents[2]
    return _digest({str(p.relative_to(root)):_file_hash(p)
                    for p in sorted(root.rglob('*.py'))})


def _write(path, value):
    path = Path(path)
    temporary = path.with_name(path.name+'.tmp')
    temporary.write_bytes(_encoded(value)+b'\n')
    temporary.replace(path)


def _lock(stack, path):
    stream = stack.enter_context(Path(path).open('a+'))
    try:
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as error:
        raise RuntimeError('Another M5 separation run already owns '+str(path)) from error


def _request(problem, site, groups, supplies=None, minimum=None, maximum=None):
    groups = normalize_trip_groups(problem, site, groups or None, partial=True)
    return dict(satellite=site, trip_groups=groups, partial_groups=True,
                supply_thresholds=dict(supplies or {}), minimum_returns=dict(minimum or {}),
                maximum_returns=dict(maximum or {}))


class _CertificateCache:
    """Own-format replay proofs, not an interface for caller-created bounds."""
    def __init__(self, directory, identity):
        self.identity = identity
        self.directory = Path(directory)/_digest(identity)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.hits = self.rejected = self.calls = 0

    def path(self, request):
        return self.directory/(_digest(request)+'.json')

    def load(self, request):
        path = self.path(request)
        if not path.exists():
            return None
        try:
            record = json.loads(path.read_bytes())
            checksum = record.pop('checksum')
            proof = self.directory/record['proof_file']
            cert = record['certificate']
            if (checksum != _digest(record) or record['schema'] != 1
                    or record['identity'] != self.identity
                    or _encoded(record['request']) != _encoded(request)
                    or proof.name != record['proof_file']
                    or _file_hash(proof) != record['proof_sha256']
                    or cert['scope'] != 'M5_conditional_physical_E2_site_cost_relaxation'
                    or cert.get('proven_optimal') or cert.get('proven_infeasible')
                    or not math.isfinite(cert['bound']) or not 0 <= cert['bound'] < 1e90):
                raise ValueError('Invalid cached certificate')
            for key in request:
                if _encoded(cert[key]) != _encoded(request[key]):
                    raise ValueError('Cached conditions mismatch')
            replay = record['replay']
            if cert['subproblem_infeasible']:
                if cert['status'] != 3 or replay['status'] != 3:
                    raise ValueError('Unproved cached infeasibility')
            elif not (cert['subproblem_optimal'] and cert['status'] == replay['status'] == 2
                      and cert['bound'] <= replay['supported_bound']+1e-9):
                raise ValueError('Only completed independent proofs are resumable')
            self.hits += 1
            return record
        except (ValueError, KeyError, TypeError, OSError):
            self.rejected += 1
            return None

    def records(self):
        for path in sorted(self.directory.glob('*.json')):
            try:
                request = json.loads(path.read_bytes())['request']
            except (ValueError, KeyError, TypeError):
                self.rejected += 1
                continue
            record = self.load(request)
            if record is not None:
                yield record

    def save(self, request, certificate, replay, proof):
        record = dict(schema=1, identity=self.identity, request=request,
                      certificate=certificate, replay=replay, proof_file=proof.name,
                      proof_sha256=_file_hash(proof))
        _write(self.path(request), {**record, 'checksum':_digest(record)})
        return record


def _integer(value):
    if not math.isfinite(value) or abs(value-round(value)) > 1e-6:
        raise ValueError('Fractional values cannot define a physical condition')
    return int(round(value))


def selected_conditions(problem, context):
    """Validate each integer exact partition before deriving supply/count atoms."""
    selected = {}
    for site, data in sorted(context['routes'].items()):
        groups, seen = [], set()
        for (i, bid), var in data['choices'].items():
            value = _integer(var.X)
            if value not in (0,1):
                raise ValueError('Route choices must be zero or one')
            if value:
                group = tuple(data['rows'][i]['keys'])
                if seen.intersection(group):
                    raise ValueError('Duplicate selected tasks')
                seen.update(group)
                groups.append(group)
        expected = {(o.id,k) for o in problem.orders if o.satellite == site for k in ('d','p')}
        if seen != expected:
            raise ValueError('Selected groups do not exactly cover original tasks')
        e1 = context['e1']
        variables = e1.get('supplies', e1.get('deliveries'))
        supplies = {b:_integer(variables[site,b].X)
                    for b in sorted({o.batch for o in problem.orders if o.satellite == site})}
        total = sum(o.count for o in problem.orders if o.satellite == site)
        minimum, maximum = {}, {}
        for batch in problem.operating_policy.batches:
            value = _integer(math.fsum(o.count*e1['allocation'][o.id,b.id].X
                for o in problem.orders if o.satellite == site
                for b in problem.operating_policy.batches
                if problem.batch_index[b.id] <= problem.batch_index[batch.id]))
            if not 0 <= value <= total:
                raise ValueError('Invalid original return quantity')
            if value: minimum[batch.id] = value
            if value < total: maximum[batch.id] = value
        selected[site] = _request(problem, site, groups, supplies, minimum, maximum)
    return selected


def _query(problem, request, cache, *, deadline, oracle_time_limit, seed, events):
    found = cache.load(request)
    if found is not None:
        events.append(dict(kind='cache_hit', request=request))
        return found
    remaining = min(oracle_time_limit, deadline-time.perf_counter())
    if remaining <= .01:
        return None
    cache.calls += 1
    # An unaccepted attempt never overwrites a previously frozen MPS.
    proof = cache.directory/(_digest(request)+'-'+uuid.uuid4().hex+'.mps.gz')
    begun = time.perf_counter()
    cert = physical_site_certificate(problem, request['satellite'],
        time_limit=remaining*.6, seed=seed, diagnostics_path=proof,
        **{k:v for k,v in request.items() if k != 'satellite'})
    event = dict(kind='oracle', request=request, status=cert['status'],
                 bound=cert['bound'], oracle_runtime=cert['runtime'], accepted=False)
    events.append(event)
    # UNKNOWN may have a bound, but this initial implementation only persists
    # complete independently replayed proofs. It never converts UNKNOWN to INF.
    if not (cert['subproblem_optimal'] or cert['subproblem_infeasible']):
        return None
    available = min(begun+remaining, deadline)-time.perf_counter()
    if available <= .005 or not proof.exists():
        return None
    import gurobipy as gp
    with gp.Env(empty=True) as env:
        env.setParam('OutputFlag',0); env.start()
        replay_model = gp.read(str(proof),env=env)
        try:
            replay_model.Params.Threads = 1
            replay_model.Params.Seed = seed+1
            replay_model.Params.DualReductions = 0
            replay_model.Params.IntFeasTol = replay_model.Params.FeasibilityTol = 1e-9
            replay_model.Params.TimeLimit = max(0,min(begun+remaining,deadline)-time.perf_counter())
            replay_model.optimize()
            replay = dict(status=int(replay_model.Status), runtime=replay_model.Runtime,
                          supported_bound=0.0)
            if cert['subproblem_infeasible']:
                accepted = replay_model.Status == gp.GRB.INFEASIBLE
            else:
                raw = float(replay_model.ObjBound)
                supported = max(0,raw-1e-7*max(1,abs(raw))) if math.isfinite(raw) and abs(raw)<1e90 else 0
                replay.update(raw_bound=raw if math.isfinite(raw) and abs(raw)<1e90 else None,
                              supported_bound=supported)
                accepted = replay_model.Status == gp.GRB.OPTIMAL and math.isfinite(cert['bound']) and 0 <= cert['bound'] < 1e90
                cert['bound'] = min(cert['bound'],supported)
        finally:
            replay_model.dispose()
    event.update(replay=replay, accepted=accepted, charged_seconds=time.perf_counter()-begun)
    if not accepted:
        return None
    return cache.save(request,cert,replay,proof)


def _atoms(request):
    return [('trip_groups',g) for g in request['trip_groups'] or ()]+[
        (kind,bid) for kind in ('supply_thresholds','minimum_returns','maximum_returns')
        for bid in sorted(request[kind])]


def _without(problem, request, atom):
    result = deepcopy(request)
    kind, key = atom
    if kind == 'trip_groups':
        result[kind] = [g for g in result[kind] if _encoded(g) != _encoded(key)]
    else:
        del result[kind][key]
    return _request(problem,result['satellite'],result['trip_groups'],
                    result['supply_thresholds'],result['minimum_returns'],result['maximum_returns'])


def shrink_conflict(problem, record, query, *, max_checks=8):
    """Greedy verified deletions; failed/unknown deletions retain the last proof."""
    if not record['certificate']['subproblem_infeasible']:
        raise ValueError('Conflict reduction requires a proved infeasible condition')
    checks, kept = 0, record
    for atom in _atoms(record['request']):
        if checks >= max_checks:
            break
        trial = _without(problem,kept['request'],atom)
        candidate = query(trial)
        checks += 1
        if candidate is not None and candidate['certificate']['subproblem_infeasible']:
            kept = candidate
    return kept, dict(checks=checks, original_atoms=len(_atoms(record['request'])),
                      retained_atoms=len(_atoms(kept['request'])), minimality_proven=False)


def logic_benders_certificate(problem, *, time_limit=60, initial_result=None, seed=42,
        max_rounds=8, oracle_time_limit=10, master_time_limit=20, reduction_checks=8,
        enumeration_max_states=2000000, timing_envelopes=True, recovery_time_limit=0,
        diagnostics_dir=None, cache_dir=None):
    """Opt-in complete-domain lower certificate and audited upper, one budget.

    max_rounds=0 is the identical integer master without physical separation.
    Cache records restore accepted cuts, not a previously optimal master solve.
    All enumeration, model build, oracle/replay/reduction and repair time is
    charged. I/O/solver construction may finish beyond a deadline; actual wall
    time and overruns are reported, never hidden as a faster benchmark.
    """
    if (problem.model_number != 5 or problem.free_assignment or not problem.reusable
            or len(problem.operating_policy.batches) <= 1 or not problem.orders):
        raise ValueError('Nonempty fixed reusable multibatch M5 required')
    for name,value in [('time_limit',time_limit),('oracle_time_limit',oracle_time_limit),
                       ('master_time_limit',master_time_limit)]:
        if isinstance(value,bool) or not math.isfinite(value) or value <= 0:
            raise ValueError(name+' must be finite and positive')
    if isinstance(recovery_time_limit,bool) or not math.isfinite(recovery_time_limit) or recovery_time_limit < 0:
        raise ValueError('recovery_time_limit must be finite and nonnegative')
    for name,value,minimum in [('max_rounds',max_rounds,0),('reduction_checks',reduction_checks,0),
                                ('enumeration_max_states',enumeration_max_states,1),('seed',seed,0)]:
        if not isinstance(value,int) or isinstance(value,bool) or value < minimum:
            raise ValueError(name+' must be an integer >= '+str(minimum))
    if not isinstance(timing_envelopes,bool):
        raise ValueError('timing_envelopes must be boolean')
    begun = time.perf_counter(); deadline = begun+time_limit
    initial = deepcopy(initial_result)
    audited = audit_solution(problem,initial) if initial is not None else None
    if audited is not None and not audited['valid']:
        raise ValueError('Initial result must pass the full M5 audit')
    identity = dict(business=business_fingerprint(problem),source=source_fingerprint())
    out = Path(diagnostics_dir) if diagnostics_dir is not None else None
    if out is not None: out.mkdir(parents=True,exist_ok=True)
    payload = dict(scope='M5_complete_integer_joint_master_verified_physical_separation',
        identity=identity,bound=0.0,upper_bound=audited['objective'] if audited else None,
        result=initial,gap=None,proven_optimal=False,proven_infeasible=False,
        status='TIME_LIMIT_BUILD',complete_domain=False,rounds=[],cuts=[],events=[],
        parameters=dict(time_limit=time_limit,seed=seed,max_rounds=max_rounds,
            oracle_time_limit=oracle_time_limit,master_time_limit=master_time_limit,
            reduction_checks=reduction_checks,enumeration_max_states=enumeration_max_states,
            timing_envelopes=timing_envelopes,service_envelopes=False,
            recovery_time_limit=recovery_time_limit))
    def save(status=None):
        if status is not None: payload['status'] = status
        payload['runtime'] = time.perf_counter()-begun
        if out is not None: _write(out/'checkpoint.json',payload)
    with ExitStack() as stack:
        if out is not None:
            _lock(stack,out/'.logic_benders.lock')
            previous = out/'checkpoint.json'
            if previous.exists() and json.loads(previous.read_bytes())['identity'] != identity:
                raise ValueError('Diagnostic checkpoint belongs to another business/source version')
        if cache_dir is None and out is None:
            cache_dir = stack.enter_context(tempfile.TemporaryDirectory(prefix='m5-lbbd-'))
        cache = _CertificateCache(cache_dir or out/'oracle_cache',identity)
        _lock(stack,cache.directory/'.cache.lock')
        run = out/('run_'+uuid.uuid4().hex) if out is not None else None
        if run is not None:
            run.mkdir();payload['run_directory'] = str(run)
        model = None
        cuts, added = [], set()
        try:
            save('ENUMERATING')
            from .multibatch_route_pricing import route_context
            from .multibatch_route_timing import enumerate_timed_route_universe,write_compressed_model
            from .multibatch_route_enumeration import enumerate_route_universe
            enumerate_routes = enumerate_timed_route_universe if timing_envelopes else enumerate_route_universe
            domains = {}
            enumeration_deadline = min(deadline,begun+time_limit*.35)
            for site in sorted({o.satellite for o in problem.orders}):
                if time.perf_counter() >= enumeration_deadline: raise TimeoutError('Enumeration budget exhausted')
                context = route_context(problem,site)
                universe = enumerate_routes(problem,context,deadline=enumeration_deadline,max_states=enumeration_max_states)
                if not universe['complete']:
                    save('INCOMPLETE_DOMAIN_NO_NEW_BOUND')
                    break
                domains[site] = context,universe
            if set(domains) != {o.satellite for o in problem.orders}:
                payload['status'] = 'INCOMPLETE_DOMAIN_NO_NEW_BOUND'
            else:
                payload['complete_domain'] = True
                model,context = build_joint_inventory(problem,domains,deadline=deadline)
                import gurobipy as gp
                for var in context['choices']: var.VType = gp.GRB.BINARY
                lift_physical_costs(model,context)
                model.Params.OutputFlag = 0;model.Params.Threads = 1;model.Params.Seed = seed
                model.Params.DualReductions = 0
                model.Params.IntFeasTol = model.Params.FeasibilityTol = 1e-9
                model.update()
                payload['route_choices'] = len(context['choices'])
                def install(record, resumed=False):
                    key = _digest(record['request'])
                    if key in added: return False
                    cert = record['certificate']
                    cut = add_physical_cut(problem,model,context,cert,name='lbbd_'+str(len(cuts)))
                    cuts.append(cut);added.add(key)
                    payload['cuts'].append(dict(request=record['request'],certificate=cert,
                                               cache_record=str(cache.path(record['request'])),resumed=resumed))
                    if initial is not None:
                        set_joint_projection(problem,context,initial)
                        projected = project_physical_costs(problem,context,initial,cuts=cuts)
                        active = cut['active'] if isinstance(cut['active'],int) else _integer(cut['active'].Start)
                        if active and (cut['infeasible'] or cert['bound'] > projected['actual_site_costs'][cert['satellite']]+1e-6):
                            raise RuntimeError('New proof contradicts the audited full initial projection')
                    return True
                if max_rounds:
                    for record in cache.records():
                        if time.perf_counter() >= deadline: break
                        install(record,resumed=True)
                if initial is not None:
                    set_joint_projection(problem,context,initial)
                    project_physical_costs(problem,context,initial,cuts=cuts)
                for round_index in range(max_rounds+1):
                    remaining = deadline-time.perf_counter()
                    if remaining <= .01: break
                    save('SOLVING_MASTER')
                    model.Params.TimeLimit = min(master_time_limit,remaining if round_index==max_rounds else remaining*.4)
                    model.optimize()
                    raw = float(model.ObjBound)
                    if model.Status != gp.GRB.INFEASIBLE and math.isfinite(raw) and abs(raw) < 1e90:
                        safe = max(0,raw-1e-7*max(1,abs(raw)))
                        payload['bound'] = max(payload['bound'],safe)
                    elif model.Status == gp.GRB.INFEASIBLE:
                        if initial is not None: raise RuntimeError('Master excluded an audited complete initial solution')
                        payload['status'] = 'RELAXATION_INFEASIBLE_FULL_FLAG_UNCHANGED'
                        break
                    if payload['upper_bound'] is not None and payload['bound'] > payload['upper_bound']+1e-6:
                        raise RuntimeError('Master certificate exceeds audited upper')
                    row = dict(index=round_index,status=int(model.Status),raw_bound=raw if math.isfinite(raw) and abs(raw)<1e90 else None,
                               bound=payload['bound'],solver_seconds=model.Runtime,added_cuts=0)
                    payload['rounds'].append(row)
                    if run is not None:
                        path = run/('master_'+str(round_index)+'.mps.gz')
                        write_compressed_model(model,path);row['master_mps'] = str(path)
                    if not model.SolCount or round_index == max_rounds: break
                    requests = selected_conditions(problem,context)
                    theta_values = {s:v.X for s,v in context['physical_costs'].items()}
                    save('SEPARATING')
                    for site,request in requests.items():
                        if time.perf_counter() >= deadline: break
                        def query(value):
                            return _query(problem,value,cache,deadline=deadline,
                                oracle_time_limit=oracle_time_limit,seed=seed,events=payload['events'])
                        record = query(request)
                        if record is None: continue
                        if record['certificate']['subproblem_infeasible'] and reduction_checks:
                            record,reduction = shrink_conflict(problem,record,query,max_checks=reduction_checks)
                            payload['events'].append(dict(kind='reduction',**reduction))
                        cert = record['certificate']
                        if cert['subproblem_infeasible'] or cert['bound'] > theta_values[site]+1e-6:
                            row['added_cuts'] += int(install(record))
                    if not row['added_cuts']:
                        if recovery_time_limit > 0 and deadline-time.perf_counter() > .01:
                            from .multibatch_group_schedule import conditional_group_schedule
                            repair = conditional_group_schedule(problem,{s:q['trip_groups'] for s,q in requests.items()},
                                time_limit=min(recovery_time_limit,deadline-time.perf_counter()),seed=seed)
                            payload['events'].append(dict(kind='complete_group_repair',certificate=repair['certificate']))
                            if repair['result'] is not None:
                                checked = audit_solution(problem,repair['result'])
                                if not checked['valid']: raise RuntimeError('Full repair failed independent audit')
                                if payload['upper_bound'] is None or checked['objective'] < payload['upper_bound']:
                                    initial = deepcopy(repair['result']);payload.update(result=initial,upper_bound=checked['objective'])
                        payload['status'] = 'NO_NEW_VERIFIED_CUT'
                        break
                else:
                    payload['status'] = 'ROUND_LIMIT'
                if payload['status'] in ('SOLVING_MASTER','SEPARATING'):
                    payload['status'] = 'BUDGET_OR_ROUND_LIMIT'
        except TimeoutError:
            payload['status'] = 'TIME_LIMIT_BUILD_OR_ENUMERATION'
        except Exception:
            save('FAILED_NO_PROMOTION')
            raise
        finally:
            if model is not None: model.dispose()
            payload['cache'] = dict(hits=cache.hits,rejected=cache.rejected,oracle_calls=cache.calls,directory=str(cache.directory))
        if source_fingerprint() != identity['source'] or business_fingerprint(problem) != identity['business']:
            save('INPUT_OR_SOURCE_CHANGED_NO_PROMOTION')
            raise RuntimeError('Business/source changed during M5 certificate run')
        if payload['upper_bound'] is not None:
            payload['gap'] = max(0,payload['upper_bound']-payload['bound'])/max(1e-9,abs(payload['upper_bound']))
        payload['runtime'] = time.perf_counter()-begun
        payload['budget_overrun_seconds'] = max(0,payload['runtime']-time_limit)
        save()
        if run is not None: _write(run/'result.json',payload)
    return payload
