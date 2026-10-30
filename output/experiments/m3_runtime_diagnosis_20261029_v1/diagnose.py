"""Cold M3 formulation ablation; original inputs, costs and exact rules retained."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import re
import sys
import time

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[2]
sys.path.insert(0, str(ROOT))

from tools.configuration.loader import load_routing_case
from models.common.cycle import load_instance
from models.common.cycle.audit import audit_solution
from models.common.cycle.state import empty_result
from models.common.solver_config import read_milp_info, accept_milp_audit
from models.routing.model_3.exact.model import build_model
from models.routing.model_3.exact.solution import extract_solution

VARIANTS = {
    'expanded': dict(compress_routes=False),
    'compact': dict(compress_routes=True),
    'expanded_indicators': dict(compress_routes=False, linear_e2_links=False),
}


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def save(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2,
                                    allow_nan=False), encoding='utf-8')


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def solve(case, variant, seconds):
    target = OUT / f'{case}_{variant}'
    if target.exists() and any(target.iterdir()):
        raise RuntimeError(f'Refusing to overwrite an existing attempt: {target}')
    target.mkdir(exist_ok=True)
    cfg, fleet = load_routing_case(
        ROOT / f'data/formal_benchmark27_uniform_7_20_50_v1/model_3/small/{case}/case.json',
        use_pool_config=False)
    p = load_instance(cfg, fleet)
    assert p.model_number == 3 and p.cost_objective and len(p.orders) == 7
    begun = time.perf_counter()
    m, ctx = build_model(p, deadline=begun + seconds, **VARIANTS[variant])
    build_seconds = time.perf_counter() - begun
    try:
        m.Params.Threads = 1
        m.Params.Seed = 42
        m.Params.MIPGap = 0
        m.Params.MIPGapAbs = 0
        m.Params.TimeLimit = max(0, seconds - build_seconds)
        m.Params.OutputFlag = 1
        m.Params.LogToConsole = 0
        m.Params.LogFile = (target.relative_to(ROOT) / 'gurobi.log').as_posix()
        assert m.NumStart == 0
        arcs = [v for r in ctx['e2'] for v in r['x'].values()]
        stats = dict(e1_slots=len(ctx['e1']), e2_slots=len(ctx['e2']),
                     indexed_e2_arc_handles=len(arcs),
                     unique_e2_arc_variables=len({v.index for v in arcs}),
                     e2_task_visits=sum(len(r['visit']) for r in ctx['e2']),
                     linear_constraints=m.NumConstrs, general_constraints=m.NumGenConstrs,
                     nonzeros=m.NumNZs, integers=m.NumIntVars, binary=m.NumBinVars,
                     horizon=p.operating_policy.cycle_end,
                     windows={str(k): list(v) for k, v in ctx['parameters'].task_windows.items()},
                     formulation_options=VARIANTS[variant],
                     linear_link_mode=ctx['single_batch_linear_links'])
        m.optimize()
        info = read_milp_info(m, proof_scope='full_model_3_integer_time_MILP',
                              build_time=build_seconds)
        result = empty_result(p, 'exact')
        result['info'] = info
        if m.SolCount:
            extract_solution(p, m, ctx, result)
            result['audit'] = audit_solution(p, result)
            accept_milp_audit(result, float(m.ObjVal))
            assert result['audit']['valid'] and result['audit']['periodic_replay']['valid']
        info['wall_seconds'] = time.perf_counter() - begun
        save(target / 'result.json', result)
        log = (target / 'gurobi.log').read_text(encoding='utf-8')
        root = re.search(r'Root relaxation: objective ([\deE+.-]+)', log)
        stats['root_relaxation'] = float(root[1]) if root else None
        row = dict(case=case, variant=variant, budget_seconds=seconds,
                   cold_start=True, mip_start=False, callback=False,
                   history_supplied_to_solver=False, info=info, model_stats=stats,
                   independent_audit_valid=result.get('audit', {}).get('valid', False))
        historical = next(r for r in read(ROOT / 'output/experiments/m3_m8_small_comparison_20261029_v1/rows.json')
                          if r['case'] == case)
        # Read historical optimum only after optimization and independent audit.
        expected = historical['m3_optimal_cost']
        row['historical_optimum_for_postcheck_only'] = expected
        if info.get('proven_optimal'):
            assert math.isclose(info['objective'], expected, abs_tol=1e-6, rel_tol=0)
        save(target / 'summary.json', row)
        print(json.dumps(dict(case=case, variant=variant, **info,
                              valid=row['independent_audit_valid']), ensure_ascii=False), flush=True)
        return row
    finally:
        m.dispose()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--cases', nargs='+', default=['S01', 'S06', 'S08'])
    ap.add_argument('--variants', nargs='+', choices=VARIANTS,
                    default=['expanded', 'compact', 'expanded_indicators'])
    ap.add_argument('--seconds', type=float, default=20)
    args = ap.parse_args()
    sources = [Path(__file__), ROOT / 'models/common/solver_config.py']
    sources += sorted((ROOT / 'models/routing/model_3/exact').rglob('*.py'))
    save(OUT / 'manifest.json', dict(cases=args.cases, variants=args.variants,
        budget_seconds=args.seconds, threads=1, seed=42,
        source_hashes={str(p.relative_to(ROOT)): sha(p) for p in sources},
        production_source_modified=False, cold_start=True,
        note='Bounded diagnostic subset; timeouts are retained. No full benchmark or pytest suite.'))
    rows = []
    for case in args.cases:
        for variant in args.variants:
            print(f'START {case} {variant}', flush=True)
            rows.append(solve(case, variant, args.seconds))
            save(OUT / 'rows.json', rows)


if __name__ == '__main__':
    main()
