"""Compare current default M5 with frozen candidate despite optional B1 helper additions."""
import hashlib
import importlib
import json
from pathlib import Path
import random
import sys
import time
import types

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT))
OUT = Path(__file__).resolve().parent
SOURCE = OUT/'candidate01_source'
from configuration.loader import load_routing_case, apply_container_pool_size
from models.routing.common.cycle import load_instance
from models.routing.solvers.alns import solver, evaluator, cycle_cache, neighborhoods

package = types.ModuleType('_m5_arc_current_reference')
package.__path__ = [str(SOURCE/'models/routing/solvers/alns')]
sys.modules[package.__name__] = package
reference_solver = importlib.import_module(package.__name__+'.solver')
reference_evaluator = importlib.import_module(package.__name__+'.evaluator')
reference_cache = importlib.import_module(package.__name__+'.cycle_cache')
reference_neighborhoods = importlib.import_module(package.__name__+'.neighborhoods')


def run():
    paths = [f'models/routing/solvers/alns/{name}.py' for name in
             ('solver','intensification','cycle_cache','evaluator','neighborhoods','construction')]
    paths += [f'models/routing/common/cycle/{name}.py' for name in
              ('instance','policy','audit','precheck','state')]
    source_hashes = {path:hashlib.sha256((ROOT/path).read_bytes()).hexdigest() for path in paths}
    frozen_hashes = {path:hashlib.sha256((SOURCE/path).read_bytes()).hexdigest() for path in paths}
    optional_helpers = ('models/routing/solvers/alns/evaluator.py',
                        'models/routing/solvers/alns/neighborhoods.py',
                        'models/routing/solvers/alns/construction.py')
    assert all(source_hashes[path]==frozen_hashes[path] for path in paths if path not in optional_helpers)
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
        trace = []
        def traced_solve(engine,cache_module,key_function):
            original = cache_module.CycleEvaluator.__call__
            def recording(self,problem,state):
                trace.append(key_function(state))
                return original(self,problem,state)
            cache_module.CycleEvaluator.__call__ = recording
            try:
                return engine.solve_alns(problem,max_time=60,max_iterations=1000,seed=42)
            finally:
                cache_module.CycleEvaluator.__call__ = original
        clock = time.perf_counter
        try:
            time.perf_counter = lambda:1000.0
            reference = traced_solve(reference_solver,reference_cache,reference_neighborhoods.state_key)
            prefix = list(trace);trace.clear()
            current = traced_solve(solver,cycle_cache,neighborhoods.state_key)
        finally:
            time.perf_counter = clock
        assert trace==prefix,'Default M5 proposal/evaluation trajectory changed'
        for field in ('e1_trips','e2_trips','audit','cutoff_snapshots','depot_releases'):
            assert current[field]==reference[field],field
        assert current['info']['objective']==reference['info']['objective']
        state = evaluator.initial_state(problem,random.Random(42))
        rng = random.Random(20261001)
        ops = ['relocate','pair','split_merge','vehicle','cutoff','e1_delivery','e1_pickup','reverse']
        for step in range(120):
            if step % 10 == 0:state=evaluator.initial_state(problem,rng)
            state=solver._move(problem,state,rng,ops[step % len(ops)])
            assert evaluator.evaluate(problem,state)==reference_evaluator.evaluate(problem,state)
        for seed in range(20):
            original = evaluator.initial_state(problem,random.Random(seed))
            mode = 'related' if seed % 2 else 'random'
            assert neighborhoods.destroy_repair(problem,original,random.Random(seed),mode,float('inf')) == (
                reference_neighborhoods.destroy_repair(problem,original,random.Random(seed),mode,float('inf')))
        records.append(dict(case_id=job['case_id'],pool=job['pool'],iterations=1000,
                            objective=current['info']['objective'],evaluation_trace_length=len(trace),
                            randomized_decoder_checks=120,repair_checks=20,default_m5_path_equal=True))
    assert all(hashlib.sha256((ROOT/path).read_bytes()).hexdigest()==digest
               for path,digest in source_hashes.items()),'Current source changed during verification'
    result = dict(current_sha256=source_hashes,frozen_sha256=frozen_hashes,
                  default_m5_controlled_clock_trajectories_equal=True,
                  randomized_decoder_checks=360,repair_checks=60,records=records)
    (OUT/'current_compatibility.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({key:value for key,value in result.items() if key not in ('current_sha256','frozen_sha256')},indent=2))


if __name__ == '__main__':run()
