"""Development probe of the universal M3 entry; no historical solver inputs."""
import argparse
import json
from pathlib import Path
import sys
import time

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[2]
sys.path.insert(0, str(ROOT))
import gurobipy as gp
from tools.configuration.loader import load_routing_case
from models.common.cycle import load_instance
from models.common.cycle.audit import audit_solution
from models.routing.model_3.exact import solve_exact

if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--case', default='M03')
    ap.add_argument('--size', default='medium')
    ap.add_argument('--seconds', type=float, default=60)
    ap.add_argument('--tag', default='v1')
    ap.add_argument('--no-resource-flows', action='store_true')
    ap.add_argument('--affordable-trips', action='store_true')
    args = ap.parse_args()
    target = OUT / f'{args.case}_{args.tag}'
    target.mkdir()
    cfg, fleet = load_routing_case(ROOT / f'data/formal_benchmark27_uniform_7_20_50_v1/model_3/{args.size}/{args.case}/case.json', use_pool_config=False)
    p = load_instance(cfg, fleet)
    calls = []
    original = gp.Model.optimize
    begun = time.perf_counter()
    capture = {}
    if args.affordable_trips:
        from models.routing.model_3.exact import milp_solver, route_partition
        original_build = milp_solver.build_model
        original_strengthen = route_partition.strengthen

        def captured_build(*a, **kw):
            answer = original_build(*a, **kw)
            capture['context'] = answer[1]
            return answer

        def captured_strengthen(*a, **kw):
            answer = original_strengthen(*a, **kw)
            capture['bounds'] = answer
            return answer

        milp_solver.build_model = captured_build
        route_partition.strengthen = captured_strengthen

    def observed(model, *a, **kw):
        assert not a and not kw
        model.update()
        if args.affordable_trips and model.ModelName == 'multi_batch_cutoff_v1' and not capture.get('done'):
            capture['done'] = True
            if model.NumStart:
                from types import SimpleNamespace
                from models.common.cycle.state import empty_result
                from models.routing.model_3.exact.solution import extract_solution
                from affordable_trips import restrict_affordable_trips
                context = capture['context']
                decoded = {}
                for layer in ('e1', 'e2'):
                    decoded[layer] = []
                    for route in context[layer]:
                        copied = dict(route)
                        for key in ('used', 'start', 'end'):
                            copied[key] = SimpleNamespace(X=route[key].Start)
                        for key in ('x', 'visit', 'times'):
                            copied[key] = {i: SimpleNamespace(X=var.Start) for i, var in route[key].items()}
                        decoded[layer].append(copied)
                candidate = empty_result(p, 'exact')
                extract_solution(p, model, decoded, candidate)
                pruned_at = time.perf_counter()
                remaining = model.Params.TimeLimit
                capture['pruning'] = restrict_affordable_trips(p, model, context, capture['bounds'], candidate,
                    deadline=min(begun+args.seconds, pruned_at+2), seed=42, threads=1)
                model.Params.TimeLimit = max(0., remaining-(time.perf_counter()-pruned_at))
                print('AFFORDABLE '+json.dumps(capture['pruning']), flush=True)
        model.update()
        model.Params.OutputFlag = 1
        model.Params.LogToConsole = 0
        model.Params.LogFile = (target.relative_to(ROOT) / f'{len(calls):02}_{model.ModelName}.log').as_posix()
        record = dict(name=model.ModelName, variables=model.NumVars, binary=model.NumBinVars,
                      rows=model.NumConstrs+model.NumGenConstrs, starts=model.NumStart)
        calls.append(record)
        print('OPTIMIZE '+json.dumps(record), flush=True)
        try:
            return original(model)
        finally:
            record.update(status=model.Status, seconds=model.Runtime, work=model.Work)

    gp.Model.optimize = observed
    try:
        result = solve_exact(p, time_limit=args.seconds, seed=42,
                             resource_flows=not args.no_resource_flows)
    finally:
        gp.Model.optimize = original
    audit = audit_solution(p, result) if result.get('audit', {}).get('valid') else None
    if audit:
        assert audit['valid'] and audit['periodic_replay']['valid']
    (target / 'result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    info = result['info']
    if capture.get('pruning'):
        info['experimental_affordable_trips'] = capture['pruning']
    summary = dict(case=args.case, wall_seconds=time.perf_counter()-begun, **info, calls=calls,
                   independent_audit_valid=bool(audit))
    (target / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(summary, ensure_ascii=False), flush=True)
