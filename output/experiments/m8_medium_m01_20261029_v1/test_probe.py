"""Selected checks for extending the research probe to 20 orders."""
from copy import deepcopy
import time

import gurobipy as gp
import pytest

from probe import HERE, ROOT, CASE, WITNESS, load, master, read, sha
from shared_medium import enumerate_trip_library, build_relaxation
from experiments.runs.research_model8_shared_relaxation import (
    enumerate_trip_library as original_enumerate,
    build_relaxation as original_build,
)
from models.common.cycle import load_instance
from models.common.cycle.audit import audit_solution
from tools.configuration.loader import load_routing_case


def test_medium_witness_decisions_unchanged_and_original_domain_audit():
    p = load()
    manifest = read(HERE / 'manifest.json')
    assert len(p.orders) == 20
    assert list(p.batch_count_candidates) == list(range(1, 21))
    original, lifted = read(WITNESS), read(HERE / 'initial_audited_result.json')
    for key in ('e1_trips', 'e2_trips', 'batches', 'batch_cutoffs', 'batch_boundaries',
                'delivery_assignments', 'recovery_rounds', 'container_pool_size'):
        if key in original:
            assert lifted[key] == original[key]
    audit = audit_solution(p, lifted)
    assert audit['valid'] and audit['periodic_replay']['valid']
    assert audit['objective'] == pytest.approx(manifest['initial_audited_upper_bound'])
    assert all(manifest['physical_file_equality'].values())


def test_small_complete_library_and_master_algebra_unchanged():
    p = load_instance(*load_routing_case(
        ROOT / 'data/formal_benchmark27_uniform_7_20_50_v1/model_8/small/S02/case.json',
        use_pool_config=False))
    library, count = enumerate_trip_library(p)
    expected, expected_count = original_enumerate(p)
    assert library == expected and count == expected_count
    m, _ = build_relaxation(p, library)
    original, _ = original_build(p, expected)
    try:
        assert m.NumVars == original.NumVars
        assert m.NumConstrs == original.NumConstrs
        assert m.NumGenConstrs == original.NumGenConstrs
        for current in (m, original):
            current.Params.TimeLimit = 10
            current.optimize()
            assert current.Status == gp.GRB.OPTIMAL
        assert m.ObjBound == pytest.approx(original.ObjBound, abs=1e-8)
    finally:
        m.dispose()
        original.dispose()


def test_expired_enumeration_never_returns_partial_route_library():
    snapshots = []
    with pytest.raises(TimeoutError, match='Incomplete trip enumeration'):
        enumerate_trip_library(load(), deadline=time.perf_counter()-1, on_progress=snapshots.append)
    assert snapshots and snapshots[-1]['prefixes'] == 4096
