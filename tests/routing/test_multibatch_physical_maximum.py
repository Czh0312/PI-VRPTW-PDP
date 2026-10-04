"""Independent cutoff equality projections through the indexed E1 backend."""
from dataclasses import replace

import pytest

from models.routing.common.cycle import MultiBatchInstance
from models.routing.common.cycle.audit import audit_solution
from models.routing.common.cycle.state import empty_result
from models.routing.common.domain.instance import ProblemInstance
from models.routing.common.domain.node import Depot, Satellite, Customer
from models.routing.solvers.exact.multibatch_joint_inventory import build_joint_inventory, set_joint_projection
from models.routing.solvers.exact.multibatch_physical_cuts import add_physical_cut, project_physical_costs
from tests.routing.test_multibatch_cycle import fixture
from tests.routing.test_multibatch_joint_inventory import domains


@pytest.mark.parametrize('returned', [30, 31])
@pytest.mark.parametrize('timed', [False, True])
def test_five_site_cutoff_allocation_equals_actual_returns_for_maximum_activation(returned, timed):
    gp = pytest.importorskip('gurobipy')
    template = fixture(pool=10, zero=True, cleaning=0)
    nodes, orders = {'D':Depot('D',0,0)}, []
    for i in range(5):
        site, customer = f'S{i}', f'C{i}'
        nodes[site] = Satellite(site,0,0)
        nodes[customer] = Customer(customer,0,0,0,0,(0,125),(0,125),site)
        orders.extend(replace(o,id=f'{o.id}_{i}',customer=customer,satellite=site) for o in template.orders)
    fleet = dict(E1_vehicle_count=1,vehicles_per_satellite=1,E1_Qk=500,E1_Ck=500,
                 E2_Qv=500,E2_Cv=500,container_weight=1,container_volume=1)
    problem = MultiBatchInstance(ProblemInstance(nodes,fleet),orders,template.operating_policy)
    initial = empty_result(problem,'manual')
    for index,batch in enumerate(problem.operating_policy.batches):
        selected = [o for o in orders if o.batch == batch.id]
        pickups = []
        for o in selected:
            quantity = (int(returned <= 30) if index == 0 else 1+int(returned > 30)) if o.satellite == 'S0' else 1
            if quantity:
                pickups.append(dict(kind='p',site_id=o.satellite,quantity=quantity,start=batch.cutoff))
            initial['e2_trips'].append(dict(trip_id=f'e2_{o.id}',vehicle_id=0,satellite_id=o.satellite,
                trip_index=index,departure=batch.start+1,
                return_complete=returned if o.id == 'o1_0' else batch.start+4,
                events=[dict(kind='d',order_id=o.id,start=batch.start+2,quantity=1),
                        dict(kind='p',order_id=o.id,start=batch.start+3,quantity=1)]))
        initial['e1_trips'].append(dict(trip_id=f'e1_{index}',vehicle_id=0,batch_id=batch.id,
            departure=batch.start,return_complete=batch.cutoff+1,
            events=[dict(kind='d',site_id=o.satellite,quantity=1,start=batch.start+1) for o in selected]+pickups))
    assert audit_solution(problem,initial)['valid']
    model,context = build_joint_inventory(problem,domains(problem,timed=timed))
    try:
        assert context['backend'] == 'indexed'
        # Zero directed distances provide an independently evident floor of 0.
        cert = dict(satellite='S0',bound=0,maximum_returns={'b1':0},
            scope='M5_conditional_physical_E2_site_cost_relaxation',subproblem_infeasible=False)
        cut = add_physical_cut(problem,model,context,cert)
        set_joint_projection(problem,context,initial,fix=True)
        project_physical_costs(problem,context,initial,cuts=[cut],fix=True)
        model.optimize()
        assert model.Status == gp.GRB.OPTIMAL and model.ObjVal == 0
        early = sum(o.count*context['e1']['allocation'][o.id,'b1'].X
                    for o in orders if o.satellite == 'S0')
        assert early == int(returned <= 30)
        assert cut['active'].X == int(returned > 30)
    finally:
        model.dispose()
