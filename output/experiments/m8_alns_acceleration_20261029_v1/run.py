"""Frozen-source ALNS equivalence replay and matched wall-budget experiments."""
import argparse
from collections import OrderedDict
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import statistics
import subprocess
import sys
import time

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def install(variant):
    source = HERE / 'sources' / variant
    sys.path[:0] = [str(source), str(ROOT)]
    assert not any(name == 'models' or name.startswith('models.') for name in sys.modules)
    from models.common.cycle import load_instance
    from models.common.cycle.audit import audit_solution
    from models.routing.model_8.alns.solver import solve_alns
    from models.routing.model_8.alns import evaluator
    from tools.configuration.loader import load_routing_case
    # Avoid import latency consuming a short algorithm-quality budget.
    from models.routing.model_8.alns import search, fixed_calendar_search, calendar_polish
    assert Path(evaluator.__file__).resolve().is_relative_to(source)
    return load_instance, audit_solution, solve_alns, evaluator, load_routing_case


def load(case, load_instance, load_routing_case):
    size = {'S': 'small', 'M': 'medium', 'L': 'large'}[case[0]]
    return load_instance(*load_routing_case(ROOT / f'data/formal_benchmark27_uniform_7_20_50_v1/model_8/{size}/{case}/case.json', use_pool_config=False))


def worker(args):
    load_instance, audit_solution, solve_alns, ev, load_routing_case = install(args.variant)
    p = load(args.case, load_instance, load_routing_case)
    out = HERE / ('corpus' if args.mode == 'capture' else 'replays' if args.mode == 'replay' else 'trials')
    if args.mode == 'capture':
        records = []
        original = ev.JointEvaluator.__call__
        def capture(self, assignment, state):
            if len(records) < 2000:
                policy = self.problem.operating_policy
                records.append(dict(K=len(policy.batches),
                    boundaries=[b.start for b in policy.batches]+[policy.cycle_end],
                    assignment=dict(assignment), state=deepcopy(state)))
            return original(self, assignment, state)
        ev.JointEvaluator.__call__ = capture
        result = solve_alns(p, max_time=args.seconds, max_iterations=10**9, seed=args.seed)
        save(out / f'{args.case}.json', records)
        save(out / f'{args.case}_capture.json', dict(records=len(records), result_valid=result.get('audit', {}).get('valid', False)))
        print(dict(case=args.case, captured=len(records)), flush=True)
        return
    if args.mode == 'replay':
        records = read(HERE / 'corpus' / f'{args.case}.json')
        for record in records:
            for route in record['state']['e2']:
                route['tasks'] = [tuple(task) for task in route['tasks']]
        rounds, final_digests = [], None
        for repetition in range(3):
            contexts = OrderedDict()
            digests = []
            elapsed = 0.
            for record in records:
                begun = time.perf_counter()
                key = (record['K'], tuple(record['boundaries']))
                if key not in contexts:
                    contexts[key] = ev.JointEvaluator(p.fixed_calendar(*key))
                    if len(contexts) > 24:
                        contexts.popitem(last=False)
                contexts.move_to_end(key)
                value = contexts[key](record['assignment'], record['state'])
                elapsed += time.perf_counter() - begun
                # Serialization is outside the measured evaluation time.
                digests.append(hashlib.sha256(json.dumps(value, sort_keys=True,
                    ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode('utf-8')).hexdigest())
            if final_digests is not None:
                assert final_digests == digests
            final_digests = digests
            rounds.append(elapsed)
        save(out / args.variant / f'{args.case}.json', dict(case=args.case, variant=args.variant,
            records=len(records), rounds_seconds=rounds, median_seconds=statistics.median(rounds),
            value_sha256=final_digests))
        print(dict(case=args.case, variant=args.variant, records=len(records), median_seconds=statistics.median(rounds)), flush=True)
        return
    counters = dict(evaluations=0, misses=0, single_audit_accepts=0, reference_fallbacks=0, physical_audits=0)
    original = ev.JointEvaluator.__call__
    def observe(self, assignment, state):
        before = (self.misses, getattr(self, 'single_audit_accepts', 0), getattr(self, 'reference_fallbacks', 0))
        value = original(self, assignment, state)
        counters['evaluations'] += 1
        counters['misses'] += self.misses - before[0]
        counters['single_audit_accepts'] += getattr(self, 'single_audit_accepts', 0) - before[1]
        counters['reference_fallbacks'] += getattr(self, 'reference_fallbacks', 0) - before[2]
        return value
    ev.JointEvaluator.__call__ = observe
    from models.common.cycle import audit as audit_module
    original_audit = audit_module._audit
    def count_audit(*a, **kw):
        counters['physical_audits'] += 1
        return original_audit(*a, **kw)
    audit_module._audit = count_audit
    begun = time.perf_counter()
    cpu_begun = time.process_time()
    result = solve_alns(p, max_time=args.seconds, max_iterations=10**9, seed=args.seed)
    wall = time.perf_counter() - begun
    cpu_seconds = time.process_time() - cpu_begun
    measured_counters = dict(counters)
    audit = audit_solution(p, result)
    valid = bool(audit['valid'] and audit.get('periodic_replay', {}).get('valid'))
    assert not result['info'].get('proven_optimal', False)
    assert not any('gurobi' in name or '.exact' in name for name in sys.modules if name.startswith(('models.', 'gurobi')))
    folder = out / args.phase / args.case / f's{args.seed}' / args.variant
    if folder.exists():
        raise FileExistsError(folder)
    save(folder / 'result.json', result)
    save(folder / 'audit.json', audit)
    save(folder / 'summary.json', dict(case=args.case, variant=args.variant, phase=args.phase,
        seed=args.seed, seconds=args.seconds, solve_wall_seconds=wall, solve_cpu_seconds=cpu_seconds,
        objective=audit['objective'] if valid else None,
        iterations=result['info'].get('iterations'), K=result.get('cycle_batch_count'),
        N=result.get('container_pool_size'), audit_valid=valid,
        two_cycle_valid=bool(audit.get('periodic_replay', {}).get('valid')),
        counters=measured_counters, exact_solution_or_lower_bound_used=False, cold_start=True))
    print(dict(case=args.case, variant=args.variant, seed=args.seed, seconds=args.seconds,
               objective=audit.get('objective') if valid else None, valid=valid,
               iterations=result['info'].get('iterations'), counters=measured_counters), flush=True)


def invoke(mode, variant, case, seconds, seed=42, phase='pilot'):
    command = [sys.executable, '-X', 'utf8', str(Path(__file__).resolve()), 'worker', '--mode', mode,
               '--variant', variant, '--case', case, '--seconds', str(seconds), '--seed', str(seed), '--phase', phase]
    environment = {**os.environ, 'PYTHONHASHSEED': '0', **{key: '1' for key in
        ['OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS', 'NUMEXPR_NUM_THREADS']}}
    completed = subprocess.run(command, cwd=ROOT, env=environment, capture_output=True,
                               text=True, encoding='utf-8', timeout=seconds+240)
    folder = HERE / 'logs'
    folder.mkdir(exist_ok=True)
    (folder / f'{phase}_{mode}_{case}_s{seed}_{variant}.log').write_text(completed.stdout+completed.stderr, encoding='utf-8')
    if completed.returncode:
        raise RuntimeError(completed.stdout+completed.stderr)
    print(completed.stdout.strip(), flush=True)


def plan(phase, jobs):
    path = HERE / f'{phase}_plan.json'
    if path.exists():
        raise FileExistsError(path)
    sources = {name: {str(p.relative_to(HERE / 'sources' / name)): sha(p)
              for p in (HERE / 'sources' / name).rglob('*.py')} for name in ['baseline', 'candidate']}
    save(path, dict(jobs=jobs, sources=sources, driver_sha256=sha(Path(__file__)),
                   concurrency=1, python=str(Path(sys.executable).resolve()),
                   exact_solution_or_lower_bound_used=False, seeds=sorted({r['seed'] for r in jobs}),
                   quality_scoring='Known optima read only after both solvers finish.'))


def orchestrate(action):
    if action == 'equivalence':
        for case in ['S02', 'S03', 'S09', 'M01', 'L01']:
            invoke('capture', 'baseline', case, 5.)
            for variant in ['baseline', 'candidate']:
                invoke('replay', variant, case, 5.)
            left, right = [read(HERE / 'replays' / v / f'{case}.json') for v in ['baseline', 'candidate']]
            assert left['value_sha256'] == right['value_sha256'], case
        return
    jobs = []
    if action == 'pilot':
        triples = [(case, 42, 12.) for case in ['S02', 'S03', 'S09']]
    else:
        triples = [(f'S{i:02}', seed, 20.) for seed in [11, 23, 37] for i in range(1, 10)]
        triples += [(case, seed, 20.) for case in ['M01', 'L01'] for seed in [11, 23]]
    for index, (case, seed, seconds) in enumerate(triples):
        for variant in (['baseline', 'candidate'] if index % 2 == 0 else ['candidate', 'baseline']):
            jobs.append(dict(case=case, seed=seed, seconds=seconds, variant=variant, phase=action))
    plan(action, jobs)
    for index, job in enumerate(jobs):
        invoke('trial', **job)
        save(HERE / f'{action}_progress.json', dict(completed=index+1, expected=len(jobs), last=job))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['worker', 'equivalence', 'pilot', 'confirmatory'])
    parser.add_argument('--mode', choices=['capture', 'replay', 'trial'], default='trial')
    parser.add_argument('--variant', choices=['baseline', 'candidate'], default='baseline')
    parser.add_argument('--case', default='S03')
    parser.add_argument('--seconds', type=float, default=12.)
    parser.add_argument('--seed', type=int, default=42)
    parser.add_argument('--phase', default='pilot')
    args = parser.parse_args()
    worker(args) if args.action == 'worker' else orchestrate(args.action)
