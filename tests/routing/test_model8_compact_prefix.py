"""Optional batch suffixes must preserve the complete original M8 domain."""
from types import SimpleNamespace

import gurobipy as gp
import pytest

from experiments.runs.model8_compact_prefix import build_prefix, extract_prefix, transport_certificate, supply_start
from models.common.cycle.calendar import CalendarCycleInstance
from models.common.solver_config import read_milp_info
from models.routing.model_8.exact import solve_exact
from tests.routing.test_model8_physical_bounds import instance


def common_clock(kind):
    p = instance(kind)
    return CalendarCycleInstance(p.network,p.orders,p.operating_policy,
        dict(batch_count_candidates=[1,2]),p.cost_profile.config,model=8)


@pytest.mark.parametrize('kind',['ordinary','late_release','multiple_satellites'])
@pytest.mark.parametrize('k',[1,2])
def test_prefix_fixed_count_equals_complete_native_model(kind,k):
    p = common_clock(kind)
    original = CalendarCycleInstance(p.network,p.orders,p.operating_policy,
        dict(batch_count_candidates=[k]),p.cost_profile.config,model=8)
    exact = solve_exact(original,time_limit=20,milp_options=dict(formulation='compact'))
    assert exact['info']['proven_optimal'] or exact['info']['proven_infeasible']
    m,ctx = build_prefix(p)
    try:
        for count,variable in ctx['prefix_selectors'].items():
            variable.LB = variable.UB = int(count == k)
        m.Params.TimeLimit = 20
        m.optimize()
        if exact['info']['proven_infeasible']:
            assert m.Status == gp.GRB.INFEASIBLE
        else:
            assert m.Status == gp.GRB.OPTIMAL
            assert m.ObjVal == pytest.approx(exact['info']['objective'],abs=1e-6)
            result = extract_prefix(p,m,ctx,read_milp_info(m,proof_scope='test_prefix',build_time=0))
            assert result['audit']['valid'] and result['audit']['periodic_replay']['valid']
            assert result['cycle_batch_count'] == k
    finally:
        m.dispose()


@pytest.mark.parametrize('kind',['ordinary','late_release','multiple_satellites'])
def test_full_prefix_optimum_certificate_and_audited_mip_start(kind):
    p = common_clock(kind)
    exact = solve_exact(p,time_limit=20,milp_options=dict(formulation='compact'))
    assert exact['info']['proven_optimal']
    cert = transport_certificate(p,exact['info']['objective'])
    assert min(cert['per_K_lower_bound'].values()) <= exact['info']['objective']+1e-6
    assert exact['cycle_batch_count'] in cert['retained_K']
    m,ctx = build_prefix(p,cert['retained_K'],certificate=cert)
    try:
        starting = supply_start(p,m,ctx,exact)
        assert starting['fixes_decisions'] is False
        assert starting['supplied_physical_trips'] == len(exact['e1_trips'])+len(exact['e2_trips'])
        m.Params.TimeLimit = 20
        m.optimize()
        assert m.Status == gp.GRB.OPTIMAL
        assert m.ObjVal == pytest.approx(exact['info']['objective'],abs=1e-6)
        result = extract_prefix(p,m,ctx,read_milp_info(m,proof_scope='test_prefix',build_time=0))
        assert result['audit']['valid'] and result['audit']['periodic_replay']['valid']
    finally:
        m.dispose()


def test_uncertified_count_restriction_and_mixed_clocks_are_rejected():
    p = common_clock('ordinary')
    with pytest.raises(ValueError,match='certificate'):
        build_prefix(p,[2])
    mixed = instance('ordinary')
    if any(mixed.for_count(k).operating_policy.time_tick_subdivision != mixed.operating_policy.time_tick_subdivision
           for k in mixed.batch_count_candidates):
        with pytest.raises(ValueError,match='common original clock'):
            build_prefix(mixed)
