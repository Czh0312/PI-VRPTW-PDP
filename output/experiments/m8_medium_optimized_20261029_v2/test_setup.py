"""Check variable-pool refinement against independent complete MILPs."""
from types import SimpleNamespace

import pytest

from study import HERE, ROOT, read, sha
from decomposition_medium import solve
from experiments.runs.model8_trip_library import enumerate_trip_library
from models.common.cycle.audit import audit_solution
from models.routing.model_8.exact import solve_exact
from tests.routing.test_model8_physical_bounds import instance


@pytest.mark.parametrize('kind', ['ordinary', 'late_release', 'multiple_satellites'])
def test_pool_indicators_and_pattern_projection_preserve_full_optimum(kind):
    p = instance(kind)
    witness = solve_exact(p, time_limit=20, milp_options=dict(formulation='compact'))
    assert witness['info']['proven_optimal']
    library, _ = enumerate_trip_library(p)
    phase = SimpleNamespace(augment=lambda *args: None)
    best, info = solve(p, witness, list(p.batch_count_candidates), [p.pool_min, p.pool_max],
                       0., library, phase, 20, 42)
    audit = audit_solution(p, best)
    assert audit['valid'] and audit['periodic_replay']['valid']
    assert info['global_optimality_certified'], info
    assert info['global_lower_bound'] == pytest.approx(witness['info']['objective'], abs=1e-6)
    assert audit['objective'] == pytest.approx(witness['info']['objective'], abs=1e-6)
    assert info['retained_N'] == [p.pool_min, p.pool_max]
    for event in info['events']:
        assert p.pool_min <= event['N'] <= p.pool_max
