"""The auxiliary stock master is only a lower bound on complete M8."""
import pytest

from experiments.runs.model8_stock_relaxation import build_stock_bound
from experiments.runs.model8_compact_prefix import transport_certificate
from models.routing.model_8.exact import solve_exact
from tests.routing.test_model8_compact_prefix import common_clock


@pytest.mark.parametrize('kind',['ordinary','late_release','multiple_satellites'])
def test_optimistic_stock_bound_never_exceeds_complete_optimum(kind):
    p = common_clock(kind)
    original = solve_exact(p,time_limit=20,milp_options=dict(formulation='compact'))
    assert original['info']['proven_optimal']
    certificate = transport_certificate(p,original['info']['objective'])
    m,ctx = build_stock_bound(p,certificate)
    try:
        m.Params.TimeLimit = 20
        m.optimize()
        assert m.Status == 2
        assert m.ObjBound <= original['info']['objective']+1e-6
    finally:
        m.dispose()
