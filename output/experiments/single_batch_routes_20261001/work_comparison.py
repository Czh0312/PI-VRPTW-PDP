"""Paired deterministic-work diagnostics, using identical audited starts."""
from pathlib import Path
import sys,json,time,importlib.util
ROOT=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT))
OUT=Path(__file__).resolve().parent
from configuration.loader import load_routing_case
from models.routing.common.cycle import load_instance
from models.routing.common.cycle.assignment import assigned_problem,input_assignment,lift_result
from models.routing.common.cycle.audit import audit_solution
from models.routing.solvers.exact import solver
from models.routing.solvers.exact.primal import construct_incumbent
spec=importlib.util.spec_from_file_location('models.routing.solvers.exact._reference_routes',OUT/'before/source_snapshot/models/routing/solvers/exact/single_batch_formulation.py')
reference=importlib.util.module_from_spec(spec);spec.loader.exec_module(reference)
original_builder=solver.build_model
original_seed=solver.construct_incumbent
rows=[]
for size,model in [(20,3),(30,3),(20,4),(30,2)]:
    run=f'zhou_Set2_100-5-1b_subset{size}_M{model}'
    p=load_instance(*load_routing_case(ROOT/f'output/experiments/single_batch_strengthening_20260930/cases/{run}.json'))
    fixed=assigned_problem(p,input_assignment(p)) if p.free_assignment else p
    initial=construct_incumbent(fixed,time.perf_counter()+2)
    assert initial is not None
    if p.free_assignment: initial=lift_result(p,initial,input_assignment(p))
    assert audit_solution(p,initial)['valid']
    (OUT/f'work_{run}_initial.json').write_text(json.dumps(initial,indent=2))
    for phase,builder in [('before',reference.build_model),('after',original_builder)]:
        def bounded(*args,**kwargs):
            m,ctx=builder(*args,**kwargs)
            m.Params.WorkLimit=15
            return m,ctx
        solver.build_model=bounded
        solver.construct_incumbent=lambda *a,**kw:None
        try:
            r=solver.solve_exact(p,time_limit=60,seed=42,initial_result=initial)
        finally:
            solver.build_model=original_builder
            solver.construct_incumbent=original_seed
        assert audit_solution(p,r)['valid']
        row=dict(run=run,phase=phase,initial_objective=initial['info']['objective'],info=r['info'])
        rows.append(row)
        (OUT/f'work_{run}_{phase}.json').write_text(json.dumps(r,indent=2))
        (OUT/'work_comparison.json').write_text(json.dumps(rows,indent=2))
        print(run,phase,{k:r['info'].get(k) for k in ('objective','best_bound','mip_gap','mip_work','mip_runtime','status')},flush=True)
