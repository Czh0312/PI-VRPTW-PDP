"""M5 constraint entry: business groups on the same Gurobi model.

The single-batch functions are retained for diagnostic compatibility.
Only strengthening.py adds optional certificate rows.
"""

from .constraint_blocks.single_batch import (
    add_assignment_constraints,
    add_supply_time_constraints,
    add_route_constraints,
    add_first_echelon_constraints,
    add_supply_coverage_constraints,
    add_second_echelon_constraints,
    add_shared_route_constraints,
    add_task_and_return_coverage_constraints,
    add_inventory_constraints,
)

from .constraint_blocks.batching import (
    multibatch_add_availability_constraints,
    multibatch_add_assignment_constraints,
    multibatch_add_supply_time_constraints,
    multibatch_add_supply_coverage_constraints,
)

from .constraint_blocks.routing import (
    multibatch_add_route_constraints,
    multibatch_add_shared_route_constraints,
)

from .constraint_blocks.echelon import (
    multibatch_add_first_echelon_constraints,
    multibatch_add_second_echelon_constraints,
)

from .constraint_blocks.recovery import (
    multibatch_add_cutoff_quantity_constraints,
    multibatch_add_task_and_return_coverage_constraints,
)

from .constraint_blocks.inventory import (
    multibatch_add_inventory_constraints,
)

from .constraint_blocks.strengthening import (
    multibatch_add_distance_certificate_constraints,
)
