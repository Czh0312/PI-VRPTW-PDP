"""Small fixed-assignment fixture used by single-batch tests."""
from models.common.cycle import MultiBatchInstance, MultiBatchPolicy, Order
from models.common.domain.instance import ProblemInstance
from models.common.domain.node import Customer, Depot, Satellite


def policy_config():
    return dict(policy_version='multi_batch_cutoff_v1', cycle_start=0, cycle_end=125,
                batches=[dict(id='b1', delivery_duration=30, return_duration=30),
                         dict(id='b2', delivery_duration=30, return_duration=30)],
                container_pool_size=1, cleaning_duration=5, e2_max_trips_per_vehicle=2, time_tick_seconds=60)


def fixture(pool=1, cleaning=5, one_batch=True, zero=False, count=1, **changes):
    cfg = policy_config()
    cfg.update(container_pool_size=pool, cleaning_duration=cleaning, **changes)
    if one_batch:
        cfg.update(batches=cfg['batches'][:1], cycle_end=65)
    p = MultiBatchPolicy.from_config(cfg)
    nodes = {'D': Depot('D', 0, 0), 'S': Satellite('S', 0 if zero else 10, 0),
             'C': Customer('C', 0 if zero else 11, 0, 0, 0, (0, 125), (0, 125), 'S')}
    fleet = dict(E1_vehicle_count=1, vehicles_per_satellite=1, E1_Qk=500, E1_Ck=500,
                 E2_Qv=500, E2_Cv=500, container_weight=1, container_volume=1)
    orders = [Order('o1', 'C', 'S', 'b1', count, 1, (0, 25), (0, 40), 0)]
    if not one_batch:
        orders.append(Order('o2', 'C', 'S', 'b2', count, 1, (60, 85), (60, 89), 0))
    return MultiBatchInstance(ProblemInstance(nodes, fleet), orders, p)
