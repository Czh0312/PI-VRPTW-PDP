"""Fixed-clock trajectory and directed candidate equivalence; paired enumeration timing."""
from collections import defaultdict
import hashlib
import importlib
import json
from pathlib import Path
import statistics
import sys
import time
import types

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[2]
sys.path.insert(0,str(ROOT))
from configuration.loader import load_routing_case, apply_container_pool_size
from models.routing.common.cycle import load_instance


def package(name,source):
    module = types.ModuleType(name)
    module.__path__ = [str(source/'models/routing/solvers/alns')]
    sys.modules[name] = module
    return {part:importlib.import_module(name+'.'+part)
            for part in ('solver','cycle_cache','neighborhoods','intensification','evaluator')}


def run():
    baseline = OUT/'before/source'
    candidate = OUT/'candidate01_source'
    sources = {'baseline':baseline,'candidate':candidate}
    source_hashes = {version:{str(p.relative_to(folder)):hashlib.sha256(p.read_bytes()).hexdigest()
                             for p in folder.rglob('*.py')} for version,folder in sources.items()}
    for name in ('instance','policy','audit','precheck','state'):
        path = f'models/routing/common/cycle/{name}.py'
        assert source_hashes['baseline'][path] == source_hashes['candidate'][path]
        assert hashlib.sha256((ROOT/path).read_bytes()).hexdigest() == source_hashes['baseline'][path]
    engines = {version:package('_m5_arc_'+version,folder) for version,folder in sources.items()}
    jobs = json.loads((OUT/'benchmark_manifest.json').read_text())['jobs']
    selected = [j for j in jobs if j['split']=='development' and j['seed']==42 and j['budget']==5]
    records = []
    for job in selected:
        config,fleet = load_routing_case(job['case'],use_pool_config=False)
        config,fleet = apply_container_pool_size(config,fleet,job['pool'])
        problem = load_instance(config,fleet)
        results,traces = {},{}
        clock = time.perf_counter
        try:
            time.perf_counter = lambda:1000.0
            for version,engine in engines.items():
                trace = []
                original = engine['cycle_cache'].CycleEvaluator.__call__
                def recording(self,problem,state):
                    trace.append(engine['neighborhoods'].state_key(state))
                    return original(self,problem,state)
                engine['cycle_cache'].CycleEvaluator.__call__ = recording
                try:
                    results[version] = engine['solver'].solve_alns(
                        problem,max_time=60,max_iterations=1000,seed=42)
                    traces[version] = trace
                finally:
                    engine['cycle_cache'].CycleEvaluator.__call__ = original
        finally:
            time.perf_counter = clock
        assert traces['candidate'] == traces['baseline'],'Evaluation trajectory changed'
        for field in ('e1_trips','e2_trips','audit','cutoff_snapshots','depot_releases'):
            assert results['candidate'][field] == results['baseline'][field],field
        for field in ('objective','incumbent_history','operator_attempts','operator_improvements',
                      'descent_candidates','descent_filtered','descent_enumerations','incumbent_restarts'):
            assert results['candidate']['info'][field] == results['baseline']['info'][field],field
        assert results['candidate']['audit']['valid']
        state = engines['baseline']['evaluator'].initial_state(problem,__import__('random').Random(42))
        groups = defaultdict(list)
        for route in state['e2']:
            groups[route['satellite']].append(route)
        timings = defaultdict(list)
        for repetition in range(3):
            options = {}
            order = ('baseline','candidate') if repetition % 2 == 0 else ('candidate','baseline')
            for version in order:
                start = clock()
                descent = engines[version]['intensification'].CycleDescent(problem)
                options[version] = [descent._enumerate_routes(routes,float('inf')) for routes in groups.values()]
                timings[version].append(clock()-start)
            assert options['candidate'] == options['baseline'],'Candidate order or arithmetic changed'
        old,new = statistics.mean(timings['baseline']),statistics.mean(timings['candidate'])
        record = dict(case_id=job['case_id'],pool_label=job['pool_label'],pool=job['pool'],
            fixed_iterations=1000,trace_length=len(traces['baseline']),
            fixed_clock_trajectory_equal=True,full_schedule_audit_equal=True,candidates_equal=True,
            enumeration_before_seconds=timings['baseline'],enumeration_after_seconds=timings['candidate'],
            enumeration_mean_before=old,enumeration_mean_after=new,
            enumeration_change_percent=(new/old-1)*100)
        records.append(record)
        print(job['case_id'],job['pool_label'],'equal; enumeration',round(old,4),'->',round(new,4),flush=True)
    for version,folder in sources.items():
        assert source_hashes[version] == {str(p.relative_to(folder)):hashlib.sha256(p.read_bytes()).hexdigest()
                                          for p in folder.rglob('*.py')}
    result = dict(records=records,checked_cases=len(records),fixed_clock_trajectories_equal=True,
        candidate_order_arithmetic_and_audits_equal=True,
        mean_enumeration_change_percent=statistics.mean(r['enumeration_change_percent'] for r in records),
        baseline_sha256=source_hashes['baseline'],candidate_sha256=source_hashes['candidate'])
    (OUT/'arc_equivalence.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print('Enumeration paired mean change:',result['mean_enumeration_change_percent'],flush=True)


if __name__ == '__main__':run()
