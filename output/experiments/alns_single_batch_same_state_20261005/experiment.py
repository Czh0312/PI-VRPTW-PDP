"""Preregistered serial fork diagnostic; no production edits or adoption."""
from pathlib import Path
from collections import Counter
from datetime import datetime
from zoneinfo import ZoneInfo
import gzip
import hashlib
import json
import math
import os
import pickle
import shutil
import signal
import statistics
import subprocess
import sys
import time

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[2]
OLD = OUT.parent / 'alns_single_batch_diverse_descent_20261005'
MODULE = Path('models/routing/solvers/alns/single_batch.py')
DESCENT = MODULE.with_name('single_batch_descent.py')
FAMILIES = ['zhou_Set2_100-5-3_subset50_scenario02', 'zhou_Set2_100-5-3_scenario02',
            'zhou_Set2_100-10-2_scenario02']

def now():
    return datetime.now(ZoneInfo('Asia/Shanghai')).isoformat()

def write(path, obj):
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + '\n')

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def manifest(folder):
    return {str(p.relative_to(folder)): sha(p) for p in sorted(folder.rglob('*'))
            if p.is_file() and '__pycache__' not in p.parts}

def job(family, model, seed, iterations):
    return dict(family=family, model=model, seed=seed, iterations=iterations,
                size=50 if 'subset50' in family else 100,
                case=str(OUT / 'benchmark_inputs' / family / 'case.json'))

def freeze():
    # Freeze current common code, including other chats' changes, without editing it.
    target = OUT / 'baseline_source'
    target.mkdir()
    files = [p for base in ('models', 'configuration', 'config', 'tests')
             for p in sorted((ROOT / base).rglob('*'))
             if p.is_file() and p.suffix in ('.py', '.json', '.md') and '__pycache__' not in p.parts]
    hashes = {str(p.relative_to(ROOT)): sha(p) for p in files}
    for p in files:
        dest = target / p.relative_to(ROOT)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(p, dest)
    checks = {r: sha(ROOT / r) == h == sha(target / r) for r, h in hashes.items()}
    write(OUT / 'freeze_verification.json', dict(time=now(), checks=checks, passed=all(checks.values())))
    assert all(checks.values())
    write(OUT / 'baseline_source_manifest.json', hashes)
    shutil.copytree(OLD / 'benchmark_inputs', OUT / 'benchmark_inputs')
    for p in (OUT / 'benchmark_inputs').rglob('case.json'):
        text = p.read_text().replace(str(OLD / 'benchmark_inputs'), str(OUT / 'benchmark_inputs'))
        p.write_text(text)
    write(OUT / 'input_hashes.json', manifest(OUT / 'benchmark_inputs'))
    protocol = dict(registered=now(), scope='M3 same-memory-state fork diagnostic only; prior wall-budget rejection stands.',
        hypothesis='Separate prefix trajectory variation from final20% selection. Also observe whether infeasible severity can influence the incumbent trajectory.',
        design='One original prefix, fork at iteration80% before first descent call. Child completes first while parent waits; then parent continues. Alternate baseline/diversity order. Fork inherits current/best state, RNG, weights and all caches. Fixed iterations, huge nonbinding wall deadline; iteration-based cooling dominates. No parallel search branches.',
        budgets='Short10000 iterations:50/100 x seeds551,552,553; long30000:second100 x seeds551,552. Not equal evaluations, CPU, or wall budget; record these separately.',
        correctness='Freeze current context and closed inputs. Hook-disabled M1-M4 x two100 sources at500 iterations exactly reproduce baseline. Two100 M3 baseline/baseline forks at500 reproduce each other and public baseline. Targeted existing tests. Every final solution fresh uncached decode and full independent audit/replay.',
        decision='Diagnostic only; no production adoption or revival of rejected selector. If prefix identity or audit fails, invalidate affected diagnostic and preserve failure. Fixed-work improvement cannot replace original equal-wall gates. Do not tune rules or seeds after results.',
        observation='Record proposed feasibility, accepted infeasible moves and SA exponent from feasible current states, by operator. Additive nonnegative severity cannot make a numerically zero SA probability nonzero; observed all-feasible trajectories would lower priority of severity-only additive scoring.',
        timeout='Each worker process group outer timeout600s; preserve error and partial files, no retry. No global process listing available in sandbox; previous own checkpoint complete, record load averages.',
        limits='Previously used nested geometry, new seeds but not new independent instances. Fixed iterations and instrumentation differ from wall-clock production. No causal wall-speed, stable quality or true GAP claim.')
    write(OUT / 'protocol.json', protocol)
    verification = [job(f, m, 550, 500) for f in FAMILIES[1:] for m in (1,2,3,4)]
    controls = [job(f, 3, 550, 500) for f in FAMILIES[1:]]
    diagnostic = [job(f, 3, s, 10000) for f in FAMILIES[:2] for s in (551,552,553)]
    diagnostic += [job(FAMILIES[2], 3, s, 30000) for s in (551,552)]
    for phase, jobs in [('verification',verification), ('controls',controls), ('diagnostic',diagnostic)]:
        write(OUT / (phase + '_manifest.json'), jobs)
    write(OUT / 'registration_hashes.json', {p.name: sha(p) for p in OUT.glob('*.json')})
    # Only after registration, create an instrumented copy and introduce the frozen selector.
    source = OUT / 'instrumented_source'
    shutil.copytree(target, source)
    assert sha(target / DESCENT) == sha(OLD / 'before_source' / DESCENT)
    shutil.copy2(OLD / 'after_source' / DESCENT, source / DESCENT)
    text = (source / MODULE).read_text()
    text = text.replace('def _move(problem, original, rng, operator):',
        '_diagnostic_branch = None\n_diagnostic_probe = None\n\n\ndef _move(problem, original, rng, operator):', 1)
    marker = '        if descent_enabled and best_state is not None and not assignment_move and polishing:\n'
    assert text.count(marker) == 1
    text = text.replace(marker, '        if polishing and _diagnostic_branch is not None:\n'
                        '            _diagnostic_branch(locals())\n' + marker)
    text = text.replace('        seen_issues.update(problems)\n', '        seen_issues.update(problems)\n'
        '        if _diagnostic_probe is not None:\n            _diagnostic_probe("proposal", locals())\n', 1)
    text = text.replace('    result = best if best is not None else empty_result(problem, \'alns\')\n',
        '        if _diagnostic_probe is not None:\n            _diagnostic_probe("decision", locals())\n'
        '    result = best if best is not None else empty_result(problem, \'alns\')\n', 1)
    (source / MODULE).write_text(text)
    write(OUT / 'instrumented_source_manifest.json', manifest(source))
    write(OUT / 'execution_hashes.json', {'experiment.py': sha(Path(__file__)),
         'instrumented_source_manifest.json': sha(OUT / 'instrumented_source_manifest.json')})
    write(OUT / 'checkpoint.json', dict(status='frozen', active_process=None, time=now()))
    print('Frozen and registered', len(hashes), 'source files', flush=True)

def verify():
    for name in ('registration_hashes.json', 'execution_hashes.json'):
        for rel, digest in json.loads((OUT / name).read_text()).items():
            assert sha(OUT / rel) == digest, rel
    for source in ('baseline_source', 'instrumented_source'):
        for rel, digest in json.loads((OUT / (source + '_manifest.json')).read_text()).items():
            assert sha(OUT / source / rel) == digest, (source, rel)
    for rel, digest in json.loads((OUT / 'input_hashes.json').read_text()).items():
        assert sha(OUT / 'benchmark_inputs' / rel) == digest, rel

def core(result):
    keys = ('objective', 'iterations', 'operator_attempts', 'operator_improvements', 'operator_noops',
            'late_pickup_checks', 'late_pickup_vetoes', 'descent_candidates', 'descent_filtered')
    return dict(e1=result.get('e1_trips'), e2=result.get('e2_trips'), assignment=result.get('assignment'),
                info={k:result['info'].get(k) for k in keys})

def worker(phase, index, source):
    spec = json.loads((OUT / (phase + '_manifest.json')).read_text())[index]
    sys.path.insert(0, str(OUT / source))
    from configuration.loader import load_routing_case
    from models.routing.common.cycle import load_instance
    from models.routing.common.cycle.audit import audit_solution
    from models.routing.common.cycle.precheck import check_necessary_conditions
    from models.routing.service import solve_routing
    from models.routing.solvers.alns import single_batch
    assert Path(single_batch.__file__).resolve().is_relative_to(OUT / source)
    config, fleet = load_routing_case(spec['case'])
    for key in ('data','orders','routing_config','pool_config','dataset_metadata'):
        if isinstance(config.get(key), str):
            assert Path(config[key]).resolve().is_relative_to(OUT / 'benchmark_inputs')
    problem = load_instance({**config, 'model':spec['model']}, fleet)
    assert len({o.customer for o in problem.orders}) == spec['size']
    assert check_necessary_conditions(problem)['passed']
    dest = OUT / phase
    counts = Counter()
    per_operator = {}
    before = {}
    branch_info = {}
    child = False
    mode = source
    branch_cpu = branch_wall = None
    solve_cpu, solve_wall = time.process_time(), time.perf_counter()

    def probe(event, local):
        op = local['op']
        if event == 'proposal':
            before.clear()
            before.update(score=local['score'], feasible=not local['issues'])
            return
        stat = per_operator.setdefault(op, Counter())
        stat['proposals'] += 1
        feasible = not local['problems']
        label = 'feasible' if feasible else 'infeasible'
        stat[label] += 1
        counts['proposals'] += 1
        counts['current_feasible' if before['feasible'] else 'current_infeasible'] += 1
        counts['proposed_' + label] += 1
        if local['accept']:
            stat['accepted_' + label] += 1
            counts['accepted_' + label] += 1
        if before['feasible'] and not feasible:
            exponent = min(0, (before['score']-local['new_score'])/local['temperature'])
            counts['infeasible_from_feasible'] += 1
            counts['zero_sa_probability'] += int(math.exp(exponent) == 0)
        for code in set(local['problems']):
            stat['issue_' + code] += 1

    def branch(local):
        nonlocal child, mode, branch_cpu, branch_wall, branch_info
        # Clear the callback in the shared prefix: exactly one fork.
        single_batch._diagnostic_branch = None
        assert spec['model'] == 3 and local['iteration'] == int(spec['iterations']*.8)
        assert local['best_state'] is not None and local['descent'].enumerations == 0
        assert time.perf_counter()-local['begun'] < 1e9/spec['iterations']
        cached = local['cached']
        payload = {k: local[k] for k in ('current','score','decoded','issues','best','best_cost',
                     'best_state','best_decoded','weights','attempts','improved','noops','iteration','seen_issues')}
        payload.update(rng_state=local['rng'].getstate(), cache_entries=cached.entries,
                       e1_cache_entries=cached.evaluator.entries, cached_distances=cached.evaluator.distances,
                       counts=counts, operators=per_operator)
        blob = pickle.dumps(payload, protocol=4)
        digest = hashlib.sha256(blob).hexdigest()
        with gzip.open(dest / ('%03d_shared_state.pkl.gz' % index), 'wb') as handle:
            handle.write(blob)
        prefix_cpu = time.process_time()-solve_cpu
        prefix_wall = time.perf_counter()-solve_wall
        order = ['baseline','diversity'] if index % 2 == 0 else ['diversity','baseline']
        # Pure-Python single-thread worker. Parent waits, so branches never compete.
        sys.stdout.flush()
        pid = os.fork()
        if pid == 0:
            child, mode = True, order[0]
        else:
            _, status = os.waitpid(pid, 0)
            assert os.waitstatus_to_exitcode(status) == 0, ('child_failed', status)
            mode = order[1]
        assert hashlib.sha256(pickle.dumps(payload, protocol=4)).hexdigest() == digest
        branch_info = dict(state_sha256=digest, iteration=local['iteration'], objective=local['best_cost'],
            prefix_cpu=prefix_cpu, prefix_wall=prefix_wall, prefix_counts=dict(counts),
            prefix_operator_counts={k:dict(v) for k,v in per_operator.items()},
            prefix_cache_hits=cached.hits, prefix_cache_misses=cached.misses,
            execution_order=order, forked_child=child, full_memory_cache_inheritance=True)
        local['descent'].diversify = phase == 'diagnostic' and mode == 'diversity'
        branch_cpu, branch_wall = time.process_time(), time.perf_counter()

    if phase in ('controls','diagnostic'):
        single_batch._diagnostic_branch = branch
        single_batch._diagnostic_probe = probe
    result = solve_routing(model=spec['model'], method='alns', problem=problem,
        alns_time=1e9, alns_iter=spec['iterations'], seed=spec['seed'])
    completed_cpu, completed_wall = time.process_time(), time.perf_counter()
    audit = audit_solution(problem, result)
    output = dict(job=spec, result=result, audit=audit, source=source, mode=mode,
        branch=branch_info, observations=dict(counts), operator_observations={k:dict(v) for k,v in per_operator.items()},
        branch_cpu=None if branch_cpu is None else completed_cpu-branch_cpu,
        branch_wall=None if branch_wall is None else completed_wall-branch_wall,
        total_process_cpu=(branch_info["prefix_cpu"]+completed_cpu-branch_cpu
                           if branch_cpu is not None else completed_cpu-solve_cpu), completed=now())
    write(dest / ('%03d_%s.json' % (index, mode)), output)
    print(phase, index, mode, result['info'].get('objective'), audit['valid'], flush=True)
    if child:
        os._exit(0)

def run(phase):
    verify()
    jobs = json.loads((OUT / (phase + '_manifest.json')).read_text())
    dest = OUT / phase
    dest.mkdir()
    lock = OUT / 'running.lock'
    fd = os.open(str(lock), os.O_EXCL|os.O_CREAT|os.O_WRONLY)
    os.write(fd, str(os.getpid()).encode()); os.close(fd)
    rows = []
    try:
        for index, spec in enumerate(jobs):
            sources = ['baseline_source','instrumented_source'] if phase == 'verification' else ['instrumented_source']
            if index % 2: sources.reverse()
            for source in sources:
                write(OUT / 'checkpoint.json', dict(status='running', phase=phase, index=index,
                      active_process=os.getpid(), completed_workers=len(rows), time=now()))
                row = dict(index=index, source=source, job=spec, started=now(), load_before=os.getloadavg())
                process = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), 'worker', phase,
                    str(index), source], cwd=OUT / source, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    start_new_session=True, env={**os.environ, 'PYTHONHASHSEED':'0','PYTHONDONTWRITEBYTECODE':'1'})
                try:
                    output, _ = process.communicate(timeout=600)
                    row['returncode'] = process.returncode
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    output, _ = process.communicate()
                    row.update(returncode=process.returncode, error='OUTER_TIMEOUT')
                (dest / ('%03d_%s.log' % (index,source))).write_bytes(output)
                row.update(finished=now(), load_after=os.getloadavg())
                rows.append(row)
                write(dest / 'workers.json', rows)
                print(output.decode(), end='', flush=True)
                if row['returncode'] != 0:
                    raise RuntimeError('Worker failed; preserve and investigate, no automatic retry')
        write(OUT / 'checkpoint.json', dict(status='phase_completed', phase=phase,
              active_process=None, workers=len(rows), time=now()))
    except Exception as exc:
        write(OUT / 'checkpoint.json', dict(status='failed', phase=phase, active_process=None,
              error=str(exc), workers=len(rows), time=now()))
        raise
    finally:
        lock.unlink()
    verify()

def fresh(phase):
    verify()
    sys.path.insert(0, str(OUT / 'baseline_source'))
    from configuration.loader import load_routing_case
    from models.routing.common.cycle import load_instance
    from models.routing.common.cycle.assignment import assigned_problem, lift_result
    from models.routing.common.cycle.audit import audit_solution
    from models.routing.solvers.alns.evaluator import evaluate
    from models.routing.solvers.alns.neighborhoods import state_from_result
    dest = OUT / (phase + '_fresh')
    dest.mkdir()
    rows = []
    for path in sorted((OUT / phase).glob('[0-9]*.json')):
        raw = json.loads(path.read_text()); spec = raw['job']
        config, fleet = load_routing_case(spec['case'])
        base = load_instance({**config,'model':spec['model']}, fleet)
        problem = assigned_problem(base,raw['result']['assignment']) if base.free_assignment else base
        _, result, issues = evaluate(problem, state_from_result(problem,raw['result']))
        fixed = audit_solution(problem,result)
        if base.free_assignment: result = lift_result(base,result,raw['result']['assignment'])
        audit = audit_solution(base,result)
        valid = not issues and fixed['valid'] and audit['valid'] and fixed['periodic_replay']['valid'] and audit['periodic_replay']['valid']
        equal = abs(result['info']['objective']-raw['result']['info']['objective']) <= 1e-8
        row = dict(name=path.name,valid=valid,equal=equal,issues=issues)
        with gzip.open(dest / (path.name+'.gz'), 'wt') as handle:
            json.dump(dict(check=row,result=result,fixed_audit=fixed,audit=audit),handle)
        rows.append(row)
    write(OUT / (phase+'_fresh_audit.json'),rows)
    assert all(r['valid'] and r['equal'] for r in rows)
    print(phase, 'fresh audits',len(rows),'passed')

def compare(phase):
    jobs = json.loads((OUT / (phase+'_manifest.json')).read_text())
    rows = []
    for index,spec in enumerate(jobs):
        modes = ['baseline_source','instrumented_source'] if phase == 'verification' else ['baseline','diversity']
        a,b = [json.loads((OUT / phase / ('%03d_%s.json'%(index,mode))).read_text()) for mode in modes]
        same = core(a['result']) == core(b['result'])
        valid = all(r['audit']['valid'] and r['audit']['periodic_replay']['valid']
                    and r['result']['info']['iterations']==spec['iterations'] for r in (a,b))
        row = dict(index=index,job=spec,valid=valid,exact_output=same,
            before=a['result']['info']['objective'],after=b['result']['info']['objective'])
        if phase != 'verification':
            row.update(same_prefix=a['branch']['state_sha256']==b['branch']['state_sha256'],
                prefix_objective=a['branch']['objective'],before_cpu=a['branch_cpu'],after_cpu=b['branch_cpu'],
                before_cache_misses=a['result']['info']['evaluation_cache_misses']-a['branch']['prefix_cache_misses'],
                after_cache_misses=b['result']['info']['evaluation_cache_misses']-b['branch']['prefix_cache_misses'])
            assert row['same_prefix']
        if phase == 'controls':
            match = next(i for i,j in enumerate(json.loads((OUT/'verification_manifest.json').read_text())) if j==spec)
            plain = json.loads((OUT/'verification'/('%03d_baseline_source.json'%match)).read_text())
            row['matches_public_baseline'] = core(plain['result']) == core(a['result'])
            assert row['matches_public_baseline']
        row['change_percent'] = 100*(row['after']/row['before']-1)
        rows.append(row)
        assert valid and (same or phase=='diagnostic')
    summary = {}
    if phase == 'diagnostic':
        for name, group in [('short',rows[:6]),('short50',rows[:3]),('short100',rows[3:6]),('long',rows[6:])]:
            summary[name] = dict(mean=statistics.mean(r['change_percent'] for r in group),
                wins=sum(r['change_percent'] < -1e-7 for r in group),ties=sum(abs(r['change_percent'])<=1e-7 for r in group),
                pairs=len(group),worst=max(r['change_percent'] for r in group))
    write(OUT/(phase+'_comparison.json'),dict(pairs=rows,summary=summary))
    print(phase,'comparison',len(rows),'passed',json.dumps(summary),flush=True)

if __name__=='__main__':
    cmd = sys.argv[1]
    if cmd == 'freeze': freeze()
    elif cmd == 'verify': verify()
    elif cmd == 'worker': worker(sys.argv[2],int(sys.argv[3]),sys.argv[4])
    elif cmd == 'run': run(sys.argv[2])
    elif cmd == 'fresh': fresh(sys.argv[2])
    elif cmd == 'compare': compare(sys.argv[2])
