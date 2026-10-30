"""Explore declared medium cases with ordinary model-8 ALNS; no K forcing."""
import json
from pathlib import Path
import sys
import time

PROJECT = Path.cwd()
sys.path.insert(0, str(PROJECT))
from cli.route import main as run_route
from models.common.cycle import load_instance
from models.common.cycle.audit import audit_solution

ROOT = Path(__file__).resolve().parent
plan = json.loads((ROOT/'plan.json').read_text(encoding='utf-8'))
started = time.perf_counter()
rows = []
for case in plan['cases']:
    case_path = f'{plan["dataset"]}/model_8/medium/{case}/case.json'
    target = ROOT/'runs'/case
    run_route(['--case', case_path, '--fleet-config', str(ROOT/'fleet_override.json'),
               '--solver', 'alns', '--time', str(plan['budget_seconds_per_case']),
               '--iterations', '1000000000', '--seed', str(plan['seed']), '--output', str(target)])
    result = json.loads((target/'result.json').read_text(encoding='utf-8'))
    manifest = json.loads((target/'manifest.json').read_text(encoding='utf-8'))
    problem = load_instance(manifest['config'], manifest['fleet'])
    valid = False
    if result['info'].get('objective') is not None:
        audit = audit_solution(problem, result)
        valid = audit['valid'] and audit['periodic_replay']['valid']
        if not valid:
            raise ValueError(f'{case}: independent replay failed: {audit["errors"]}')
    counts = [dict(K=r['batch_count'], valid=r['valid'], cost=r.get('objective'),
                   status=r['status'], seconds=r['runtime']) for r in result.get('count_search', [])]
    row = dict(case=case, customers=len(problem.orders), status=result['info']['status'],
        valid=valid, cost=result['info'].get('objective'), K=result.get('cycle_batch_count'),
        N=result.get('container_pool_size'), seconds=result['info']['runtime'],
        counts=counts, result=str(target/'result.json'))
    rows.append(row)
    found = valid and result['cycle_batch_count'] >= 2
    progress = dict(finished=found, found_case=case if found else None,
                    completed_cases=rows, elapsed=time.perf_counter()-started,
                    stop_rule=plan['selection'], gurobi_checked=False)
    (ROOT/'progress.json').write_text(json.dumps(progress, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    print(json.dumps(row, ensure_ascii=False), flush=True)
    if found:
        break
else:
    progress['finished'] = True
    progress['reason'] = 'No declared full-domain 30-second run selected K>=2; no infeasibility claim.'
    (ROOT/'progress.json').write_text(json.dumps(progress, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
print(json.dumps(progress, ensure_ascii=False), flush=True)
