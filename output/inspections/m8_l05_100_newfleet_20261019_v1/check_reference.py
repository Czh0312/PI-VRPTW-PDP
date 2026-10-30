"""Check existing design witnesses against the new fleet; no optimization."""
from collections import Counter
from copy import deepcopy
import json
from pathlib import Path
import sys
from zipfile import ZipFile

PROJECT = Path.cwd()
sys.path.insert(0, str(PROJECT))
from cli.route import load_config
from models.common.cycle import load_instance
from models.common.cycle.audit import audit_solution
from models.common.cycle.joint import lift_result
from models.common.cycle.calendar import attach_calendar_decisions

ROOT = Path(__file__).resolve().parent
case_path = PROJECT / 'data/formal_benchmark27_interleaved_v1/model_8/large/L05/case.json'
config, original_fleet = load_config(case_path, use_pool_config=False)
override = json.loads((ROOT / 'fleet_override.json').read_text(encoding='utf-8'))
problem = load_instance(config, {**override, 'operating_policy': original_fleet['operating_policy']})
with ZipFile(PROJECT / 'data/formal_benchmark27_interleaved_v1/validation/witnesses.zip') as archive:
    one = json.loads(archive.read('L05/model_8.json'))
    three = json.loads(archive.read('L05/model_5.json'))

out = []
checked = audit_solution(problem, one)
out.append(dict(source='dataset M8 design witness, K=1', valid=checked['valid'],
    error_counts=dict(Counter(row['code'] for row in checked['errors'])),
    first_errors=checked['errors'][:5]))
if checked['valid']:
    (ROOT / 'verified_reference_K1.json').write_text(
        json.dumps(one, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')

boundaries = [batch['start'] for batch in three['batches']] + [three['batches'][-1]['end']]
child = problem.fixed_calendar(3, boundaries)
assignment = {order.id: order.batch for order in problem.orders}
cutoffs = {batch['id']: batch['cutoff'] for batch in three['batches']}
raw = deepcopy(three)
raw['info']['objective'] = None
candidate = lift_result(child, raw, assignment, problem.total, 'feasibility_witness', cutoffs)
if candidate.get('audit', {}).get('valid'):
    attach_calendar_decisions(problem, candidate, 3, boundaries)
    checked_three = audit_solution(problem, candidate)
else:
    checked_three = candidate.get('audit', {})
out.append(dict(source='dataset M5 design witness projected into M8, K=3',
    valid=checked_three.get('valid', False),
    error_counts=dict(Counter(row['code'] for row in checked_three.get('errors', []))),
    first_errors=checked_three.get('errors', [])[:5]))
if checked_three.get('valid'):
    (ROOT / 'verified_reference_K3.json').write_text(
        json.dumps(candidate, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
(ROOT / 'reference_check.json').write_text(json.dumps(out, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
print(json.dumps(out, ensure_ascii=False, indent=2))
