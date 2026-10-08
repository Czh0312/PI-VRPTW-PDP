"""Verify algorithm independence in fresh interpreters, including lazy model APIs."""
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]

SCRIPT = r'''
import importlib.abc
import sys

method = sys.argv[1]
opposite = 'alns' if method == 'exact' else 'exact'
class BlockOtherSolver(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        blocked = fullname.startswith('models.routing.solvers.'+opposite)
        if fullname.startswith('models.routing.variants.model_'):
            parts = fullname.split('.')
            blocked |= len(parts) > 4 and parts[4] == opposite
        if method != 'exact':
            blocked |= fullname.startswith('gurobipy')
        if method == 'shared':
            blocked |= fullname.startswith('models.routing.solvers')
        if blocked:
            raise AssertionError('Unexpected algorithm dependency: '+fullname)
        return None

sys.meta_path.insert(0, BlockOtherSolver())
from tools.configuration.loader import load_routing_case
from models.routing.common.cycle import load_instance
from models.routing.common.cycle.audit import audit_solution

if method == 'shared':
    from models.routing.common.cycle.assignment import assigned_problem, input_assignment, lift_result
    from models.routing.common.cycle.precheck import check_necessary_conditions
    from models.routing.common.cycle.state import empty_result
    from models.routing.common.cycle.timing import normalize_schedule
    config, fleet = load_routing_case('config/experiments/examples/single_batch_demo.json')
    problem = load_instance({**config, 'model': 4}, fleet)
    assignment = input_assignment(problem)
    fixed = assigned_problem(problem, assignment)
    assert check_necessary_conditions(fixed)['passed']
    result = lift_result(problem, empty_result(fixed, 'manual'), assignment)
    assert not audit_solution(problem, result)['valid']  # No routes, correctly rejected.
    assert not any(name.startswith('models.routing.solvers') for name in sys.modules)
else:
    from models.routing.service import solve_routing
    for number in range(1, 6):
        case = 'config/experiments/examples/multibatch_demo.json' if number == 5 else 'config/experiments/examples/single_batch_demo.json'
        config, fleet = load_routing_case(case)
        problem = load_instance({**config, 'model': number}, fleet)
        result = solve_routing(model=number, method=method, problem=problem,
                               exact_time=10, alns_time=.3, alns_iter=200, seed=42)
        assert result['solver'] == method
        backend = 'assignment' if number in (2, 4) else 'solver'
        assert result['solver_module'] == f'models.routing.solvers.{method}.{backend}'
        assert audit_solution(problem, result)['valid']
        assert result['audit']['periodic_replay']['valid']
        if method == 'alns':
            assert not result['info']['proven_optimal'] and result['info']['mip_gap'] is None
    assert not any(name.startswith('models.routing.solvers.'+opposite) for name in sys.modules)
print('ISOLATED', method)
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
    completed = subprocess.run([sys.executable, '-c', SCRIPT, method], cwd=ROOT,
                               capture_output=True, text=True, timeout=60)
    assert completed.returncode == 0, completed.stdout+'\n'+completed.stderr
    assert 'ISOLATED '+method in completed.stdout
