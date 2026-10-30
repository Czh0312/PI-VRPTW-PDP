"""Record ordinary full-domain ALNS candidates without restricting K or costs."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import shutil
import sys
import time

PROJECT = Path.cwd()
sys.path.insert(0, str(PROJECT))
from models.common.cycle import load_instance
from models.common.cycle.audit import audit_solution
from models.routing.model_8.alns.k_search.scheduler import solve_counts, _attach
from models.routing.model_8.alns.search import solve_variable_count

ROOT = Path(__file__).resolve().parent/'M01_full_domain_120s'
SOURCE = PROJECT/'output/inspections/m8_m01_interleaved_newfleet_20261019_v1/alns'
if ROOT.exists():
    raise ValueError('Refusing to overwrite existing candidate trace')
manifest = json.loads((SOURCE/'manifest.json').read_text(encoding='utf-8'))
for name, expected in manifest['source_sha256'].items():
    if hashlib.sha256((PROJECT/name).read_bytes()).hexdigest() != expected:
        raise ValueError(f'Source changed: {name}')
for name, expected in manifest['input_sha256'].items():
    if hashlib.sha256(Path(name).read_bytes()).hexdigest() != expected:
        raise ValueError(f'Input changed: {name}')
ROOT.mkdir()
shutil.copytree(SOURCE/'source_snapshot', ROOT/'source_snapshot')
shutil.copytree(SOURCE/'inputs', ROOT/'inputs')
manifest['config']['alns_time'] = 120
manifest.update(experiment='Full K=1..20, candidate recording only; no K forcing, no initial result.',
                seed=42, budget_seconds=120, gurobi_checked=False)
(ROOT/'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
problem = load_instance(manifest['config'], manifest['fleet'])
best_by_count, rows, visits = {}, {}, []
started = time.perf_counter()


def record(child, **kwargs):
    candidate = solve_variable_count(child, **kwargs)
    k = child.count
    valid = bool(candidate.get('audit', {}).get('valid'))
    if valid:
        saved = deepcopy(candidate)
        _attach(problem, child, saved, k)
        checked = audit_solution(problem, saved)
        if not checked['valid'] or not checked['periodic_replay']['valid']:
            raise ValueError(f'Candidate K={k} failed replay: {checked["errors"]}')
        if k not in best_by_count or saved['info']['objective'] < best_by_count[k]['info']['objective']:
            best_by_count[k] = saved
            destination = ROOT/f'best_K{k}.json'
            destination.write_text(json.dumps(saved, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
            rows[k] = dict(K=k, N=saved['container_pool_size'], cost=saved['info']['objective'],
                           valid=True, result=str(destination), elapsed=time.perf_counter()-started)
            print(json.dumps(rows[k], ensure_ascii=False), flush=True)
    visits.append(dict(K=k, valid=valid, status=candidate['info']['status'],
                       cost=candidate['info'].get('objective'), budget=kwargs['max_time']))
    (ROOT/'progress.json').write_text(json.dumps(dict(candidates=list(rows.values()), visits=visits,
        elapsed=time.perf_counter()-started), ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    return candidate


result = solve_counts(problem, solve_candidate=record, method='alns', budget=120,
    seed=42, max_iterations=1000000000, initial_result=None, budget_strategy='adaptive')
checked = audit_solution(problem, result)
if not checked['valid']:
    raise ValueError(checked['errors'])
result['audit'] = checked
(ROOT/'result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
summary = dict(case='M01', customers=len(problem.orders), budget=120, seed=42,
    full_domain=list(problem.batch_count_candidates), selected_K=result['cycle_batch_count'],
    selected_N=result['container_pool_size'], selected_cost=result['info']['objective'],
    candidates=list(rows.values()), cold_start=True, forced_K=False, gurobi_checked=False)
(ROOT/'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
print(json.dumps(summary, ensure_ascii=False), flush=True)
