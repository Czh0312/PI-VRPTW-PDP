"""Optional route-cover lower-bound experiment; no integer solution output."""
from models.common.cycle.scope import validate_problem


def route_cover_certificate(problem, **options):
    validate_problem(problem, 5)
    from models.routing.model_5.exact.multibatch_route_cg import route_cover_certificate as compute
    return compute(problem, **options)


def e1_inventory_certificate(problem, **options):
    validate_problem(problem, 5)
    from models.routing.model_5.exact.multibatch_e1_inventory import e1_inventory_certificate as compute
    return compute(problem, **options)


def joint_route_inventory_certificate(problem, **options):
    validate_problem(problem, 5)
    from models.routing.model_5.exact.multibatch_joint_inventory import joint_route_inventory_certificate as compute
    return compute(problem, **options)


def logic_benders_certificate(problem, **options):
    """Opt-in complete-domain certificate with verified physical separation."""
    validate_problem(problem, 5)
    from models.routing.model_5.exact.multibatch_logic_benders import logic_benders_certificate as compute
    return compute(problem, **options)
