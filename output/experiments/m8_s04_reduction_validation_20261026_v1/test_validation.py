"""Independent reload/replay of S04's complete-model experiment evidence."""
import hashlib
import json
import importlib.util
from pathlib import Path

import pytest

from experiments.runs.research_model8_shared_relaxation import build_relaxation, enumerate_trip_library
from models.common.cycle import load_instance
from models.common.cycle.audit import audit_solution
from models.cost.comparison import evaluate_comparison_cost
from tools.configuration.loader import load_routing_case

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent


def read(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def problem():
    return load_instance(*load_routing_case(ROOT/'data/formal_benchmark27_uniform_7_20_50_v1/model_8/small/S04/case.json', use_pool_config=False))


def test_sources_and_physical_inputs_are_unchanged():
    manifest = read(HERE/'manifest.json')
    assert manifest['K'] == list(range(1, 8))
    for path, digest in manifest['input_and_driver_hashes'].items():
        assert hashlib.sha256(Path(path).read_bytes()).hexdigest() == digest
    for path, digest in manifest['source_hashes'].items():
        assert hashlib.sha256((ROOT/path).read_bytes()).hexdigest() == digest


def test_enhanced_solution_passes_original_full_domain_and_cost_audit():
    p = problem()
    result = read(HERE/'enhanced/result.json')
    summary = read(HERE/'enhanced/summary.json')
    audit = audit_solution(p, result)
    assert audit['valid'] and audit['periodic_replay']['valid']
    cost = evaluate_comparison_cost(p, result, p.cost_profile.config, audit=audit)
    assert cost['total'] == pytest.approx(summary['objective'], abs=1e-6)
    assert summary['objective'] <= read(HERE/'reduction_certificate.json')['audited_upper_bound'] + 1e-6
    assert summary['mip_start_supplied'] is False and summary['route_hints_supplied'] is False
    if summary['external_full_domain_optimality_certified']:
        assert summary['status'] == 'OPTIMAL'
        assert summary['best_bound'] == pytest.approx(summary['objective'], abs=1e-5)


def test_rerun_shared_relaxation_certifies_unique_K_and_pool():
    import gurobipy as gp
    p = problem()
    original = read(HERE/'reduction_certificate.json')
    library, _ = enumerate_trip_library(p)
    model, ctx = build_relaxation(p, library)
    try:
        model.Params.TimeLimit = 20
        model.addConstr(model.getObjective() <= original['audited_upper_bound']+1e-7)
        K = gp.quicksum(k*v for k,v in ctx['selectors'].items())
        for key, expr, sign in [('K_min',K,1), ('K_max',K,-1),
                                ('N_min',ctx['pool'],1), ('N_max',ctx['pool'],-1)]:
            model.setObjective(sign*expr)
            model.optimize()
            assert model.Status == gp.GRB.OPTIMAL
            assert sign*model.ObjVal == pytest.approx(original[key])
    finally:
        model.dispose()


def test_baseline_status_matches_its_solution_and_full_domain_scope():
    row = read(HERE/'baseline/physical_time_summary.json')
    assert row['batch_count_candidates'] == list(range(1, 8))
    assert row['complete_count_domain'] and row['mip_start_supplied'] is False
    if row['sol_count']:
        p = problem()
        result = read(HERE/'baseline/physical_time_result.json')
        audit = audit_solution(p, result)
        assert audit['valid'] and audit['periodic_replay']['valid']
        assert audit['objective'] == pytest.approx(row['objective'], abs=1e-6)
    else:
        assert row['objective'] is None and not row['proven_optimal']


def phase_module():
    spec = importlib.util.spec_from_file_location('s04_phase_bound', HERE/'phase_bound.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_phase_pattern_bounds_are_below_actual_first_echelon_routes():
    p = problem()
    result = read(HERE/'enhanced/result.json')
    patterns = {(r['delivery'],r['pickup']):r['distance'] for r in phase_module().phase_patterns(p)}
    for batch in result['batches']:
        trips = [r for r in result['e1_trips'] if r['batch_id'] == batch['id']]
        delivery = {e['site_id'] for r in trips for e in r['events'] if e['kind'] == 'd'}
        pickup = {e['site_id'] for r in trips for e in r['events'] if e['kind'] == 'p'}
        masks = tuple(sum(1 << p.satellites.index(s) for s in sites) for sites in (delivery,pickup))
        actual = 0.
        for trip in trips:
            nodes = [p.depot]+[e['site_id'] for e in trip['events']]+[p.depot]
            actual += sum(p.distance(a,b) for a,b in zip(nodes,nodes[1:]))
        assert patterns[masks] <= actual+1e-7
    assert read(HERE/'phase_bound.json')['lower_bound'] <= result['audit']['objective']+1e-6


@pytest.mark.parametrize('kind', ['ordinary','late_release','multiple_satellites'])
def test_phase_strengthening_remains_below_micro_exact_optimum(kind):
    from tests.routing.test_model8_physical_bounds import instance
    from models.routing.model_8.exact import solve_exact
    p = instance(kind)
    library,_ = enumerate_trip_library(p)
    model,ctx = build_relaxation(p,library)
    try:
        phase_module().augment(p,model,ctx)
        model.Params.TimeLimit = 15
        model.optimize()
        assert model.Status == 2
        exact = solve_exact(p,time_limit=20,milp_options=dict(formulation='compact'))
        assert exact['info']['proven_optimal']
        assert model.ObjBound <= exact['info']['objective']+1e-6
    finally:
        model.dispose()
