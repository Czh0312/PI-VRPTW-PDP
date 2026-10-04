from pathlib import Path
import sys,json,math
import gurobipy as gp
HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE/'source_snapshot_subset19'))
from configuration.loader import load_routing_case
from models.routing.common.cycle import load_instance
from models.routing.solvers.exact.multibatch_route_pricing import evaluate_column,route_context
problem=load_instance(*load_routing_case(str(HERE/'inputs/case.json')))
model=gp.read(str(HERE/'cover_partition20/physical.mps.gz'))
model.Params.Threads=1;model.Params.TimeLimit=30;model.Params.MIPGap=model.Params.MIPGapAbs=0
model.Params.IntFeasTol=model.Params.FeasibilityTol=1e-9;model.optimize();assert model.Status==2
schema=json.loads((HERE/'s1full16/route_schema.json').read_text());context=route_context(problem,'S1')
columns=[];seen=set();total=0
for route in schema:
 if model.getVarByName(route['used']).X<.5:continue
 edges={a['a']:a['b'] for a in route['arcs'] if model.getVarByName(a['name']).X>.5}
 node=edges.get(-1);sequence=[];visited=set()
 while node is not None and node!=-2:
  assert node not in visited;visited.add(node);sequence.append(tuple(route['nodes'][node]));node=edges.get(node)
 assert node==-2 and len(visited)==sum(model.getVarByName(n).X>.5 for n in route['visits'].values())
 assert not seen.intersection(sequence);seen.update(sequence)
 column=evaluate_column(problem,context,sequence);assert column is not None
 places=['S1']+[problem.order_map[k[0]].customer for k in sequence]+['S1']
 cost=math.fsum(problem.distance(a,b) for a,b in zip(places,places[1:]));assert abs(cost-column['cost'])<1e-7
 total+=cost;columns.append(dict(tasks=sequence,cost=cost,source_physical_slot=route['label']))
assert seen=={(o.id,k) for o in problem.orders if o.satellite=='S1' for k in ('d','p')}
assert abs(total-model.ObjVal)<1e-7
(HERE/'pool22/physical_columns.json').write_text(json.dumps(columns,indent=2)+'\n')
(HERE/'pool22/physical_column_projection.json').write_text(json.dumps(dict(status=int(model.Status),physical_site_cost=model.ObjVal,
 independently_calculated_cost=total,task_coverage=len(seen),columns=len(columns),full_feasible=False),indent=2)+'\n')
model.dispose()
