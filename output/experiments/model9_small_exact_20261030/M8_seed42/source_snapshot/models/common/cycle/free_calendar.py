"""M9 assignment domains and fixed-calendar physical projections; no solver code."""
from dataclasses import replace

from models.common.cycle.assignment import validate_assignment
from models.common.cycle.joint import JointBatchInstance


def assignment_domains(problem, fixed=None):
    """All satellites are eligible; an explicit complete map fixes the M8 special case."""
    customers = sorted({order.customer for order in problem.orders})
    if fixed is not None:
        fixed = validate_assignment(problem, fixed)
    return {customer: (fixed[customer],) if fixed is not None else tuple(problem.satellites)
            for customer in customers}


class FreeJointBatchInstance(JointBatchInstance):
    """One M9 calendar before the customer's common delivery/recovery depot is chosen."""

    def __init__(self, *args, customer_assignment_domains, **kwargs):
        super().__init__(*args, **kwargs)
        self.free_assignment = True
        self.calendar_model_number = 9
        self.customer_assignment_domains = customer_assignment_domains
        # The input satellite is only a reference assignment. Geographic
        # screening against it would discard feasible choices at other sites.
        self.legal_batches = {
            order.id: tuple(bid for bid in order.allowed_batch_ids
                            if self.batch_map[bid].start >= order.release_time)
            for order in self.orders}

    def assigned(self, assignment):
        assignment = validate_assignment(self, assignment)
        if any(site not in self.customer_assignment_domains[customer]
               for customer, site in assignment.items()):
            raise ValueError('Customer assignment is outside the configured domain')
        orders = [replace(order, satellite=assignment[order.customer]) for order in self.orders]
        fixed = JointBatchInstance(self.network, orders, self.operating_policy,
                                  self.options, self.cost_profile.config,
                                  _physical_source=self,
                                  _calendar_grid_seconds=self.calendar_decision_grid_seconds)
        fixed.calendar_model_number = 9
        return fixed

    def project(self, *args, **kwargs):
        raise ValueError('Resolve the customer assignment before physical projection')
