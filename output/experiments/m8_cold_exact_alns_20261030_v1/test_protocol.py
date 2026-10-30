"""Protocol checks; fixtures never load a historical solution."""
import importlib.util
from pathlib import Path
import sys

import pytest

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('cold_start_protocol', HERE/'bench.py')
bench = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bench)


def test_read_guard_blocks_a_historical_result_but_allows_raw_inputs():
    path = bench.ROOT/'data/raw_case.json'
    guard = bench.WorkspaceReadGuard([path])
    guard('open',(str(path),'r',0))
    guard('open',(str(bench.SOURCE/'models/example.py'),'r',0))
    guard('open',(str(HERE/'runs/new/result.json'),'w',1))
    with pytest.raises(PermissionError,match='Cold-start'):
        guard('open',(str(bench.ROOT/'output/old/result.json'),'r',0))
    assert len(guard.blocked) == 1 and len(guard.read_inputs) == 1


def test_gurobi_observer_preserves_a_cold_integer_optimum_and_records_events(monkeypatch):
    import gurobipy as gp
    original = gp.Model
    monkeypatch.setattr(gp,'Model',original)
    events, calls = bench.install_model_observer(gp,lambda:0.)
    with gp.Env(empty=True) as env:
        env.setParam('OutputFlag',0)
        env.start()
        with gp.Model(env=env) as model:
            model.Params.Threads = 1
            model.Params.Seed = 42
            x = model.addVar(vtype=gp.GRB.INTEGER,lb=0,ub=2)
            model.addConstr(x >= 1)
            model.setObjective(x)
            model.optimize()
            assert model.Status == gp.GRB.OPTIMAL and model.ObjVal == 1.
    assert events and calls[0]['NumStart'] == 0 and calls[0]['threads'] == 1


def test_frozen_plan_uses_one_seed_full_domain_and_no_external_start():
    assert bench.SEED == 42 and len(bench.CASES) == 9
    assert bench.EXACT_OPTIONS == dict(strengthening='physical_time', preprocessing=True,
        vehicle_symmetry=True, milp_options=dict(formulation='compact',threads=1))
