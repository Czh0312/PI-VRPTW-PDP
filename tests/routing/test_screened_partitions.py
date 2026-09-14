"""An excluded but unresolved assignment must remain in the global bound."""
import pytest
pytest.importorskip('gurobipy')
from models.routing.variants.model_7 import screened_partitions as module
from tests.routing.test_model7_exact import _problem


@pytest.mark.parametrize('sub_status,expected', [('TIME_LIMIT',False),('CUTOFF',True)])
def test_unresolved_partition_blocks_master_certificate(monkeypatch,sub_status,expected):
    p = _problem()
    assignment = {c.id:c.assigned_sat for c in p.customers}
    calls = [0]
    def master(*a,**kw):
        calls[0] += 1
        if calls[0] == 1:
            return None,None,assignment,{'status_code':2,'best_bound':5.0}
        assert kw['excluded_assignments'] == [assignment]
        return None,None,None,{'status_code':6,'best_bound':10.0}
    monkeypatch.setattr(module,'solve_commodity_flow',master)
    monkeypatch.setattr(module,'solve_m6',lambda *a,**kw:
        (None,None,{'status':sub_status,'best_bound':7.0,'objective':None,'proven_optimal':False}))
    *_,info = module.solve_screened_partitions(p,2,TFp=9999,remaining_time=lambda:5.0,
        incumbent_routes=([['D0','S0d','D0']],{}),incumbent_assignment=assignment,
        incumbent_objective=10.0,global_bound=4.0,gurobi_params={})
    assert info['assignment_master_closed']
    assert info['proven_optimal'] is expected
    assert info['best_bound'] == (10.0 if expected else 7.0)


def test_reuses_relaxation_assignment_before_rebuilding_master(monkeypatch):
    p = _problem()
    assignment = {c.id:c.assigned_sat for c in p.customers}
    calls = []
    def sub(*args,**kwargs):
        calls.append(kwargs['time_limit'])
        return None,None,dict(status='CUTOFF',best_bound=10.,objective=None,proven_optimal=False)
    def master(*args,**kwargs):
        assert len(calls)==1
        assert kwargs['excluded_assignments']==[assignment]
        return None,None,None,dict(status_code=6,best_bound=10.)
    monkeypatch.setattr(module,'solve_m6',sub)
    monkeypatch.setattr(module,'solve_commodity_flow',master)
    *_,info = module.solve_screened_partitions(p,2,TFp=9999,remaining_time=lambda:5.,
        incumbent_routes=([['D0','S0d','D0']],{}),incumbent_assignment=assignment,
        incumbent_objective=10.,global_bound=5.,gurobi_params={},initial_assignment=assignment)
    assert info['proven_optimal']
    assert info['assignment_master_calls']==1


def test_last_unresolved_partition_receives_all_remaining_retry_budget(monkeypatch):
    p = _problem()
    a = {c.id:c.assigned_sat for c in p.customers}
    b = {c:('S1' if s=='S0' else 'S0') for c,s in a.items()}
    remaining = [20.]
    limits = []
    masters = iter([(None,None,a,dict(status_code=2,best_bound=5.)),
                    (None,None,b,dict(status_code=2,best_bound=5.)),
                    (None,None,None,dict(status_code=6,best_bound=10.))])
    def sub(*args,**kwargs):
        limits.append(kwargs['time_limit'])
        remaining[0] -= kwargs['time_limit']
        return None,None,dict(status='TIME_LIMIT',best_bound=5.,objective=None,proven_optimal=False)
    monkeypatch.setattr(module,'solve_m6',sub)
    monkeypatch.setattr(module,'solve_commodity_flow',lambda *a,**kw:next(masters))
    *_,info = module.solve_screened_partitions(p,2,TFp=9999,remaining_time=lambda:remaining[0],
        incumbent_routes=([['D0','S0d','D0']],{}),incumbent_assignment=a,
        incumbent_objective=10.,global_bound=4.,gurobi_params={})
    assert limits==pytest.approx([5.,5.,5.,5.])
    assert remaining[0]==pytest.approx(0.)
    assert not info['proven_optimal']


def test_global_relaxation_route_requires_original_model_revalidation():
    from models.routing.variants.model_7.exact_solver import _global_relaxation_certificate
    result = _global_relaxation_certificate(_problem(),2,9999.,lambda:5.,
        {'Threads':1,'Seed':42},None,False,direct_time_fraction=0.)
    assert result['certified']
    assert result['incumbent_source']=='independently_validated_relaxation_route'
    assert result['direct_info']['route_audit']['valid']
    assert result['objective']==pytest.approx(result['lower_bound'])
