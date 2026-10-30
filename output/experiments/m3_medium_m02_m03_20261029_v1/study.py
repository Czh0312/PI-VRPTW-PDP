"""Current M3 exact entry on two additional 20-order cases, one seed each."""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
import time
import zipfile

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[2]
sys.path.insert(0, str(ROOT))
import gurobipy as gp
from tools.configuration.loader import load_routing_case
from models.common.cycle import load_instance
from models.common.cycle.audit import audit_solution
from models.routing.model_3.exact import solve_exact

CASES = ('M02', 'M03')


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def save(path, data):
    path = Path(path)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
    temporary.replace(path)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def verify(hashes):
    for path, expected in hashes.items():
        assert sha(ROOT / path) == expected, path


def case_path(case):
    return ROOT / f'data/formal_benchmark27_uniform_7_20_50_v1/model_3/medium/{case}/case.json'


def main():
    assert not (OUT / 'plan.json').exists(), 'Refusing to overwrite an attempt'
    files = {Path(__file__)}
    for folder in ('models/routing/model_3/exact', 'models/common', 'models/cost', 'tools/configuration'):
        files.update((ROOT / folder).rglob('*.py'))
    for case in CASES:
        cfg, _ = load_routing_case(case_path(case), use_pool_config=False)
        files.add(case_path(case))
        files.update(ROOT / cfg[key] for key in ('data','routing_config','economic_config',
            'dataset_metadata','orders','operating_policy','parameter_contract'))
    hashes = {path.relative_to(ROOT).as_posix(): sha(path) for path in files}
    save(OUT / 'plan.json', dict(cases=list(CASES), model=3, customers_per_case=20,
        seed=42, threads=1, workers=1, budget_per_case_seconds=300, formulation='expanded',
        route_strengthening_requested=True, small_enumeration_limit=9,
        historical_information_used=False, source_hashes_and_inputs=hashes,
        interpreter=sys.executable, gurobi_version=list(gp.gurobi.version()),
        registered_at=datetime.now(timezone.utc).isoformat(),
        timing='Case load, model construction, optimization, extraction, independent audit; startup/preflight/archive excluded.'))
    with zipfile.ZipFile(OUT / 'source_inputs.zip', 'x', compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(hashes):
            archive.write(ROOT / path, path)
    rows = []
    for case in CASES:
        verify(hashes)
        target = OUT / case
        target.mkdir()
        print('START ' + case, flush=True)
        begun = time.perf_counter()
        p = load_instance(*load_routing_case(case_path(case), use_pool_config=False))
        assert p.model_number == 3 and p.cost_objective and len(p.orders) == p.total == 20
        calls = []
        original = gp.Model.optimize

        def observed(model, *args, **kwargs):
            assert not args and not kwargs
            model.update()
            model.Params.OutputFlag = 1
            model.Params.LogToConsole = 0
            model.Params.LogFile = (target.relative_to(ROOT) / 'gurobi.log').as_posix()
            call = dict(model_name=model.ModelName, variables=model.NumVars, binary=model.NumBinVars,
                linear_constraints=model.NumConstrs, general_constraints=model.NumGenConstrs,
                starts=model.NumStart, fingerprint=model.Fingerprint,
                optimize_begins_after_seconds=time.perf_counter()-begun)
            save(OUT / 'progress.json', dict(case=case, stage='optimizing', completed=len(rows), **call))
            print(json.dumps(dict(case=case, **call), ensure_ascii=False), flush=True)
            try:
                return original(model)
            finally:
                call.update(native_status=model.Status, runtime=model.Runtime, work=model.Work,
                    nodes=model.NodeCount, optimize_ends_after_seconds=time.perf_counter()-begun)
                calls.append(call)

        gp.Model.optimize = observed
        try:
            result = solve_exact(p, time_limit=300, seed=42)
        finally:
            gp.Model.optimize = original
        info = result['info']
        audit = audit_solution(p, result) if result.get('audit', {}).get('valid') else None
        if audit:
            assert audit['valid'] and audit['periodic_replay']['valid']
        if info['proven_optimal']:
            assert audit and calls[-1]['native_status'] == gp.GRB.OPTIMAL
            assert abs(info['best_bound']-audit['objective']) <= 1e-6
        assert not info['route_strengthening']['applied']
        assert info['route_strengthening']['reason'] == 'outside_small_fixed_assignment_scope'
        row = dict(case=case, model=3, customers=20, seed=42, budget_seconds=300,
            total_wall_seconds=time.perf_counter()-begun, **info, audited_feasible=bool(audit),
            periodic_replay_valid=bool(audit and audit['periodic_replay']['valid']),
            satellite_order_counts=dict(Counter(order.satellite for order in p.orders)),
            optimize_calls=calls, small_acceleration_applied=False, historical_information_used=False)
        save(target / 'result.json', result)
        save(target / 'independent_audit.json', audit)
        save(target / 'summary.json', row)
        save(target / 'completion.json', dict(files={name: sha(target / name) for name in (
            'result.json','independent_audit.json','summary.json')}))
        rows.append(row)
        save(OUT / 'rows.json', rows)
        save(OUT / 'progress.json', dict(stage='completed' if len(rows) == 2 else 'case_completed',
            completed=len(rows), planned=2, optimal=sum(r['proven_optimal'] for r in rows)))
        verify(hashes)
        print(json.dumps({key: row[key] for key in ('case','status','proven_optimal','objective','best_bound','mip_gap','total_wall_seconds')}, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
