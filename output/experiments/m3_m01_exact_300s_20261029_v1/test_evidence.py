"""Verify the single medium M3 attempt without upgrading a timeout to a proof."""
import hashlib
import json
from pathlib import Path
import sys

import pytest

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[2]
sys.path.insert(0, str(ROOT))
from models.common.cycle import load_instance
from models.common.cycle.audit import audit_solution
from tools.configuration.loader import load_routing_case


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def test_one_medium_case_one_seed_and_original_inputs_were_used():
    plan = read(OUT / 'plan.json')
    summary = read(OUT / 'summary.json')
    assert plan['model'] == 3 and plan['case'] == 'M01' and plan['customers'] == 20
    assert plan['seed'] == 42 and plan['budget_seconds'] == 300 and plan['threads'] == 1
    for path, expected in plan['hashes'].items():
        assert hashlib.sha256((ROOT / path).read_bytes()).hexdigest() == expected
    assert len(summary['actual_optimize_calls']) == 1
    assert summary['actual_optimize_calls'][0]['starts'] == 0
    assert summary['route_strengthening']['applied'] is False
    assert summary['route_strengthening']['reason'] == 'outside_small_fixed_assignment_scope'


def test_native_full_model_status_is_not_replaced_by_a_partial_certificate():
    summary = read(OUT / 'summary.json')
    result = read(OUT / 'result.json')
    assert result['info']['proof_scope'] == 'full_model_3_integer_time_MILP'
    native = summary['actual_optimize_calls'][0]['native_status']
    assert result['info']['proven_optimal'] == (native == 2 and summary['audited_feasible'])
    if summary['audited_feasible']:
        p = load_instance(*load_routing_case(ROOT / 'data/formal_benchmark27_uniform_7_20_50_v1/model_3/medium/M01/case.json', use_pool_config=False))
        audit = audit_solution(p, result)
        assert audit['valid'] and audit['periodic_replay']['valid']
        assert result['info']['objective'] == pytest.approx(audit['objective'])
        if result['info']['best_bound'] is not None:
            assert result['info']['best_bound'] <= audit['objective'] + 1e-6
            expected = max(0., audit['objective']-result['info']['best_bound']) / max(1e-9, abs(audit['objective']))
            assert result['info']['mip_gap'] == pytest.approx(expected)
    else:
        assert not summary['proven_optimal'] and summary['objective'] is None
        assert read(OUT / 'independent_audit.json') is None
