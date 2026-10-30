"""Reload the original S04 input and independently verify this experiment."""
import hashlib
import json
from pathlib import Path

import pytest

from experiments.runs.research_model8_shared_relaxation import (
    build_relaxation, enumerate_trip_library,
)
from models.common.cycle import load_instance
from models.common.cycle.audit import audit_solution
from models.cost.comparison import evaluate_comparison_cost
from tools.configuration.loader import load_routing_case

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]


def read(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def problem():
    return load_instance(*load_routing_case(
        ROOT / 'data/formal_benchmark27_uniform_7_20_50_v1/model_8/small/S04/case.json',
        use_pool_config=False,
    ))


def test_frozen_inputs_sources_and_original_proof_domain():
    manifest = read(HERE / 'manifest.json')
    completion = read(HERE / 'completion.json')
    assert manifest['original_K'] == completion['original_K'] == list(range(1, 8))
    assert completion['sources_and_inputs_unchanged']
    for path, digest in manifest['input_and_driver_hashes'].items():
        assert hashlib.sha256(Path(path).read_bytes()).hexdigest() == digest
    for path, digest in manifest['source_hashes'].items():
        assert hashlib.sha256((ROOT / path).read_bytes()).hexdigest() == digest


def test_solution_cost_and_two_cycles_against_original_full_domain():
    p = problem()
    result = read(HERE / 'enhanced/result.json')
    summary = read(HERE / 'enhanced/summary.json')
    certificate = read(HERE / 'reduction_certificate.json')
    audit = audit_solution(p, result)
    assert audit['valid'], audit.get('errors')
    assert audit['periodic_replay']['valid']
    cost = evaluate_comparison_cost(p, result, p.cost_profile.config, audit=audit)
    assert cost['total'] == pytest.approx(summary['objective'], abs=1e-6)
    assert summary['objective'] <= certificate['audited_upper_bound'] + 1e-6
    assert summary['best_bound'] <= summary['objective'] + 1e-6
    assert summary['native_solved_K'] == [2]
    assert result['container_pool_size'] == 4
    if summary['external_full_domain_optimality_certified']:
        assert summary['status'] == 'OPTIMAL'
        assert summary['best_bound'] == pytest.approx(summary['objective'], abs=1e-5)
        assert summary['proven_optimal']
    else:
        assert summary['status'] != 'OPTIMAL'
    log = (HERE / 'enhanced/gurobi.log').read_text(encoding='utf-8')
    assert ('Optimal solution found' in log) == (summary['status'] == 'OPTIMAL')


def test_recompute_full_K_lower_bound_and_reduction_with_audited_witness():
    import gurobipy as gp

    p = problem()
    manifest = read(HERE / 'manifest.json')
    original = read(HERE / 'reduction_certificate.json')
    witness = read(Path(manifest['reused_witness']))
    audit = audit_solution(p, witness)
    assert audit['valid'] and audit['periodic_replay']['valid']
    assert audit['objective'] == pytest.approx(original['audited_upper_bound'], abs=1e-6)
    library, _ = enumerate_trip_library(p)
    model, ctx = build_relaxation(p, library)
    try:
        model.Params.TimeLimit = 30
        model.optimize()
        assert model.Status == gp.GRB.OPTIMAL
        assert model.ObjBound == pytest.approx(original['global_lower_bound'], abs=1e-6)
        assert list(ctx['selectors']) == list(p.batch_count_candidates)
        model.addConstr(model.getObjective() <= audit['objective'] + 1e-7)
        K = gp.quicksum(k * v for k, v in ctx['selectors'].items())
        for name, expr, sign in [('K_min', K, 1), ('K_max', K, -1),
                                 ('N_min', ctx['pool'], 1), ('N_max', ctx['pool'], -1)]:
            model.setObjective(sign * expr)
            model.optimize()
            assert model.Status == gp.GRB.OPTIMAL
            assert sign * model.ObjVal == pytest.approx(original[name], abs=1e-6)
    finally:
        model.dispose()
