"""Physical fixtures shared by Model 5 regression tests."""
import pytest

from models.common.cycle.instance import Order
from models.common.cycle.policy import MultiBatchPolicy
from models.common.cycle.joint import JointBatchInstance
from models.common.domain.node import Depot, Satellite, Customer
from models.common.domain.instance import ProblemInstance
from tools.data.build_model6_demo_suite import synthetic_profile


def joint_fixture(pool_rate=5,cleaning=5,late=False,one_batch=False,zero=False,**options):
    policy=dict(policy_version='multi_batch_cutoff_v1',cycle_start=0,cycle_end=130,
                batches=[dict(id='b1',delivery_duration=40,return_duration=20),
                         dict(id='b2',delivery_duration=40,return_duration=20)],
                container_pool_size=2,cleaning_duration=cleaning,e2_max_trips_per_vehicle=4,time_tick_seconds=60)
    if one_batch:
        policy['batches']=policy['batches'][:1]
    p=MultiBatchPolicy.from_config(policy)
    nodes={'D':Depot('D',0,0),'S':Satellite('S',0 if zero else 5,0)}
    for i in (1,2):
        nodes['C'+str(i)]=Customer('C'+str(i),0 if zero else 5+i,0,0,0,(0,130),(0,130),'S')
    fleet=dict(E1_vehicle_count=1,vehicles_per_satellite=1,E1_Qk=100,E1_Ck=100,E2_Qv=100,E2_Cv=100,container_weight=1,container_volume=1)
    allowed=('b1',) if one_batch else ('b1','b2')
    orders=[Order('o1','C1','S','b1',1,1,(0,20 if late else 90),(65,70) if late else (0,95),0,2,0,allowed)]
    if not late:
        orders.append(Order('o2','C2','S','b1' if one_batch else 'b2',1,1,(0,90),(0,95),0,2,0,allowed))
    return JointBatchInstance(ProblemInstance(nodes,fleet),orders,p,options,synthetic_profile(pool_rate))


@pytest.fixture
def exact_available():
    gp=pytest.importorskip('gurobipy')
    try:
        m=gp.Model(); m.Params.OutputFlag=0; m.dispose()
    except gp.GurobiError as exc:
        pytest.skip(str(exc))
