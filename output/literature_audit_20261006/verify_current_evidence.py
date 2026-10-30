"""Read-only reassessment of saved experiments; does not solve new cases."""
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import sys
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
OUT = Path(__file__).resolve().parent


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def rel(path):
    return str(Path(path).relative_to(ROOT)).replace('\\', '/')


def tests(path):
    suites = ET.parse(path).getroot().findall('testsuite')
    return dict(path=rel(path), **{key: sum(int(s.get(key, '0')) for s in suites)
                                 for key in ('tests', 'failures', 'errors', 'skipped')})


studies = {}
for name, relative in (
    ('small_and_medium', 'output/model5678_comparison/20261006_same_cost_v1'),
    ('large_60_seconds', 'output/model5678_comparison/20261006_large_same_cost_v1'),
    ('large_300_seconds', 'output/model35678_comparison/20261006_5min_v1'),
):
    folder = ROOT / relative
    rows = read(folder / 'rows.json')
    plan = read(folder / 'experiment.json')
    demand = {c['family']: c['demand'] for c in plan['cases']}
    assert all(r['valid'] for r in rows)
    assert all(math.isclose(sum(r['cost_components'].values()), r['cost'], abs_tol=1e-6)
               for r in rows)
    assert all(math.isclose(r['objective'], r['cost'], abs_tol=1e-5, rel_tol=1e-7)
               for r in rows if r['model'] in (6, 7, 8))
    joint = [r for r in rows if r['model'] in (6, 7, 8)]
    representatives = []
    for family in demand:
        for model in sorted({r['model'] for r in rows}):
            subset = [r for r in rows if r['family'] == family and r['model'] == model]
            if not subset:
                continue
            row = min(subset, key=lambda r: (r['distance'], r['cost']) if model in (3, 5)
                      else (r['cost'], r['distance']))
            representatives.append({**{k: row.get(k) for k in
                ('id', 'family', 'model', 'method', 'seed', 'cost', 'distance', 'pool',
                 'minimum_pool', 'k', 'delivery_batches', 'proven_optimal', 'cost_components')},
                'demand': demand[family], 'selection': 'minimum_distance' if model in (3, 5)
                else 'minimum_cost'})
    hashes = plan.get('source_hashes', {})
    snapshot_bad = [name for name, expected in hashes.items()
                    if digest(folder / 'source_snapshot' / name) != expected]
    assert not snapshot_bad, snapshot_bad
    input_snapshots = plan.get('input_hashes', {})
    for item in input_snapshots.values():
        assert digest(folder / item['snapshot']) == item['sha256'], item['snapshot']
    assert digest(folder / 'inputs/common_economic.json') == plan['common_economic_sha256']
    current_different = [name for name, expected in hashes.items()
                         if not (ROOT / name).is_file() or digest(ROOT / name) != expected]
    studies[name] = dict(path=relative, rows=len(rows), valid_rows=sum(r['valid'] for r in rows),
        families=len(demand), joint_rows=len(joint),
        joint_one_delivery_rows=sum(r['delivery_batches'] == 1 for r in joint),
        joint_pool_equal_demand_rows=sum(r['pool'] == demand[r['family']] for r in joint),
        joint_pool_below_demand_rows=sum(r['pool'] < demand[r['family']] for r in joint),
        joint_proven_optimal_rows=sum(bool(r['proven_optimal']) for r in joint),
        economic_profile_hashes=sorted({r['economic_profile_hash'] for r in rows}),
        source_snapshot_files_checked=len(hashes), current_source_differences=current_different,
        input_snapshot_files_checked=len(input_snapshots)+1,
        rows_sha256=digest(folder / 'rows.json'), experiment_sha256=digest(folder / 'experiment.json'),
        representatives=representatives)

# Reconstruct all 110 saved solutions with the current physical auditor.
from configuration.loader import load_routing_case
from models.routing.common.cycle.instance import load_instance
from models.routing.common.cycle.audit import audit_solution
from models.cost.comparison import evaluate_comparison_cost

replays = []
for study_name, study in studies.items():
    folder = ROOT / study['path']
    plan = read(folder / 'experiment.json')
    for job in plan['jobs']:
        config, fleet = load_routing_case(job['case_path'], use_pool_config=False)
        problem = load_instance(config, fleet)
        result = read(folder / 'results' / job['id'] / 'result.json')
        original_row = read(folder / 'results' / job['id'] / 'row.json')
        audit = audit_solution(problem, result)
        assert audit['valid'] and audit['periodic_replay']['valid'], (job['id'], audit['errors'])
        cost = evaluate_comparison_cost(problem, result, config['economic_config'], audit=audit)
        assert math.isclose(cost['total'], original_row['cost'], abs_tol=1e-6), job['id']
        assert audit['minimum_pool_for_schedule'] == original_row['minimum_pool'], job['id']
        replays.append(dict(study=study_name, id=job['id'], model=job['model'], valid=audit['valid'],
            periodic_replay_valid=audit['periodic_replay']['valid'],
            minimum_pool_for_schedule=audit['minimum_pool_for_schedule'],
            recomputed_cost=cost['total'], saved_result_sha256=digest(folder / 'results' / job['id'] / 'result.json')))

core_names = (
    'models/cost/joint_batch.py', 'models/routing/common/cycle/calendar.py',
    'models/routing/common/cycle/joint.py', 'models/routing/variants/calendar_search.py',
    'models/routing/variants/model_6/exact/model.py',
    'models/routing/variants/model_8/alns/search.py',
    'models/routing/solvers/exact/multibatch_logic_benders.py',
    'tests/routing/test_model78_independent_oracle.py', 'tests/routing/test_model78_review.py',
)
report = ROOT / 'docs/novelty_reassessment_20261006.md'
local_links = []
broken_links = []
if report.is_file():
    for target in re.findall(r'\]\(([^)]+)\)', report.read_text(encoding='utf-8')):
        if '://' in target or target.startswith('#'):
            continue
        target = target.split('#')[0].strip('<>')
        path = (report.parent / target).resolve()
        local_links.append(target)
        if not path.exists():
            broken_links.append(target)

value = dict(review_date='2026-10-06', generated_at=datetime.now(timezone.utc).isoformat(),
    scope='Novelty reassessment, saved-solution replay, and 30 small model checks; no new large optimization',
    studies=studies, current_code_replay=dict(tested=len(replays), valid=len(replays), rows=replays),
    current_model_tests=tests(OUT / 'current_model_checks_host.xml'),
    first_sandbox_attempt=tests(OUT / 'current_model_checks.xml'),
    sandbox_skip_reason='Gurobi license user mismatch; rerun under host account passed all 30 selected tests',
    inspected_current_core_sha256={name: digest(ROOT / name) for name in core_names},
    report=dict(path=rel(report), local_links_checked=len(local_links), broken_links=broken_links,
                sha256=digest(report) if report.exists() else None),
    literature_scope='Previous priority PDF audits plus current publisher abstract for Hariga 2016 and primary HTML model sections for Lakhmi et al. 2022. No exhaustive originality proof.')
assert not broken_links, broken_links
(OUT / 'reassessment_verification.json').write_text(json.dumps(value, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
print(json.dumps(dict(studies={key: {k: v for k, v in study.items()
       if k in ('rows', 'valid_rows', 'families', 'joint_rows', 'joint_one_delivery_rows',
                'joint_pool_equal_demand_rows', 'joint_pool_below_demand_rows',
                'joint_proven_optimal_rows', 'current_source_differences')} for key, study in studies.items()},
       current_code_replays=len(replays), tests=value['current_model_tests'], report=value['report']),
       ensure_ascii=False, indent=2))
