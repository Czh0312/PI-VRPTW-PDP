"""Small fixtures for retained baseline algorithms, independent of old runners."""
from pathlib import Path
from models.routing.common.data_loader import parse_dataset
from models.routing.common.domain.instance import ProblemInstance
from models.routing.alns.types import Route, Solution

ROOT = Path(__file__).resolve().parents[2]


def load_problem(path, fleet):
    nodes, _ = parse_dataset(str(path))
    problem = ProblemInstance(nodes, fleet, speed=fleet.get("speed", 1.0))
    problem.aggregate_demands_from_pre_assignment()
    return problem


def as_solution(e1_paths, e2_paths):
    sol = Solution()
    sol.e1_routes = [Route(path[0], list(path[1:-1])) for path in e1_paths]
    sol.e2_routes = {
        sat: [Route(path[0], list(path[1:-1])) for path in paths]
        for sat, paths in e2_paths.items()
    }
    return sol
