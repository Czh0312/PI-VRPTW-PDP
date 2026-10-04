"""Atomic exchange moves preserve pickup/delivery coverage and feasibility."""
import json
import time

from tests.routing.baseline_helpers import ROOT, load_problem, as_solution
from models.routing.alns.route_exchange import improve_route_exchanges
from models.routing.baselines.alns.model_4 import Evaluator
from models.routing.common.solution_audit import audit_exact_solution


def fixture():
    fleet = json.loads((ROOT/'config/routing/validation/zhou_small.json').read_text())
    problem = load_problem(ROOT/'data/zhou_later_pickup/small/standard_same_day/Set1_12-2-4.csv',fleet)
    e1 = [['D0','S1d','S1p','D0']]
    e2 = {'S0': [], 'S1': [
        ['S1','C2d','C9d','C10d','C10p','C2p','C9p','C3p','C6p','S1'],
        ['S1','C0d','C11d','C1d','C1p','C11p','C0p','C7p','S1'],
        ['S1','C7d','C3d','C6d','C4d','C4p','S1'],
        ['S1','C5d','C8d','C8p','C5p','S1']]}
    return problem, as_solution(e1,e2)


def test_exchange_escapes_saved_local_optimum_without_mutating_input():
    problem, solution = fixture()
    evaluator = Evaluator(problem, vehicles_per_satellite=4)
    before = [[list(r.nodes) for r in routes] for routes in solution.e2_routes.values()]
    improved, count = improve_route_exchanges(solution,evaluator,time.time()+2)
    assert count > 0
    assert abs(improved.cost-383.52188088551407) < 1e-6
    assert before == [[list(r.nodes) for r in routes] for routes in solution.e2_routes.values()]
    audit = audit_exact_solution(problem,4,[r.full_path() for r in improved.e1_routes],
        {s:[r.full_path() for r in routes] for s,routes in improved.e2_routes.items()},
        assignment=evaluator.assignment(improved),objective=improved.cost)
    assert audit['valid']
    assert evaluator._active_assignment == evaluator.assignment(improved)


def test_expired_deadline_returns_unchanged_feasible_incumbent():
    problem, solution = fixture()
    evaluator = Evaluator(problem, vehicles_per_satellite=4)
    result, count = improve_route_exchanges(solution,evaluator,time.time()-1)
    assert count == 0
    assert abs(result.cost-398.2751512239789) < 1e-6
    assert evaluator.is_feasible(result)
