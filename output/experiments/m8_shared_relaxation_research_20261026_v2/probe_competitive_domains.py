"""Bound K and N jointly under an audited cost cap, without separate K models."""
import csv
import hashlib
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from experiments.runs.research_model8_shared_relaxation import build_relaxation
from models.common.cycle import load_instance
from tools.configuration.loader import load_routing_case

HERE = Path(__file__).resolve().parent


def main():
    import gurobipy as gp
    rows = []
    for idx in range(1, 10):
        case = f'S{idx:02d}'
        saved = json.loads((HERE / f'{case}.json').read_text(encoding='utf-8'))
        assert all(hashlib.sha256(Path(path).read_bytes()).hexdigest() == digest for path, digest in saved['input_hashes'].items())
        p = load_instance(*load_routing_case(ROOT / f'data/formal_benchmark27_uniform_7_20_50_v1/model_8/small/{case}/case.json', use_pool_config=False))
        library = json.loads((HERE / f'{case}_trip_library.json').read_text(encoding='utf-8'))
        model, ctx = build_relaxation(p, library)
        try:
            model.Params.TimeLimit = 20
            model.addConstr(model.getObjective() <= saved['audited_upper_bound'] + 1e-7, name='audited_cost_cap')
            K = gp.quicksum(k * v for k, v in ctx['selectors'].items())
            row = dict(case=case, upper_bound=saved['audited_upper_bound'],
                       scope='Contains every original solution no more expensive than the audited witness',
                       optimization_seconds=0.)
            for name, expression, sign in [('K_min', K, 1), ('K_max', K, -1),
                                           ('N_min', ctx['pool'], 1), ('N_max', ctx['pool'], -1)]:
                model.setObjective(sign * expression)
                model.optimize()
                assert model.Status == 2, (case, name, model.Status)
                row[name] = round(sign * model.ObjVal)
                row['optimization_seconds'] += model.Runtime
            rows.append(row)
            print(json.dumps(row, ensure_ascii=False), flush=True)
        finally:
            model.dispose()
    (HERE / 'competitive_domains.json').write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding='utf-8')


if __name__ == '__main__':
    main()
