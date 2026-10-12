"""M5 exact entry."""
from models.routing.model_5.exact.solver import solve_exact
from models.routing.model_5.exact.bounds import e1_inventory_certificate, route_cover_certificate, joint_route_inventory_certificate, logic_benders_certificate
from models.routing.model_5.exact.primal import reoptimize_e1, reoptimize_route_pool, solve_hybrid

__all__ = ["solve_exact", "route_cover_certificate", "e1_inventory_certificate", "joint_route_inventory_certificate", "logic_benders_certificate", "reoptimize_e1", "reoptimize_route_pool", "solve_hybrid"]

MODEL_NUMBER = 5
BATCH_MODE = 'multi_batch'
