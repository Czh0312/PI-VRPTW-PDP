"""Live full-domain pricing survives a slice, freezes proofs, and is disposed."""
from copy import deepcopy
from types import SimpleNamespace
import time

import pytest

from tests.routing.test_multibatch_hybrid import capacity_problem
from tests.routing.test_multibatch_route_cg import enumerate_trips, enumerated_lp
from models.routing.solvers.exact.multibatch_route_pricing import route_context


@pytest.mark.parametrize('capacity_rows', [False,True])
def test_same_live_pricer_continues_after_interrupted_phase_and_snapshots_do_not_mutate(monkeypatch,capacity_rows):
    pytest.importorskip('gurobipy')
    import models.routing.solvers.exact.multibatch_route_cg as cg
    real_clock=time.perf_counter;forced=[None];deadline=real_clock()+3
    monkeypatch.setattr(cg,'time',SimpleNamespace(perf_counter=lambda:forced[0] if forced[0] is not None else real_clock()))
    original=cg.RoutePricer;created=[];disposed=[]
    def build(*a,**kw):
        obj=original(*a,**kw);created.append(obj)
        price,dispose=obj.price,obj.dispose
        def bounded(*a,**kw):
            result=price(*a,**kw)
            if forced[0] is None and len(created)==1 and not getattr(obj,'sliced',False):
                obj.sliced=True;forced[0]=deadline+1
            return result
        def close():disposed.append(obj);dispose()
        obj.price,obj.dispose=bounded,close
        return obj
    monkeypatch.setattr(cg,'RoutePricer',build)
    p=capacity_problem();session=cg.SiteRouteCoverSession(p,route_context(p,'S'),
        heuristic_pricing=False,batch_pricing=True,dual_repair=True,delivery_count_cuts=capacity_rows)
    try:
        first=session.run(deadline=deadline);saved=deepcopy(first)
        assert session.active and len(created)==1 and not disposed
        forced[0]=None
        second=session.run(deadline=real_clock()+3)
        assert second['lp_converged'] and second['continuation'] and not session.active
        assert first==saved and len(created)==1 and len(disposed)==1
        oracle=4 if capacity_rows else enumerated_lp(p,'S',enumerate_trips(p,'S'))
        assert second['bound']==pytest.approx(oracle,abs=1e-4)
        assert session.run(deadline=0)['bound']==second['bound']
    finally:session.dispose()
    with pytest.raises(RuntimeError,match='disposed'):session.run(deadline=0)


def test_expired_empty_session_returns_safe_bound_and_releases_resources():
    from models.routing.solvers.exact.multibatch_route_cg import SiteRouteCoverSession
    p=capacity_problem();s=SiteRouteCoverSession(p,route_context(p,'S'))
    try:
        row=s.run(deadline=0)
        assert row['bound']==0 and row['status']=='TIME_LIMIT_BUILD' and not s.active
    finally:s.dispose()


def test_public_hybrid_disposes_all_live_sessions_when_a_stage_raises(monkeypatch):
    import models.routing.solvers.exact.multibatch_hybrid as hybrid
    closed=[]
    def fail(p,**options):
        options['_route_sessions']['S']=SimpleNamespace(dispose=lambda:closed.append('S'))
        raise RuntimeError('failed stage')
    monkeypatch.setattr(hybrid,'_solve_hybrid',fail)
    with pytest.raises(RuntimeError,match='failed stage'):hybrid.solve_hybrid(None)
    assert closed==['S']


def test_actual_public_resume_pipeline_keeps_full_audit_and_releases_owned_sessions(monkeypatch):
    pytest.importorskip('gurobipy')
    import models.routing.service as service
    import models.routing.solvers.exact.multibatch_route_cg as cg
    from models.routing.solvers.exact.multibatch_hybrid import solve_hybrid
    from models.routing.common.cycle.audit import audit_solution
    from tests.routing.test_multibatch_cycle import fixture,hand_solution
    p=fixture(pool=2);initial=hand_solution(p);owned=[]
    original=cg.SiteRouteCoverSession
    def build(*a,**kw):
        session=original(*a,**kw);owned.append(session);return session
    monkeypatch.setattr(cg,'SiteRouteCoverSession',build)
    monkeypatch.setattr(service,'solve_routing',lambda **kw:deepcopy(initial))
    out=solve_hybrid(p,time_limit=2,primal_time_limit=0,cover_time_limit=1,
                     conditional_time_limit=0,adaptive_pricing_mode='resume')
    assert owned and all(s._disposed for s in owned)
    assert out['target_reached'] and out['bound']<=out['upper_bound']
    assert audit_solution(p,out['result'])['valid'] and not out['proven_optimal']
