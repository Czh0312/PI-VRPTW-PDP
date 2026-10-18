"""Supply conjunctions activate only when all necessary batch conditions hold."""
import random
import pytest
from models.routing.model_5.exact.accelerators.multibatch.conditional_inventory import build_conditional_inventory, conditional_context, conditional_supply_context, set_conditional_projection
from tests.routing.test_multibatch_cycle import fixture,hand_solution


@pytest.mark.parametrize('second,active',[(69,True),(70,True),(71,False)])
def test_two_supply_conditions_are_and_not_or_and_project_the_complete_schedule(second,active):
    gp=pytest.importorskip('gurobipy');p=fixture(pool=2);initial=hand_solution(p)
    supplies=[dict(batch='b1',threshold=10),dict(batch='b2',threshold=second)]
    model,ctx=build_conditional_inventory(p,{'S':1},[dict(site='S',supply_conditions=supplies,bound=3)])
    try:
        # The hand solution supplies at 10/70. Fix its E1 topology/quantities,
        # then let the exact conjunction determine the minimum theta.
        from models.routing.model_5.exact.accelerators.multibatch.e1_routes import set_route_projection
        set_route_projection(p,ctx,initial,fix=True)
        model.optimize();assert model.Status==gp.GRB.OPTIMAL
        assert model.getVarByName('conditional_all_late_0').X==int(active)
        assert ctx['theta']['S'].X==pytest.approx(3 if active else 1)
        # The complete physical projection uses the actual E2 cost of 4.
        set_conditional_projection(p,ctx,initial,fix=True)
        model.optimize();assert model.Status==gp.GRB.OPTIMAL and model.ObjVal==44
    finally:model.dispose()


def test_joint_context_tightens_both_delivery_and_necessary_pickup_bounds_without_losing_other_batches():
    p=fixture(pool=2)
    combined=conditional_supply_context(p,'S',[dict(batch='b1',threshold=12),dict(batch='b2',threshold=73)])
    for bid,threshold in [('b1',12),('b2',73)]:
        single=conditional_context(p,'S',bid,threshold)
        for n in combined['nodes']:
            if n['order'].batch==bid:assert n['window']==single['lookup'][n['key']]['window']
    with pytest.raises(ValueError,match='nonempty'):conditional_supply_context(p,'S',[])
    with pytest.raises(ValueError,match='distinct'):conditional_supply_context(p,'S',[dict(batch='b1',threshold=10),dict(batch='b1',threshold=11)])


def test_conjunction_floor_is_maximum_with_single_conditions_not_an_additional_cost():
    gp=pytest.importorskip('gurobipy');p=fixture(pool=2)
    rows=[dict(site='S',batch='b1',threshold=10,bound=2),
          dict(site='S',supply_conditions=[dict(batch='b1',threshold=10),dict(batch='b2',threshold=70)],bound=3)]
    model,ctx=build_conditional_inventory(p,{'S':1},rows)
    try:
        model.optimize();assert model.Status==gp.GRB.OPTIMAL
        assert model.ObjVal==43 and ctx['theta']['S'].X==pytest.approx(3)
    finally:model.dispose()


@pytest.mark.parametrize('phase_one',[False,True])
def test_conjunctive_full_domain_pricing_matches_independent_elementary_trip_enumeration(phase_one):
    pytest.importorskip('gurobipy')
    from models.routing.model_5.exact.accelerators.pickup_route_pricing import RoutePricer
    from tests.routing.test_multibatch_route_cg import enumerate_trips
    p=fixture(pool=2)
    supplies=[dict(batch='b1',threshold=12),dict(batch='b2',threshold=73)]
    context=conditional_supply_context(p,'S',supplies)
    oracle=enumerate_trips(p,'S',supply_conditions=supplies)
    assert oracle and {p.order_map[oid].batch for seq,_ in oracle for oid,kind in seq if kind=='d'}=={'b1','b2'}
    rng=random.Random(643)
    pricer=RoutePricer(p,context)
    try:
        for _ in range(6):
            duals={n['key']:rng.uniform(-5,20) for n in context['nodes']}
            fleet_dual=-rng.uniform(0,5)
            expected=min((0 if phase_one else cost)-sum(duals[k] for k in seq)-fleet_dual for seq,cost in oracle)
            priced=pricer.price(duals,fleet_dual,time_limit=5,phase_one=phase_one)
            assert priced['optimal'] and priced['reduced_cost']==pytest.approx(expected,abs=1e-6)
            assert priced['lower_bound']<=expected+1e-7
            assert priced['column']['tasks'] in {seq for seq,_ in oracle}
    finally:pricer.dispose()
