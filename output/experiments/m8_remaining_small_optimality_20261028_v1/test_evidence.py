"""Independent original-input replay and recomputation for all five cases."""
import hashlib
import json
from pathlib import Path

import pytest

from experiments.runs.research_model8_shared_relaxation import build_relaxation, enumerate_trip_library
from models.common.cycle import load_instance
from models.common.cycle.audit import audit_solution
from models.cost.comparison import evaluate_comparison_cost
from tools.configuration.loader import load_routing_case

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
CASES = ['S03', 'S05', 'S07', 'S08', 'S09']


def read(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def problem(case):
    return load_instance(*load_routing_case(
        ROOT / f'data/formal_benchmark27_uniform_7_20_50_v1/model_8/small/{case}/case.json',
        use_pool_config=False,
    ))


@pytest.mark.parametrize('case', CASES)
def test_original_domain_inputs_and_source_hashes(case):
    folder = HERE / case
    manifest = read(folder / 'manifest.json')
    summary = read(folder / 'summary.json')
    assert manifest['original_K'] == summary['original_K'] == list(range(1, 8))
    assert summary['original_sources_and_inputs_unchanged']
    for path, digest in manifest['input_and_driver_hashes'].items():
        assert hashlib.sha256(Path(path).read_bytes()).hexdigest() == digest
    for path, digest in manifest['source_hashes'].items():
        assert hashlib.sha256((ROOT / path).read_bytes()).hexdigest() == digest


@pytest.mark.parametrize('case', CASES)
def test_best_witness_cost_and_two_cycle_replay_on_original_input(case):
    p = problem(case)
    folder = HERE / case
    summary = read(folder / 'summary.json')
    result = read(folder / 'best_audited_result.json')
    certificate = read(folder / 'reduction_certificate.json')
    audit = audit_solution(p, result)
    assert audit['valid'], audit.get('errors')
    assert audit['periodic_replay']['valid']
    cost = evaluate_comparison_cost(p, result, p.cost_profile.config, audit=audit)
    assert cost['total'] == pytest.approx(summary['best_audited_upper_bound'], abs=1e-6)
    assert result['cycle_batch_count'] in certificate['retained_K']
    assert certificate['N_min'] <= result['container_pool_size'] <= certificate['N_max']
    assert summary['global_lower_bound'] <= cost['total'] + 1e-6
    if summary['external_full_domain_optimality_certified']:
        assert summary['global_lower_bound'] == pytest.approx(cost['total'], abs=1e-6)


@pytest.mark.parametrize('case', CASES)
def test_native_evidence_and_original_integer_clock(case):
    folder = HERE / case
    native = read(folder / 'native_summary.json')
    summary = read(folder / 'summary.json')
    certificate = read(folder / 'reduction_certificate.json')
    assert native['status'] == summary['native_status']
    assert native['mip_start_supplied'] is False and native['route_hints_supplied'] is False
    assert set(map(int, native['screened_counts'])) == set(range(1, 8)) - set(certificate['retained_K'])
    assert all('COST_DOMINATED' in value['reason'] for value in native['screened_counts'].values())
    if native['sol_count']:
        p = problem(case)
        result = read(folder / 'native_result.json')
        audit = audit_solution(p, result)
        assert audit['valid'] and audit['periodic_replay']['valid']
        assert audit['objective'] == pytest.approx(native['objective'], abs=1e-6)
        policy, _, _ = p.count_clock(result['cycle_batch_count'])
        assert result['time_tick_seconds'] == policy.time_tick_seconds
        assert result['calendar_config']['batch_count_candidates'] == list(range(1, 8))
    else:
        assert native['objective'] is None
        assert not (folder / 'native_result.json').exists()
    log = (folder / 'gurobi.log').read_text(encoding='utf-8')
    assert ('Optimal solution found' in log) == (native['status'] == 'OPTIMAL')
    if native['status'] == 'OPTIMAL':
        assert native['best_bound'] == pytest.approx(native['objective'], abs=1e-5)
        assert summary['external_full_domain_optimality_certified']


@pytest.mark.parametrize('case', CASES)
def test_recompute_all_K_lower_bound_and_domain_extrema(case):
    import gurobipy as gp

    p = problem(case)
    folder = HERE / case
    manifest = read(folder / 'manifest.json')
    original = read(folder / 'reduction_certificate.json')
    audit = audit_solution(p, read(Path(manifest['witness_path'])))
    assert audit['valid'] and audit['periodic_replay']['valid']
    assert audit['objective'] == pytest.approx(original['audited_upper_bound'], abs=1e-6)
    library, _ = enumerate_trip_library(p)
    model, ctx = build_relaxation(p, library)
    try:
        model.Params.TimeLimit = 30
        model.optimize()
        assert model.Status == gp.GRB.OPTIMAL
        assert model.ObjBound == pytest.approx(original['global_lower_bound'], abs=1e-6)
        assert list(ctx['selectors']) == list(range(1, 8))
        model.addConstr(model.getObjective() <= audit['objective'] + 1e-7)
        count = gp.quicksum(k * selector for k, selector in ctx['selectors'].items())
        for name, expression, sign in [('K_min', count, 1), ('K_max', count, -1),
                                       ('N_min', ctx['pool'], 1), ('N_max', ctx['pool'], -1)]:
            model.setObjective(sign * expression)
            model.optimize()
            assert model.Status == gp.GRB.OPTIMAL
            assert sign * model.ObjVal == pytest.approx(original[name], abs=1e-6)
    finally:
        model.dispose()


@pytest.mark.parametrize('case', CASES)
def test_final_best_available_witness_and_combined_bound(case):
    p = problem(case)
    folder = HERE / case
    row = read(folder / 'final_summary.json')
    initial = read(folder / 'summary.json')
    phase = read(folder / 'phase_bound.json')
    result = read(folder / 'final_best_result.json')
    audit = audit_solution(p, result)
    assert audit['valid'] and audit['periodic_replay']['valid']
    assert audit['objective'] == pytest.approx(row['best_audited_upper_bound'], abs=1e-6)
    feasible_costs = []
    for evidence in row['witness_audits']:
        path = Path(evidence['path'])
        assert hashlib.sha256(path.read_bytes()).hexdigest() == evidence['sha256']
        replay = audit_solution(p, read(path))
        assert replay['valid'] == evidence['valid']
        if replay['valid'] and replay['periodic_replay']['valid']:
            feasible_costs.append(replay['objective'])
    assert row['best_audited_upper_bound'] == pytest.approx(min(feasible_costs), abs=1e-6)
    bounds = [initial['global_lower_bound'], phase['lower_bound']]
    if row['refinement'] is not None:
        bounds.append(row['refinement']['global_lower_bound'])
    assert row['global_lower_bound'] == pytest.approx(max(bounds), abs=1e-6)
    assert row['global_lower_bound'] <= audit['objective'] + 1e-6
    if row['external_full_domain_optimality_certified']:
        assert row['global_lower_bound'] == pytest.approx(audit['objective'], abs=1e-6)


@pytest.mark.parametrize('case', CASES)
def test_phase_ordered_full_domain_bound_recomputed(case):
    import importlib.util
    import gurobipy as gp

    p = problem(case)
    row = read(HERE / case / 'phase_bound.json')
    path = Path(row['phase_source'])
    assert hashlib.sha256(path.read_bytes()).hexdigest() == row['phase_source_sha256']
    assert hashlib.sha256((HERE / 'finalize.py').read_bytes()).hexdigest() == row['driver_sha256']
    spec = importlib.util.spec_from_file_location('verified_phase_bound', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    library, _ = enumerate_trip_library(p)
    model, ctx = build_relaxation(p, library)
    try:
        module.augment(p, model, ctx)
        model.Params.TimeLimit = 30
        model.optimize()
        assert model.Status == gp.GRB.OPTIMAL
        assert model.ObjBound == pytest.approx(row['lower_bound'], abs=1e-6)
        assert list(ctx['selectors']) == list(range(1, 8))
        assert model.ObjBound <= read(HERE / case / 'final_summary.json')['best_audited_upper_bound'] + 1e-6
    finally:
        model.dispose()


def test_s05_refinement_original_domain_sources_certificate_and_solution():
    import gurobipy as gp

    p = problem('S05')
    folder = HERE / 'S05/refinement'
    manifest = read(folder / 'manifest.json')
    original = read(folder / 'reduction_certificate.json')
    native = read(folder / 'enhanced/summary.json')
    assert original['original_K'] == manifest['original_K'] == list(range(1, 8))
    assert original['retained_K'] == [2]
    assert read(folder / 'completion.json')['sources_and_inputs_unchanged']
    for path, digest in manifest['input_and_driver_hashes'].items():
        assert hashlib.sha256(Path(path).read_bytes()).hexdigest() == digest
    for path, digest in manifest['source_hashes'].items():
        assert hashlib.sha256((ROOT / path).read_bytes()).hexdigest() == digest
    prior = read(HERE / 'S05/native_result.json')
    prior_audit = audit_solution(p, prior)
    assert prior_audit['valid'] and prior_audit['periodic_replay']['valid']
    assert original['audited_upper_bound'] == pytest.approx(prior_audit['objective'], abs=1e-6)
    library, _ = enumerate_trip_library(p)
    model, ctx = build_relaxation(p, library)
    try:
        model.Params.TimeLimit = 30
        model.addConstr(model.getObjective() <= prior_audit['objective'] + 1e-7)
        count = gp.quicksum(k * selector for k, selector in ctx['selectors'].items())
        for name, expression, sign in [('K_min', count, 1), ('K_max', count, -1),
                                       ('N_min', ctx['pool'], 1), ('N_max', ctx['pool'], -1)]:
            model.setObjective(sign * expression)
            model.optimize()
            assert model.Status == gp.GRB.OPTIMAL
            assert sign * model.ObjVal == pytest.approx(original[name], abs=1e-6)
    finally:
        model.dispose()
    if native['sol_count']:
        result = read(folder / 'enhanced/result.json')
        audit = audit_solution(p, result)
        assert audit['valid'] and audit['periodic_replay']['valid']
        cost = evaluate_comparison_cost(p, result, p.cost_profile.config, audit=audit)
        assert cost['total'] == pytest.approx(native['objective'], abs=1e-6)
        assert result['cycle_batch_count'] == 2 and result['container_pool_size'] == 4
    if native['status'] == 'OPTIMAL':
        assert native['best_bound'] == pytest.approx(native['objective'], abs=1e-6)
        assert native['external_full_domain_optimality_certified']
