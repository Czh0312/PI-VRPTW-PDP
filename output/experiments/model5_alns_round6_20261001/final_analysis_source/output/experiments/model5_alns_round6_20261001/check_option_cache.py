"""Controlled-clock comparison against the uncached, frozen regret candidate."""
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
sys.path.insert(0, str(ROOT))
from configuration.loader import load_routing_case, apply_container_pool_size
from models.routing.common.cycle import load_instance
from models.routing.solvers.alns import solver, cycle_cache, neighborhoods
from models.routing.solvers.alns.multibatch_repair import RegretRepair

package = types.ModuleType('_round6_uncached')
package.__path__ = [str(OUT/'candidate_01/source/models/routing/solvers/alns')]
sys.modules[package.__name__] = package
reference = importlib.import_module(package.__name__+'.solver')
ref_cache = importlib.import_module(package.__name__+'.cycle_cache')
ref_repair = importlib.import_module(package.__name__+'.multibatch_repair').RegretRepair
jobs = json.loads((OUT/'benchmark_manifest.json').read_text())['jobs']
selected = [next(j for j in jobs if j['family']==family and j['batches']==batches
    and j['pool_label']=='tight' and j['seed']==42 and j['budget']==5)
    for family,batches in [('zhou_Set2_100-5-2_subset50',2),
        ('zhou_Set2_100-5-2_subset50',3),('zhou_Set2_100-5-3',3)]]
records = []
for job in selected:
    config,fleet = load_routing_case(job['case'],use_pool_config=False)
    config,fleet = apply_container_pool_size(config,fleet,job['pool'])
    problem = load_instance(config,fleet)
    repairs = []
    for seed in range(10):
        state = solver.initial_state(problem,random.Random(seed))
        a,b = random.Random(seed),random.Random(seed)
        left = ref_repair(problem)(problem,state,a,'related',float('inf'))
        right = RegretRepair(problem)(problem,state,b,'related',float('inf'))
        assert neighborhoods.state_key(left)==neighborhoods.state_key(right)
        assert a.getstate()==b.getstate()
        repairs.append(seed)
    traces=[]
    def run(function,module):
        trace=[]
        original=module.CycleEvaluator.__call__
        def record(self,problem,state):
            trace.append(neighborhoods.state_key(state))
            return original(self,problem,state)
        module.CycleEvaluator.__call__=record
        try:
            result=function(problem,max_time=60,max_iterations=500,seed=42)
            traces.append(trace)
            return result
        finally:
            module.CycleEvaluator.__call__=original
    clock=time.perf_counter
    try:
        time.perf_counter=lambda:1000.0
        left=run(reference.solve_alns,ref_cache)
        right=run(solver.solve_alns,cycle_cache)
    finally:
        time.perf_counter=clock
    assert traces[0]==traces[1]
    for field in ('e1_trips','e2_trips','audit','cutoff_snapshots','depot_releases'):
        assert left[field]==right[field]
    for field in ('objective','incumbent_history','operator_attempts','operator_improvements',
                  'regret_repair_calls','regret_repair_reordered'):
        assert left['info'][field]==right['info'][field]
    records.append(dict(case_id=job['case_id'],repair_comparisons=len(repairs),
        iterations=500,trace_length=len(traces[0]),objective=right['info']['objective'],
        choices_rng_trajectory_schedule_and_audit_equal=True))
    print(job['case_id'],'cache preserves repair choices and full search trajectory',flush=True)
owned=['models/routing/solvers/alns/solver.py','models/routing/solvers/alns/multibatch_repair.py']
result=dict(controlled_clock=True,records=records,
    source_sha256={f:hashlib.sha256((ROOT/f).read_bytes()).hexdigest() for f in owned})
(OUT/'option_cache_verification.json').write_text(json.dumps(result,indent=2)+'\n')
