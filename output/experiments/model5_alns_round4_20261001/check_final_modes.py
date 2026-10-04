"""Validate final default against before and experimental mode against timed after."""
import hashlib
import importlib
import json
from pathlib import Path
import random
import sys
import time
import types

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[2]
sys.path.insert(0,str(ROOT))
from configuration.loader import load_routing_case, apply_container_pool_size
from models.routing.common.cycle import load_instance
from models.routing.solvers.alns import solver, cycle_cache, neighborhoods


def engine(name,source):
    package = types.ModuleType(name)
    package.__path__ = [str(source/'models/routing/solvers/alns')]
    sys.modules[name] = package
    return tuple(importlib.import_module(name+'.'+part) for part in ('solver','cycle_cache','neighborhoods'))


def run():
    paths = [f'models/routing/solvers/alns/{n}.py' for n in
             ('solver','intensification','cycle_cache','evaluator','neighborhoods','construction')]
    paths += [f'models/routing/common/cycle/{n}.py' for n in ('instance','policy','audit','precheck','state')]
    paths += ['models/routing/variants/model_5/alns/solver.py']
    hashes = {path:hashlib.sha256((ROOT/path).read_bytes()).hexdigest() for path in paths}
    references = {'default':engine('_m5_final_before',OUT/'before/source'),
                  'experimental':engine('_m5_final_after',OUT/'after/source')}
    jobs = json.loads((OUT/'benchmark_manifest.json').read_text())['jobs']
    selected = [next(j for j in jobs if j['family']==family and j['batches']==batches
                     and j['pool_label']=='tight' and j['seed']==42 and j['budget']==5)
                for family,batches in [('zhou_Set2_100-5-2_subset50',2),
                    ('zhou_Set2_100-5-3',3),('zhou_Set2_100-10-1_scenario02',3)]]
    records = []
    for job in selected:
        config,fleet = load_routing_case(job['case'],use_pool_config=False)
        config,fleet = apply_container_pool_size(config,fleet,job['pool'])
        problem = load_instance(config,fleet)
        for mode,(reference,cache_module,key_module) in references.items():
            traces = []
            def solve_traced(function,module,key_function,**kwargs):
                trace = []
                original = module.CycleEvaluator.__call__
                def recording(self,problem,state):
                    trace.append(key_function(state))
                    return original(self,problem,state)
                module.CycleEvaluator.__call__ = recording
                try:
                    result = function(problem,max_time=60,max_iterations=1000,seed=42,**kwargs)
                    traces.append(trace)
                    return result
                finally:
                    module.CycleEvaluator.__call__ = original
            clock = time.perf_counter
            try:
                time.perf_counter = lambda:1000.0
                saved = solve_traced(reference.solve_alns,cache_module,key_module.state_key)
                active = solve_traced(solver.solve_alns,cycle_cache,neighborhoods.state_key,
                                      **({'cache_descent_distances':True} if mode=='experimental' else {}))
            finally:
                time.perf_counter = clock
            assert traces[0] == traces[1],mode+' trajectory changed'
            for field in ('e1_trips','e2_trips','audit','cutoff_snapshots','depot_releases'):
                assert saved[field] == active[field],mode+' '+field
            for field in ('objective','incumbent_history','operator_attempts','operator_improvements',
                          'descent_candidates','descent_filtered','descent_enumerations','incumbent_restarts'):
                assert saved['info'][field] == active['info'][field],mode+' '+field
            assert active['info']['descent_arc_cache_enabled'] == (mode=='experimental')
            records.append(dict(case_id=job['case_id'],mode=mode,iterations=1000,
                trace_length=len(traces[0]),objective=active['info']['objective'],
                trajectory_schedule_and_audit_equal=True))
            print(job['case_id'],mode,'matches frozen reference',flush=True)
        reference_solver,_,reference_neighborhoods = references['default']
        state = solver.initial_state(problem,random.Random(42))
        rng = random.Random(20261001)
        operators = ['relocate','pair','split_merge','vehicle','cutoff','e1_delivery','e1_pickup','reverse']
        for step in range(120):
            if step % 10 == 0:state=solver.initial_state(problem,rng)
            state=solver._move(problem,state,rng,operators[step % len(operators)])
            assert solver.evaluate(problem,state) == reference_solver.evaluate(problem,state)
        for seed in range(20):
            original = solver.initial_state(problem,random.Random(seed))
            mode = 'related' if seed % 2 else 'random'
            assert neighborhoods.destroy_repair(problem,original,random.Random(seed),mode,float('inf')) == (
                reference_neighborhoods.destroy_repair(problem,original,random.Random(seed),mode,float('inf')))
    assert all(hashlib.sha256((ROOT/path).read_bytes()).hexdigest()==digest for path,digest in hashes.items())
    result = dict(default_matches_before=True,experimental_matches_after=True,
                  controlled_clock=True,randomized_decoder_checks=360,repair_checks=60,
                  source_sha256=hashes,records=records)
    (OUT/'final_modes_verification.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')


if __name__ == '__main__':run()
