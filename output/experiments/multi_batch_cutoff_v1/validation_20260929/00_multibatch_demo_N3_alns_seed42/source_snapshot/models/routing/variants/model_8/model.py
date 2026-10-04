"""Joint M8: free E1/E2 routes, deliveries, pickups and integer handoffs.

Under the current operational assumptions the feasible set equals M6's compact
formulation. M8's capacity-cooperation search is an algorithmic development.
No M3 solve, fixed route, fixed delivery order or restricted active vehicle set.
"""
from models.routing.variants.model_6.model import build_model as build_compact


def build_model(problem, e1_tasks, e2_tasks_by_sat, vehicles_per_satellite,
                TFp=9999.0, allow_transfers=True, strengthen=True):
    from models.routing.common.operating_policy import policy_for
    policy_for(problem).require_model(8)
    model, variables, ctx = build_compact(problem, e1_tasks, e2_tasks_by_sat,
        vehicles_per_satellite, TFp=TFp, strengthen=strengthen)
    model.ModelName = 'Model8_Joint_Capacity_Cooperation'
    if not allow_transfers:
        for group in ('transfer', 'transfer_active'):
            for var in variables[group].values():
                var.UB = 0
    ctx.update(proof_scope='full_joint_fixed_satellite_problem',
               allow_transfers=allow_transfers, mathematical_equivalent='model6')
    model.update()
    return model, variables, ctx
