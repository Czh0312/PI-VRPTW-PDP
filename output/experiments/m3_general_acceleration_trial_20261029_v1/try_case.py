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
    args = ap.parse_args()
    target = OUT / f'{args.case}_{args.tag}'
    target.mkdir()
    cfg, fleet = load_routing_case(ROOT / f'data/formal_benchmark27_uniform_7_20_50_v1/model_3/{args.size}/{args.case}/case.json', use_pool_config=False)
    p = load_instance(cfg, fleet)
    calls = []
    original = gp.Model.optimize
    begun = time.perf_counter()

    def observed(model, *a, **kw):
        assert not a and not kw
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
        result = solve_exact(p, time_limit=args.seconds, seed=42)
    finally:
        gp.Model.optimize = original
    audit = audit_solution(p, result) if result.get('audit', {}).get('valid') else None
    if audit:
        assert audit['valid'] and audit['periodic_replay']['valid']
    (target / 'result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    info = result['info']
    summary = dict(case=args.case, wall_seconds=time.perf_counter()-begun, **info, calls=calls,
                   independent_audit_valid=bool(audit))
    (target / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(summary, ensure_ascii=False), flush=True)
