"""Post-run checks of the complete nine-case M3 exact speed comparison."""
import hashlib
import json
from pathlib import Path
import re
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


@pytest.mark.parametrize('case', [f'S{i:02}' for i in range(1, 10)])
def test_original_and_optimized_full_model_optima_and_independent_audits_match(case):
    rows = read(OUT / 'rows.json')
    pair = {row['version']: row for row in rows if row['case'] == case}
    assert set(pair) == {'before', 'after'}
    cfg, fleet = load_routing_case(ROOT / pair['before']['case_path'], use_pool_config=False)
    p = load_instance(cfg, fleet)
    assert len(p.orders) == p.total == 7 and p.model_number == 3 and p.cost_objective
    actual = {}
    for version, row in pair.items():
        folder = OUT / 'runs' / row['id']
        for name, digest in read(folder / 'completion.json')['files'].items():
            assert hashlib.sha256((folder / name).read_bytes()).hexdigest() == digest
        result = read(folder / 'result.json')
        audit = audit_solution(p, result)
        assert audit['valid'] and audit['periodic_replay']['valid']
        assert row['status'] == 'OPTIMAL' and row['optimal']
        assert result['info']['status'] == 'OPTIMAL' and result['info']['proven_optimal']
        assert result['info']['proof_scope'] == 'full_model_3_integer_time_MILP'
        assert row['native_phases'][-1]['phase'] == 'full_model'
        assert row['native_phases'][-1]['status'] == 2
        assert row['proof_seconds'] <= row['total_seconds']
        assert abs(audit['objective']-row['bound']) <= 1e-6
        assert not row['history_used']
        actual[version] = audit['objective']
    assert actual['before'] == pytest.approx(actual['after'], rel=0, abs=1e-6)
    assert pair['before']['economic_profile_hash'] == pair['after']['economic_profile_hash']
    old_phase = pair['before']['native_phases'][-1]
    new_phase = pair['after']['native_phases'][-1]
    assert old_phase['variables'] == new_phase['variables']
    assert old_phase['binary'] == new_phase['binary']
    assert old_phase['starts'] == 0 and len(pair['before']['native_phases']) == 1
    historical = (ROOT / 'output/experiments/m3_s01_matched_cost_exact_20261024/expanded_gurobi.log'
                  if case == 'S01' else ROOT / (
                      'output/experiments/staged_comparison_27_seed42_20261024_v1/runs/'
                      f'A_{case}_m3_cost_exact_300s_s42/attempt_001/worker.log'))
    match = re.search(r'Model fingerprint: (0x[0-9a-fA-F]+)', historical.read_text(encoding='utf-8-sig'))
    assert match and old_phase['fingerprint'] & 0xffffffff == int(match[1], 16)


def test_frozen_scope_and_inputs_cover_exactly_the_nine_original_cases():
    plan = read(OUT / 'plan.json')
    assert len(plan['jobs']) == 18
    assert {(j['case'], j['version']) for j in plan['jobs']} == {
        (f'S{i:02}', version) for i in range(1, 10) for version in ('before', 'after')}
    assert all(j['seed'] == 42 and j['budget'] == 300 for j in plan['jobs'])
    assert plan['threads'] == plan['workers'] == 1
    assert plan['independent_process_per_call'] and not plan['historical_information_used']
    for name, digest in plan['hashes'].items():
        assert hashlib.sha256((ROOT / name).read_bytes()).hexdigest() == digest
