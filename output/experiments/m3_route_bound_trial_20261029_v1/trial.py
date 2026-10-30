"""Fresh M3 cold solves; never load historical solver evidence."""
import argparse
import json
from pathlib import Path
import sys
import time

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[2]
sys.path.insert(0, str(ROOT))
from models.common.cycle import load_instance
from models.common.cycle.audit import audit_solution
from models.routing.model_3.exact import solve_exact
from tools.configuration.loader import load_routing_case

if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--cases', nargs='+', default=['S01', 'S06', 'S08'])
    ap.add_argument('--seconds', type=float, default=20)
    ap.add_argument('--formulation', default='expanded')
    ap.add_argument('--tag', default='')
    args = ap.parse_args()
    rows = []
    for case in args.cases:
        name = case + '_' + args.formulation + args.tag
        cfg, fleet = load_routing_case(ROOT / f'data/formal_benchmark27_uniform_7_20_50_v1/model_3/small/{case}/case.json', use_pool_config=False)
        p = load_instance(cfg, fleet)
        print('START ' + name, flush=True)
        start = time.perf_counter()
        result = solve_exact(p, time_limit=args.seconds, milp_options=dict(formulation=args.formulation))
        wall = time.perf_counter() - start
        audit = audit_solution(p, result) if result.get('audit', {}).get('valid') else None
        if audit:
            assert audit['valid'] and audit['periodic_replay']['valid']
        (OUT / (name + '_result.json')).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
        row = dict(case=case, formulation=args.formulation, wall_seconds=wall, **result['info'])
        rows.append(row)
        (OUT / ('rows_' + args.formulation + args.tag + '.json')).write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps(row, ensure_ascii=False), flush=True)
