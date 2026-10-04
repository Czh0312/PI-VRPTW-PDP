"""Serialize every full schedule in the regression's greedy-capacity trap."""
import json
from pathlib import Path
import runpy
import sys
import time

OUT=Path(__file__).resolve().parent
ROOT=OUT.parents[2]
sys.path.insert(0,str(ROOT))
from models.routing.common.cycle.audit import audit_solution
from models.routing.solvers.alns.evaluator import evaluate
from models.routing.solvers.alns.neighborhoods import repair_orders
from models.routing.solvers.alns.multibatch_repair import RegretRepair

fixture=runpy.run_path(str(ROOT/'tests/routing/test_model5_regret_repair.py'))
problem,state=fixture['constrained_routes']()
removed=[problem.order_map['b1_A'],problem.order_map['b1_B']]
repair=RegretRepair(problem)
states=dict(original=state,greedy=repair_orders(problem,state,removed,time.perf_counter()+2),
            regret=repair.repair(state,removed,time.perf_counter()+2))
records={}
for name,proposal in states.items():
    distance,result,issues=evaluate(problem,proposal)
    assert not issues
    result=json.loads(json.dumps(result))
    audit=audit_solution(problem,result)
    assert audit['valid'] and audit['periodic_replay']['valid']
    records[name]=dict(distance=distance,state=proposal,result=result,independent_audit=audit)
record=dict(removed=[o.id for o in removed],reordered=repair.reordered,records=records,
    directed_nonmetric_distance_matrix={a:{b:problem.distance(a,b) for b in problem.network.nodes}
                                        for a in problem.network.nodes})
(OUT/'counterexample.json').write_text(json.dumps(record,ensure_ascii=False,indent=2)+'\n')
print({k:v['distance'] for k,v in records.items()})
