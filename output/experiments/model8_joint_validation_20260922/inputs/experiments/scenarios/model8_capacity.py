"""Designed mechanism example, NOT a sampled benchmark or empirical evidence.

Two vehicles (weight 100, volume 3). Delivery loads 10,10,80 and 50,45
force the two delivery groups. Early pickup windows at P/T, late pickup at H,
plus R's delivery deadline force B to detour to P/T without a handoff.
A can carry P's box to H, hand it to B before B leaves for R, then pick up H/Q.
"""
from models.routing.common.domain.instance import ProblemInstance
from models.routing.common.domain.node import Depot, Satellite, Customer


def make_capacity_example(volume_cap=3.0):
    nodes = {'D0': Depot('D0', -1, 0), 'S0': Satellite('S0', 0, 0)}
    specs = [
        ('P', 10, 10, 8, (14, 16), (16, 18)),
        ('T', 20, 10, 8, (27, 29), (30, 32)),
        ('Q', 20, -10, 78, (65, 70), (75, 80)),
        ('H', 10, 0, 48, (45, 47), (50, 55)),
        ('R', -10, 0, 43, (68, 69), (75, 80)),
    ]
    for n, x, y, demand, td, tp in specs:
        nodes[n] = Customer(n, x, y, demand, 1, td, tp, assigned_sat='S0')
    p = ProblemInstance(nodes, {
        'E1_vehicle_count': 1, 'E1_Qk': 500, 'E1_Ck': 20,
        'vehicles_per_satellite': 2, 'E2_Qv': 100, 'E2_Cv': volume_cap,
        'container_weight': 2, 'container_volume': 1, 'speed': 1})
    p.aggregate_demands_from_pre_assignment()
    return p


def reference_routes():
    return ([['D0', 'S0d', 'S0p', 'D0']], {'S0': [
        ['S0', 'Pd', 'Td', 'Tp', 'Hp', 'Qd', 'Qp', 'S0'],
        ['S0', 'Pp', 'Hd', 'Rd', 'Rp', 'S0']]})
