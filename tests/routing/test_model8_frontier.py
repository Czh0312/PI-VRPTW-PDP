"""Stock frontier cuts and vehicle renaming preserve complete M8 optima."""
import time

import pytest

from experiments.runs.model8_compact_prefix import build_prefix,extract_prefix,transport_certificate,supply_start
from experiments.runs.model8_frontier import stock_frontier,normalize_vehicle_ids,add_frontier_cuts
from models.routing.model_8.exact import solve_exact
from models.common.solver_config import read_milp_info
from tests.routing.test_model8_compact_prefix import common_clock


@pytest.mark.parametrize('kind',['ordinary','late_release','multiple_satellites'])
def test_frontier_and_symmetry_preserve_complete_optimum(kind):
    p = common_clock(kind)
    original = solve_exact(p,time_limit=20,milp_options=dict(formulation='compact'))
    assert original['info']['proven_optimal']
    cert = transport_certificate(p,original['info']['objective'])
    frontier = stock_frontier(p,cert,deadline=time.perf_counter()+10,per_pair_seconds=2)
    assert (original['cycle_batch_count'],original['container_pool_size']) in {
        (row['K'],row['N']) for row in frontier['retained_pairs']}
    cert['retained_K'] = frontier['retained_K']
    m,ctx = build_prefix(p,frontier['retained_K'],certificate=cert,vehicle_symmetry=True)
    try:
        cuts = add_frontier_cuts(p,m,ctx,frontier,cert)
        normalized = normalize_vehicle_ids(p,original)
        start = supply_start(p,m,ctx,normalized)
        assert not start['fixes_decisions']
        m.Params.TimeLimit = 20
        m.optimize()
        assert m.Status == 2
        assert m.ObjVal == pytest.approx(original['info']['objective'],abs=1e-6)
        result = extract_prefix(p,m,ctx,read_milp_info(m,proof_scope='test_frontier',build_time=0))
        assert result['audit']['valid'] and result['audit']['periodic_replay']['valid']
    finally:
        m.dispose()


def test_frontier_timeout_keeps_unresolved_pairs():
    p = common_clock('ordinary')
    original = solve_exact(p,time_limit=20,milp_options=dict(formulation='compact'))
    cert = transport_certificate(p,original['info']['objective'])
    frontier = stock_frontier(p,cert,deadline=time.perf_counter()-1)
    assert frontier['retained_pairs']
    assert all(row['native_status'] is None for row in frontier['pairs'])
    assert (original['cycle_batch_count'],original['container_pool_size']) in {
        (row['K'],row['N']) for row in frontier['retained_pairs']}
