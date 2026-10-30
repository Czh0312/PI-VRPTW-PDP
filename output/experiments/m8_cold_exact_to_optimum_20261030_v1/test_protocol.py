"""Cold-read isolation, observation and no practical stopping target."""
import importlib.util
from pathlib import Path
import sys

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0,str(HERE))
spec = importlib.util.spec_from_file_location('exact_to_optimum_protocol',HERE/'bench.py')
bench = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bench)


def test_no_historical_result_can_be_opened_by_the_worker():
    raw = bench.ROOT/'data/example.json'
    guard = bench.WorkspaceReadGuard([raw])
    guard('open',(str(raw),'r',0))
    with pytest.raises(PermissionError,match='Cold-start'):
        guard('open',(str(bench.ROOT/'output/old/result.json'),'r',0))
    assert guard.read_inputs and len(guard.blocked)==1


def test_observer_does_not_change_a_small_integer_optimum(monkeypatch):
    import gurobipy as gp
    monkeypatch.setattr(gp,'Model',gp.Model)
    fixture_dir=HERE/'protocol_fixture'
    fixture_dir.mkdir(exist_ok=True)
    events,calls,_ = bench.install_observer(gp,fixture_dir,lambda:0.,lambda:0.,'test')
    with gp.Env(empty=True) as env:
        env.setParam('OutputFlag',0)
        env.start()
        with gp.Model(env=env) as model:
            model.Params.Threads=1
            model.Params.Seed=42
            model.Params.MIPGap=0
            model.Params.MIPGapAbs=0
            x=model.addVar(vtype=gp.GRB.INTEGER,lb=0,ub=2)
            model.addConstr(x>=1)
            model.setObjective(x)
            model.optimize()
            assert model.Status==gp.GRB.OPTIMAL and model.ObjVal==1.
    assert calls[0]['NumStart']==0 and events[0]['objective']==1.


def test_unbounded_request_has_no_reference_objective_or_extra_seed():
    assert bench.SEED==42 and bench.UNBOUNDED_REQUEST_SECONDS>=1e12
    assert bench.CASES==[f'S{i:02}' for i in range(1,10)]
    assert bench.OPTIONS==dict(strengthening='physical_time',preprocessing=True,vehicle_symmetry=True,
                               milp_options=dict(formulation='compact',threads=1))
