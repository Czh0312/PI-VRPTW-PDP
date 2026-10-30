"""Verify two original-domain M3 medium runs and preserve timeout semantics."""
import hashlib
import json
from pathlib import Path
import sys

import pytest

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[2]
sys.path.insert(0, str(ROOT))
from tools.configuration.loader import load_routing_case
from models.common.cycle import load_instance
from models.common.cycle.audit import audit_solution


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


@pytest.mark.parametrize('case', ['M02', 'M03'])
def test_native_scope_bounds_and_original_business_replay(case):
    row = read(OUT / case / 'summary.json')
    result = read(OUT / case / 'result.json')
    assert row['seed'] == 42 and row['customers'] == 20 and row['budget_seconds'] == 300
    assert len(row['optimize_calls']) == 1 and row['optimize_calls'][0]['starts'] == 0
    assert result['info']['proof_scope'] == 'full_model_3_integer_time_MILP'
    assert not result['info']['route_strengthening']['applied']
    assert result['info']['route_strengthening']['reason'] == 'outside_small_fixed_assignment_scope'
    assert not row['historical_information_used']
    for filename, digest in read(OUT / case / 'completion.json')['files'].items():
        assert hashlib.sha256((OUT / case / filename).read_bytes()).hexdigest() == digest
    native = row['optimize_calls'][0]['native_status']
    assert row['proven_optimal'] == (native == 2 and row['audited_feasible'])
    if native == 9:
        assert row['status'] == 'TIME_LIMIT' and not row['proven_infeasible']
    if row['audited_feasible']:
        p = load_instance(*load_routing_case(ROOT / f'data/formal_benchmark27_uniform_7_20_50_v1/model_3/medium/{case}/case.json', use_pool_config=False))
        audit = audit_solution(p, result)
        assert audit['valid'] and audit['periodic_replay']['valid']
        assert row['objective'] == pytest.approx(audit['objective'])
        assert row['best_bound'] <= audit['objective']+1e-6
        assert row['mip_gap'] == pytest.approx(max(0., audit['objective']-row['best_bound']) / abs(audit['objective']))
    else:
        assert row['objective'] is None and not row['proven_optimal']


def test_frozen_scope_has_exactly_two_cases_and_one_seed_each():
    plan = read(OUT / 'plan.json')
    assert plan['cases'] == ['M02', 'M03'] and plan['seed'] == 42
    assert plan['threads'] == plan['workers'] == 1 and plan['budget_per_case_seconds'] == 300
    for path, digest in plan['source_hashes_and_inputs'].items():
        assert hashlib.sha256((ROOT / path).read_bytes()).hexdigest() == digest
