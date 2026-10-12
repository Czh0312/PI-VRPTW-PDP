"""Verify algorithm independence in fresh interpreters, including lazy model APIs."""
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]

SCRIPT = r'''
import importlib.abc
import sys

method, number = sys.argv[1], int(sys.argv[2])
class BlockOtherSolver(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        parts = fullname.split('.')
        if fullname.startswith('models.routing.model_'):
            if method == 'shared' or parts[2] != 'model_'+str(number):
                raise AssertionError('Unexpected model dependency: '+fullname)
            if len(parts) > 3 and parts[3] in ('alns', 'exact') and parts[3] != method:
                raise AssertionError('Unexpected algorithm dependency: '+fullname)
        if method != 'exact' and fullname.startswith('gurobipy'):
            raise AssertionError('Unexpected Gurobi dependency')
        return None

sys.meta_path.insert(0, BlockOtherSolver())
from tools.configuration.loader import load_routing_case
from models.common.cycle import load_instance
from models.common.cycle.audit import audit_solution

if method == 'shared':
    from models.common.cycle.assignment import assigned_problem, input_assignment, lift_result
    from models.common.cycle.precheck import check_necessary_conditions
    from models.common.cycle.state import empty_result
    from models.common.cycle.timing import normalize_schedule
    config, fleet = load_routing_case('config/experiments/examples/single_batch_demo.json')
    problem = load_instance({**config, 'model': 4}, fleet)
    assignment = input_assignment(problem)
    fixed = assigned_problem(problem, assignment)
    assert check_necessary_conditions(fixed)['passed']
    result = lift_result(problem, empty_result(fixed, 'manual'), assignment)
    assert not audit_solution(problem, result)['valid']
    assert not any(name.startswith('models.routing.model_') for name in sys.modules)
else:
    from models.routing.service import solve_routing
    if number <= 5:
        case = 'config/experiments/examples/multibatch_demo.json' if number == 5 else 'config/experiments/examples/single_batch_demo.json'
        config, fleet = load_routing_case(case)
        problem = load_instance({**config, 'model': number}, fleet)
    elif number == 6:
        from tests.routing.test_model6_uniform import uniform_fixture
        problem = uniform_fixture()
    else:
        from tests.routing.test_model78_calendar import calendar_fixture
        problem = calendar_fixture(number)
    result = solve_routing(model=number, method=method, problem=problem,
                           exact_time=10, alns_time=.3, alns_iter=200, seed=42)
    assert result['solver'] == method
    own = f'models.routing.model_{number}.{method}'
    assert result['model_module'] == own
    assert result['solver_module'].startswith(own+'.')
    assert audit_solution(problem, result)['valid']
    assert result['audit']['periodic_replay']['valid']
    if method == 'alns':
        assert not result['info']['proven_optimal'] and result['info']['mip_gap'] is None
print('ISOLATED', method, number)
'''



@pytest.mark.parametrize('method', ['exact', 'alns', 'shared'])
def test_solver_runs_without_importing_the_other_algorithm(method):
    if method == 'exact':
        gp = pytest.importorskip('gurobipy')
        try:
            model = gp.Model()
            model.dispose()
        except gp.GurobiError as exc:
            pytest.skip(f'Gurobi unavailable: {exc}')
    for number in ([0] if method == 'shared' else range(1, 9)):
        completed = subprocess.run([sys.executable, '-c', SCRIPT, method, str(number)], cwd=ROOT,
                                   capture_output=True, text=True, timeout=60)
        assert completed.returncode == 0, completed.stdout+'\n'+completed.stderr
        assert 'ISOLATED '+method in completed.stdout
