# -*- coding: utf-8 -*-
import tempfile
import unittest
from pathlib import Path

from models.routing.common.domain.instance import ProblemInstance
from models.routing.common.domain.node import Depot, Satellite
from models.routing.common.data_loader import parse_dataset
from models.routing.common.matrix_loader import load_square_matrix
from models.routing.common.task_builder import build_tasks_for_model


FLEET = {
    "E1_vehicle_count": 1,
    "E1_Qk": 100.0,
    "E1_Ck": 100.0,
    "vehicles_per_satellite": 1,
    "E2_Qv": 100.0,
    "E2_Cv": 100.0,
    "container_weight": 2.0,
    "container_volume": 1.0,
    "speed": 1.0,
}


class RealCaseSupportTests(unittest.TestCase):
    def test_aoi_aggregated_container_counts_drive_capacity(self):
        content = (
            "node_id,node_type,x,y,delivery_demand,delivery_ready,delivery_due,"
            "service_time,pickup_ready,pickup_due,assigned_sat,"
            "delivery_container_count,pickup_container_count\n"
            "D0,d,0,0,0,0,999,0,0,999,,,\n"
            "S0,s,1,0,0,0,999,0,0,999,,,\n"
            "C0,c,2,0,10,0,500,5,600,900,S0,3,2\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "case.csv"
            path.write_text(content, encoding="utf-8")
            nodes, _ = parse_dataset(str(path))

        problem = ProblemInstance(nodes, FLEET)
        problem.aggregate_demands_from_pre_assignment()
        satellite = problem.satellites[0]
        self.assertEqual(satellite.delivery_weight, 16.0)
        self.assertEqual(satellite.delivery_volume, 3.0)
        self.assertEqual(satellite.pickup_weight, 4.0)
        self.assertEqual(satellite.pickup_volume, 2.0)

        e1_tasks, e2_by_sat = build_tasks_for_model(problem, 3)
        e2 = {task.id: task for task in e2_by_sat["S0"]}
        self.assertEqual(e2["C0d"].w, 16.0)
        self.assertEqual(e2["C0d"].v, 3.0)
        self.assertEqual(e2["C0p"].w, 4.0)
        self.assertEqual(e2["C0p"].v, 2.0)
        self.assertEqual({task.id for task in e1_tasks}, {"S0d", "S0p"})

        # M4 constructs these tasks dynamically from its free assignment.
        m4_e1, m4_e2 = build_tasks_for_model(problem, 4)
        self.assertEqual(m4_e1, [])
        self.assertEqual(m4_e2, {"S0": []})

    def test_labelled_asymmetric_road_matrices_override_euclidean_distance(self):
        content = (
            "node_id,D0,S0\n"
            "D0,0,7\n"
            "S0,9,0\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "matrix.csv"
            path.write_text(content, encoding="utf-8")
            matrix = load_square_matrix(path)

        dataset = {
            "D0": Depot("D0", 0, 0),
            "S0": Satellite("S0", 1, 0),
        }
        problem = ProblemInstance(
            dataset, FLEET, distance_matrix=matrix, travel_time_matrix=matrix
        )
        self.assertEqual(problem.distance("D0", "S0"), 7.0)
        self.assertEqual(problem.distance("S0", "D0"), 9.0)
        self.assertEqual(problem.travel_time("D0", "S0"), 7.0)


if __name__ == "__main__":
    unittest.main()
