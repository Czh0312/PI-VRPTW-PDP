"""Independent validation of the three long-budget complete M8 experiments."""
import hashlib
import importlib.util
import json
from pathlib import Path
import zipfile

import pytest

from experiments.runs.research_model8_shared_relaxation import build_relaxation, enumerate_trip_library
from models.common.cycle import load_instance
from models.common.cycle.audit import audit_solution
from models.cost.comparison import evaluate_comparison_cost
from tools.configuration.loader import load_routing_case

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
CASES = ['S03', 'S08', 'S09']


def read(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def problem(case):
    return load_instance(*load_routing_case(
        ROOT / f'data/formal_benchmark27_uniform_7_20_50_v1/model_8/small/{case}/case.json',
        use_pool_config=False,
    ))


@pytest.mark.parametrize('case', CASES)
def test_frozen_inputs_sources_and_zip_snapshot(case):
    folder = HERE / case
    manifest = read(folder / 'manifest.json')
    expected = dict(manifest['input_and_driver_hashes'])
    expected.update({str((ROOT / path).resolve()): digest for path, digest in manifest['source_hashes'].items()})
    for path, digest in expected.items():
        assert hashlib.sha256(Path(path).read_bytes()).hexdigest() == digest
    archive = folder / 'frozen_sources_inputs.zip'
    assert hashlib.sha256(archive.read_bytes()).hexdigest() == manifest['frozen_archive_sha256']
    with zipfile.ZipFile(archive) as snapshot:
        for path, digest in expected.items():
            archived = snapshot.read(Path(path).relative_to(ROOT).as_posix())
            assert hashlib.sha256(archived).hexdigest() == digest


@pytest.mark.parametrize('case', CASES)
def test_best_complete_witness_on_original_domain_cost_and_two_cycles(case):
    p = problem(case)
    folder = HERE / case
    result = read(folder / 'best_audited_result.json')
    summary = read(folder / 'summary.json')
    audit = audit_solution(p, result)
    assert audit['valid'], audit.get('errors')
    assert audit['periodic_replay']['valid']
    cost = evaluate_comparison_cost(p, result, p.cost_profile.config, audit=audit)
    assert cost['total'] == pytest.approx(summary['best_audited_upper_bound'], abs=1e-6)
    assert summary['selected_K'] == result['cycle_batch_count']
    assert summary['selected_pool'] == result['container_pool_size']
    assert summary['best_audited_upper_bound'] <= summary['prior_upper_bound'] + 1e-6
    assert summary['global_lower_bound'] <= cost['total'] + 1e-6


@pytest.mark.parametrize('case', CASES)
def test_native_status_all_K_scope_and_original_clock(case):
    p = problem(case)
    folder = HERE / case
    native = read(folder / 'native_summary.json')
    summary = read(folder / 'summary.json')
    manifest = read(folder / 'manifest.json')
    assert native['status'] == summary['native_status']
    assert native['original_K'] == manifest['original_K'] == list(range(1, 8))
    assert manifest['optimization_limit_seconds'] == native['optimization_limit_seconds'] == 3600
    assert manifest['previous_search_tree_reused'] is False
    assert native['mip_start_supplied'] is False and native['route_hints_supplied'] is False
    if native['sol_count']:
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
        assert native['best_bound'] == pytest.approx(native['objective'], abs=1e-6)
        assert summary['external_full_domain_optimality_certified']


@pytest.mark.parametrize('case', CASES)
def test_recompute_original_K_N_extrema_and_phase_lower_bound(case):
    import gurobipy as gp

    p = problem(case)
    folder = HERE / case
    manifest = read(folder / 'manifest.json')
    certificate = read(folder / 'reduction_certificate.json')
    witness = read(Path(manifest['witness_path']))
    audit = audit_solution(p, witness)
    assert audit['valid'] and audit['periodic_replay']['valid']
    assert audit['objective'] == pytest.approx(certificate['audited_upper_bound'], abs=1e-6)
    library, _ = enumerate_trip_library(p)
    model, ctx = build_relaxation(p, library)
    try:
        model.Params.TimeLimit = 30
        model.optimize()
        assert model.Status == gp.GRB.OPTIMAL
        assert model.ObjBound == pytest.approx(certificate['global_lower_bound'], abs=1e-6)
        assert list(ctx['selectors']) == list(range(1, 8))
        model.addConstr(model.getObjective() <= audit['objective'] + 1e-7)
        K = gp.quicksum(k * selector for k, selector in ctx['selectors'].items())
        for name, expression, sign in [('K_min', K, 1), ('K_max', K, -1),
                                       ('N_min', ctx['pool'], 1), ('N_max', ctx['pool'], -1)]:
            model.setObjective(sign * expression)
            model.optimize()
            assert model.Status == gp.GRB.OPTIMAL
            assert sign * model.ObjVal == pytest.approx(certificate[name], abs=1e-6)
    finally:
        model.dispose()
    phase_path = Path(manifest['phase_module'])
    spec = importlib.util.spec_from_file_location('independent_long_phase_bound', phase_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    model, ctx = build_relaxation(p, library)
    try:
        module.augment(p, model, ctx)
        model.Params.TimeLimit = 30
        model.optimize()
        assert model.Status == gp.GRB.OPTIMAL
        assert model.ObjBound == pytest.approx(read(folder / 'phase_bound.json')['lower_bound'], abs=1e-6)
        assert model.ObjBound <= audit['objective'] + 1e-6
    finally:
        model.dispose()


@pytest.mark.parametrize('case', CASES)
def test_global_cost_bounds_and_previous_run_comparison(case):
    folder = HERE / case
    summary = read(folder / 'summary.json')
    native = read(folder / 'native_summary.json')
    manifest = read(folder / 'manifest.json')
    certificate = read(folder / 'solver_certificate.json')
    previous = read(Path(manifest['prior_summary']))
    lower = certificate['global_lower_bound']
    if native['best_bound'] is not None:
        lower = max(lower, min(certificate['audited_upper_bound'], native['best_bound']))
    assert summary['global_lower_bound'] == pytest.approx(lower, abs=1e-6)
    assert summary['prior_upper_bound'] == pytest.approx(previous['best_audited_upper_bound'], abs=1e-6)
    assert summary['prior_lower_bound'] == pytest.approx(previous['global_lower_bound'], abs=1e-6)
    gap = max(0., summary['best_audited_upper_bound']-lower)/abs(summary['best_audited_upper_bound'])
    assert summary['gap_over_upper'] == pytest.approx(gap, abs=1e-8)
    closed = abs(summary['best_audited_upper_bound']-lower) <= 1e-6
    assert summary['external_full_domain_optimality_certified'] == closed
