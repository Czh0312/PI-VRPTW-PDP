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
import gzip
import hashlib
import json
import math
from pathlib import Path
import tempfile
import shutil
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


def _compress(source, destination):
    with Path(source).open('rb') as incoming, gzip.open(destination,'wb',compresslevel=1) as outgoing:
        shutil.copyfileobj(incoming,outgoing,length=1024*1024)


def _write_model(model, destination):
    # Gurobi 12.0.3 native compressed I/O traps on long Darwin paths. Keep
    # native paths short; Python gzip preserves the same unmodified MPS bytes.
    with tempfile.TemporaryDirectory(prefix='m5-mps-') as directory:
        raw = Path(directory)/'model.mps'
        model.write(str(raw))
        _compress(raw,destination)


def _read_model(gp, path, env):
    with tempfile.TemporaryDirectory(prefix='m5-replay-') as directory:
        raw = Path(directory)/'model.mps'
        with gzip.open(path,'rb') as incoming, raw.open('wb') as outgoing:
            shutil.copyfileobj(incoming,outgoing,length=1024*1024)
        return gp.read(str(raw),env=env)


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
        # Run-local hints never become a cached certificate without replay.
        self.pending = {}
        self.feasible = set()
        self.attempts = {}

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
    if not math.isfinite(value) or abs(value) >= 1e90 or abs(value-round(value)) > 1e-6:
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


def _feasibility_certificate(problem, request, *, time_limit, seed, diagnostics_path):
    """Same physical model and objective, stop on the first witness.

    Only infeasibility can produce a certificate here. A feasible witness is
    used solely to stop an unnecessary conflict-deletion proof attempt.
    """
    import gurobipy as gp
    from .multibatch_physical_site import build_physical_site
    begun = time.perf_counter(); model = None
    cert = dict(**request, bound=0.0, raw_bound=None, margin=0.0,
        status='TIME_LIMIT_BUILD', scope='M5_conditional_physical_E2_site_cost_relaxation',
        proven_optimal=False, proven_infeasible=False, subproblem_optimal=False,
        subproblem_infeasible=False, subproblem_objective=None, feasible_witness=False,
        oracle_mode='feasibility_only')
    try:
        model,context = build_physical_site(problem,request['satellite'],
            **{k:v for k,v in request.items() if k != 'satellite'},deadline=begun+time_limit)
        # Keep the original cost to guide the search, without spending time
        # proving a feasible deletion's cost optimum.
        model.Params.Threads = 1; model.Params.Seed = seed
        model.Params.DualReductions = 0; model.Params.SolutionLimit = 1
        model.Params.IntFeasTol = model.Params.FeasibilityTol = 1e-9
        model.Params.TimeLimit = max(0,begun+time_limit-time.perf_counter())
        model.optimize()
        cert.update(status=int(model.Status),subproblem_infeasible=model.Status == gp.GRB.INFEASIBLE,
                    feasible_witness=bool(model.SolCount),projection_proof=context['projection_proof'])
        model.write(str(diagnostics_path))
    except TimeoutError:
        pass
    finally:
        if model is not None: model.dispose()
    cert['runtime'] = time.perf_counter()-begun
    return cert


def _query(problem, request, cache, *, deadline, oracle_time_limit, seed, events,
           feasibility_only=False):
    found = cache.load(request)
    if found is not None:
        events.append(dict(kind='cache_hit', request=request))
        return found
    key = _digest(request)
    if feasibility_only and key in cache.feasible:
        events.append(dict(kind='feasible_hint_hit',request=request))
        return None
    remaining = min(oracle_time_limit, deadline-time.perf_counter())
    if remaining <= .01:
        return None
    cache.calls += 1
    # An unaccepted attempt never overwrites a previously frozen MPS.
    proof = cache.directory/(key+'-'+uuid.uuid4().hex+'.mps.gz')
    begun = time.perf_counter()
    attempt = cache.attempts.get(key,0)
    cache.attempts[key] = attempt+1
    query_seed = (seed+11*attempt) % 2000000000 if feasibility_only else seed
    pending = cache.pending.get(key) if feasibility_only else None
    if pending is not None and _file_hash(pending[1]) != pending[2]:
        cache.pending.pop(key,None)
        pending = None
    if pending is not None:
        cert,proof = deepcopy(pending[0]),pending[1]
    else:
        with tempfile.TemporaryDirectory(prefix='m5-oracle-') as directory:
            raw = Path(directory)/'oracle.mps'
            if feasibility_only:
                cert = _feasibility_certificate(problem,request,
                    time_limit=remaining*.6,seed=query_seed,diagnostics_path=raw)
            else:
                cert = physical_site_certificate(problem, request['satellite'],
                    time_limit=remaining*.6, seed=query_seed, diagnostics_path=raw,
                    **{k:v for k,v in request.items() if k != 'satellite'})
            if raw.exists(): _compress(raw,proof)
    oracle_seconds = time.perf_counter()-begun
    event = dict(kind='oracle', request=request, status=cert['status'],
        bound=cert['bound'],oracle_runtime=0.0 if pending else cert['runtime'],accepted=False,
        oracle_wall_seconds=oracle_seconds,replay_wall_seconds=0.0,
        charged_seconds=oracle_seconds,feasibility_only=feasibility_only,
        reused_pending_proof=pending is not None,attempt=attempt+1,seed=query_seed,
        feasible_witness=cert.get('feasible_witness',False))
    events.append(event)
    if feasibility_only and cert.get('feasible_witness'):
        cache.feasible.add(key)
    # UNKNOWN may have a bound, but this initial implementation only persists
    # complete independently replayed proofs. It never converts UNKNOWN to INF.
    if not (cert['subproblem_optimal'] or cert['subproblem_infeasible']):
        return None
    if feasibility_only and proof.exists():
        cache.pending[key] = deepcopy(cert),proof,_file_hash(proof)
    available = min(begun+remaining, deadline)-time.perf_counter()
    if available <= .005 or not proof.exists():
        return None
    import gurobipy as gp
    with gp.Env(empty=True) as env:
        env.setParam('OutputFlag',0); env.start()
        replay_model = _read_model(gp,proof,env)
        try:
            replay_model.Params.Threads = 1
            replay_model.Params.Seed = (query_seed+1) % 2000000000
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
    event.update(replay=replay, accepted=accepted,charged_seconds=time.perf_counter()-begun,
                 replay_wall_seconds=time.perf_counter()-begun-oracle_seconds)
    if not accepted:
        return None
    record = cache.save(request,cert,replay,proof)
    cache.pending.pop(key,None)
    event['charged_seconds'] = time.perf_counter()-begun
    return record


def _atoms(request):
    # Generalize supply/stock conditions before spending checks on group size.
    return [
        (kind,bid) for kind in ('supply_thresholds','minimum_returns','maximum_returns')
        for bid in sorted(request[kind])]+[('trip_groups',g) for g in request['trip_groups'] or ()]


def _without(problem, request, atom):
    result = deepcopy(request)
    kind, key = atom
    if kind == 'trip_groups':
        result[kind] = [g for g in result[kind] if _encoded(g) != _encoded(key)]
    else:
        del result[kind][key]
    return _request(problem,result['satellite'],result['trip_groups'],
                    result['supply_thresholds'],result['minimum_returns'],result['maximum_returns'])


def _literals(request):
    """Exact condition identities: numeric thresholds include their values."""
    result = {_encoded(('trip_groups',g)):('trip_groups',g) for g in request['trip_groups'] or ()}
    for kind in ('supply_thresholds','minimum_returns','maximum_returns'):
        result.update({_encoded((kind,bid,value)):(kind,bid)
                       for bid,value in request[kind].items()})
    return result


def _group_coupling(group):
    tasks = set(tuple(task) for task in group)
    return sum((oid,'d') not in tasks or (oid,'p') not in tasks for oid in {oid for oid,_ in tasks})


class _ConflictGuide:
    """Proven cores guide new queries; an intersection is never a proof."""
    def __init__(self):
        self.cores = {}

    def remember(self, record):
        if record['certificate']['subproblem_infeasible']:
            q = record['request']
            self.cores.setdefault(q['satellite'],{})[_digest(q)] = frozenset(_literals(q))

    def proposals(self, problem, record):
        q = record['request']; literals = _literals(q); current = frozenset(literals)
        candidates = set()
        for old in self.cores.get(q['satellite'],{}).values():
            common = current & old
            if common and common < current: candidates.add(common)
        for common in sorted(candidates,key=lambda c:(len(c),tuple(sorted(c)))):
            trial = q
            for key,atom in literals.items():
                if key not in common: trial = _without(problem,trial,atom)
            yield trial

    def deletion_order(self, request):
        old = self.cores.get(request['satellite'],{}).values()
        frequencies = {key:sum(key in core for core in old) for key in _literals(request)}
        # Delete infrequent conditions first, preserving likely explanations.
        literal_for = {_encoded(atom):key for key,atom in _literals(request).items()}
        return sorted(_atoms(request),key=lambda atom:(
            atom[0]=='trip_groups',frequencies[literal_for[_encoded(atom)]],
            len(atom[1])+_group_coupling(atom[1]) if atom[0]=='trip_groups' else 0,
            _group_coupling(atom[1]) if atom[0]=='trip_groups' else 0,_encoded(atom)))


def shrink_conflict(problem, record, query, *, max_checks=8, guide=None, retry_query=None,
                    retry_priority=None):
    """Greedy verified deletions; failed/unknown deletions retain the last proof."""
    if not record['certificate']['subproblem_infeasible']:
        raise ValueError('Conflict reduction requires a proved infeasible condition')
    checks, kept, proposals, retries = 0, record, 0, 0
    unknown = []
    if guide is not None:
        for trial in guide.proposals(problem,record):
            if checks >= max_checks:break
            candidate = query(trial);checks += 1;proposals += 1
            if candidate is None and retry_query is not None and checks < max_checks:
                candidate = retry_query(trial);checks += 1;retries += 1
            if candidate is not None and candidate['certificate']['subproblem_infeasible']:
                kept = candidate
                break
    order = guide.deletion_order(kept['request']) if guide is not None else _atoms(kept['request'])
    for atom in order:
        if checks >= max_checks:
            break
        trial = _without(problem,kept['request'],atom)
        if guide is not None and not _atoms(trial):
            # Minimality is not claimed. Reserve time for other conditional
            # conflicts instead of attempting unconditional site infeasibility.
            continue
        candidate = query(trial)
        checks += 1
        if candidate is not None and candidate['certificate']['subproblem_infeasible']:
            kept = candidate
        elif candidate is None:
            unknown.append(trial)
    # A completed but not yet replayed proof, or an unknown deletion, may
    # deserve one larger retry. Never extend the current verified condition.
    if retry_query is not None:
        current = frozenset(_literals(kept['request']))
        useful = [q for q in unknown if frozenset(_literals(q)) < current]
        for trial in sorted(useful,key=lambda q:(
                retry_priority(q) if retry_priority else 0,len(_literals(q)),_digest(q)))[:1]:
            if checks >= max_checks:break
            candidate = retry_query(trial);checks += 1;retries += 1
            if candidate is not None and candidate['certificate']['subproblem_infeasible']:
                kept = candidate
                break
    if guide is not None:guide.remember(kept)
    return kept, dict(checks=checks, original_atoms=len(_atoms(record['request'])),
        retained_atoms=len(_atoms(kept['request'])),minimality_proven=False,
        intersection_queries=proposals,retry_queries=retries)


def logic_benders_certificate(problem, *, time_limit=60, initial_result=None, seed=42,
        max_rounds=8, oracle_time_limit=10, master_time_limit=20, reduction_checks=8,
        enumeration_max_states=2000000, timing_envelopes=True, recovery_time_limit=0,
        group_first=True, target_gap=1e-6, conflict_learning=True, final_master_reserve=3,
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
    if not isinstance(group_first,bool):
        raise ValueError('group_first must be boolean')
    if not isinstance(conflict_learning,bool):
        raise ValueError('conflict_learning must be boolean')
    if (isinstance(final_master_reserve,bool) or not math.isfinite(final_master_reserve)
            or final_master_reserve < 0):
        raise ValueError('final_master_reserve must be finite and nonnegative')
    if isinstance(target_gap,bool) or not math.isfinite(target_gap) or target_gap < 0:
        raise ValueError('target_gap must be finite and nonnegative')
    begun = time.perf_counter(); deadline = begun+time_limit
    reserved = min(final_master_reserve,time_limit*.1,master_time_limit)
    initial = deepcopy(initial_result)
    audited = audit_solution(problem,initial) if initial is not None else None
    if audited is not None and not audited['valid']:
        raise ValueError('Initial result must pass the full M5 audit')
    import gurobipy as gp
    identity = dict(business=business_fingerprint(problem),source=source_fingerprint(),
                    gurobi_version=list(gp.gurobi.version()))
    out = Path(diagnostics_dir) if diagnostics_dir is not None else None
    if out is not None: out.mkdir(parents=True,exist_ok=True)
    payload = dict(scope='M5_complete_integer_joint_master_verified_physical_separation',
        identity=identity,bound=0.0,upper_bound=audited['objective'] if audited else None,
        result=initial,gap=None,proven_optimal=False,proven_infeasible=False,
        status='TIME_LIMIT_BUILD',complete_domain=False,target_reached=False,rounds=[],cuts=[],events=[],
        parameters=dict(time_limit=time_limit,seed=seed,max_rounds=max_rounds,
            oracle_time_limit=oracle_time_limit,master_time_limit=master_time_limit,
            reduction_checks=reduction_checks,enumeration_max_states=enumeration_max_states,
            timing_envelopes=timing_envelopes,service_envelopes=False,
            recovery_time_limit=recovery_time_limit,group_first=group_first,target_gap=target_gap,
            conflict_learning=conflict_learning,final_master_reserve=final_master_reserve))
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
        guide = _ConflictGuide() if conflict_learning else None
        try:
            save('ENUMERATING')
            from .multibatch_route_pricing import route_context
            from .multibatch_route_timing import enumerate_timed_route_universe
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
                if conflict_learning:
                    # A loose default MIP gap can otherwise trigger physical
                    # separation while this very master still has easy proof work.
                    model.Params.MIPGap = min(model.Params.MIPGap,target_gap*.25)
                    model.Params.MIPGapAbs = 0
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
                        model.update()  # Flush lazy Start assignments before reading trigger flags.
                        active = cut['active'] if isinstance(cut['active'],int) else _integer(cut['active'].Start)
                        if active and (cut['infeasible'] or cert['bound'] > projected['actual_site_costs'][cert['satellite']]+1e-6):
                            raise RuntimeError('New proof contradicts the audited full initial projection')
                    return True
                if max_rounds:
                    for record in cache.records():
                        if time.perf_counter() >= deadline: break
                        install(record,resumed=True)
                        if guide is not None:guide.remember(record)
                if initial is not None:
                    set_joint_projection(problem,context,initial)
                    project_physical_costs(problem,context,initial,cuts=cuts)
                repairs_done = set()
                def near_target():
                    upper = payload['upper_bound']
                    return upper is not None and max(0,upper-payload['bound'])/max(1e-9,abs(upper)) <= target_gap
                def repair_once(requests):
                    nonlocal initial
                    groups = {s:q['trip_groups'] for s,q in requests.items()}
                    key = _digest(groups)
                    available = min(recovery_time_limit,deadline-time.perf_counter())
                    if key in repairs_done or available <= .01:return
                    repairs_done.add(key)
                    from .multibatch_group_schedule import conditional_group_schedule
                    repair = conditional_group_schedule(problem,groups,time_limit=available,seed=seed)
                    payload['events'].append(dict(kind='complete_group_repair',certificate=repair['certificate']))
                    if repair['result'] is not None:
                        checked = audit_solution(problem,repair['result'])
                        if not checked['valid']:raise RuntimeError('Full repair failed independent audit')
                        if payload['upper_bound'] is None or checked['objective'] < payload['upper_bound']:
                            if payload['bound'] > checked['objective']+1e-6:
                                raise RuntimeError('Full repair contradicts certified lower')
                            initial = deepcopy(repair['result'])
                            payload.update(result=initial,upper_bound=checked['objective'])
                for round_index in range(max_rounds+1):
                    remaining = deadline-time.perf_counter()
                    if remaining <= .01: break
                    save('SOLVING_MASTER')
                    final_round = round_index==max_rounds or (reserved>0 and remaining<=reserved+.05)
                    model.Params.TimeLimit = min(master_time_limit,remaining if final_round else remaining*.4)
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
                        _write_model(model,path);row['master_mps'] = str(path)
                    if not model.SolCount or final_round: break
                    if near_target():
                        payload['status'] = 'AUDITED_GAP_TARGET_REACHED'
                        break
                    requests = selected_conditions(problem,context)
                    requests = dict(sorted(requests.items(),key=lambda item:(len(item[1]['trip_groups']),item[0])))
                    # Try a complete witness before many local oracle calls:
                    # on an already tight master the remaining gap may be UB.
                    repair_once(requests)
                    if near_target():
                        payload['status'] = 'AUDITED_GAP_TARGET_REACHED'
                        break
                    theta_values = {s:v.X for s,v in context['physical_costs'].items()}
                    # Leave a final master solve after a costly oracle/core search.
                    remaining = max(0,deadline-time.perf_counter())
                    separation_deadline = deadline-min(master_time_limit,max(reserved,remaining*.2))
                    save('SEPARATING')
                    for site,request in requests.items():
                        if time.perf_counter() >= separation_deadline: break
                        def query(value, feasibility=False, retry=False):
                            return _query(problem,value,cache,deadline=separation_deadline,
                                oracle_time_limit=oracle_time_limit*(1.8 if retry else 1),
                                seed=seed,events=payload['events'],feasibility_only=feasibility)
                        record = None
                        if group_first:
                            plain = _request(problem,site,request['trip_groups'])
                            record = query(plain)
                            if record is not None and not record['certificate']['subproblem_infeasible']:
                                if record['certificate']['bound'] > theta_values[site]+1e-6:
                                    row['added_cuts'] += int(install(record))
                                record = None
                        if record is None:
                            record = query(request)
                        if record is None: continue
                        if record['certificate']['subproblem_infeasible'] and reduction_checks:
                            def reduction_query(value, retry=False):
                                answer = query(value,feasibility=conflict_learning,retry=retry)
                                if conflict_learning and answer is None and _digest(value) in cache.feasible:
                                    # A rejection hint only: never installed or cached as a proof.
                                    return dict(request=value,certificate=dict(subproblem_infeasible=False),
                                                feasible_hint_only=True)
                                return answer
                            record,reduction = shrink_conflict(problem,record,
                                reduction_query,
                                max_checks=reduction_checks,guide=guide,
                                retry_query=(lambda value:reduction_query(value,retry=True)) if conflict_learning else None,
                                retry_priority=lambda value:_digest(value) not in cache.pending)
                            payload['events'].append(dict(kind='reduction',**reduction))
                        cert = record['certificate']
                        if cert['subproblem_infeasible'] or cert['bound'] > theta_values[site]+1e-6:
                            row['added_cuts'] += int(install(record))
                    if not row['added_cuts']:
                        repair_once(requests)
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
            if payload['bound'] > payload['upper_bound']+1e-6:
                save('BOUND_EXCEEDS_AUDITED_UPPER_NO_PROMOTION')
                raise RuntimeError('Final certificate exceeds the audited complete upper')
            payload['gap'] = max(0,payload['upper_bound']-payload['bound'])/max(1e-9,abs(payload['upper_bound']))
            payload['target_reached'] = payload['gap'] <= target_gap
        payload['runtime'] = time.perf_counter()-begun
        payload['budget_overrun_seconds'] = max(0,payload['runtime']-time_limit)
        save()
        if run is not None: _write(run/'result.json',payload)
    return payload
