"""Solver configuration and result metadata, independent of business models.

This module neither builds a formulation nor imports a numbered solver.
"""
from dataclasses import asdict, dataclass
import math


@dataclass(frozen=True)
class MilpOptions:
    """Reproducible direct-Gurobi baseline; no external starts or certificates.

    expanded/compact choose equivalent expressions, not different K domains.
    Safe model preprocessing and symmetry remain part of the declared model.
    """

    threads: int = 1
    formulation: str = 'expanded'

    def __post_init__(self):
        if isinstance(self.threads, bool) or not isinstance(self.threads, int) or self.threads < 1:
            raise ValueError('threads must be a positive integer')
        if self.formulation not in ('expanded', 'compact'):
            raise ValueError('formulation must be expanded or compact')

    @classmethod
    def resolve(cls, value=None):
        if value is None:
            return cls()
        if isinstance(value, cls):
            return value
        if isinstance(value, dict):
            return cls(**value)
        raise TypeError('milp_options must be MilpOptions or a configuration dict')

    def metadata(self, time_limit, seed):
        return dict(asdict(self), time_limit=time_limit, seed=int(seed),
                    mip_gap=0.0, mip_gap_abs=0.0, mip_start=False,
                    custom_callback=False, external_certificate=False,
                    gurobi_presolve='default', gurobi_cuts='default',
                    gurobi_heuristics='default', gurobi_mip_focus=0,
                    model_preprocessing='safe_bounds_sparse_arcs_and_symmetry',
                    budget_scope='precheck_build_optimize_extract_audit')


def validate_budget(time_limit, seed):
    if isinstance(time_limit, bool) or not math.isfinite(time_limit) or time_limit <= 0:
        raise ValueError('time_limit must be finite and positive')
    if isinstance(seed, bool) or not isinstance(seed, int) or not 0 <= seed <= 2000000000:
        raise ValueError('seed must be an integer between 0 and 2000000000')


def read_milp_info(model, *, proof_scope, build_time):
    """Read solver evidence; an incumbent still needs independent auditing."""
    import gurobipy as gp
    from gurobipy import GRB

    status = {GRB.OPTIMAL: 'OPTIMAL', GRB.INFEASIBLE: 'INFEASIBLE',
              GRB.TIME_LIMIT: 'TIME_LIMIT', GRB.INTERRUPTED: 'INTERRUPTED',
              GRB.INF_OR_UNBD: 'INF_OR_UNBD'}.get(model.Status, str(model.Status))
    bound = None
    try:
        bound = float(model.ObjBound) if math.isfinite(model.ObjBound) else None
    except (AttributeError, gp.GurobiError):
        pass
    return dict(status=status, proven_optimal=bool(model.SolCount and model.Status == GRB.OPTIMAL),
                proven_infeasible=model.Status == GRB.INFEASIBLE, objective=None,
                best_bound=bound, gurobi_best_bound=bound,
                best_bound_source='gurobi' if bound is not None else None,
                mip_gap=None, proof_scope=proof_scope, build_time=build_time,
                mip_runtime=model.Runtime, mip_work=model.Work,
                node_count=model.NodeCount, sol_count=model.SolCount,
                variables=model.NumVars, binary_variables=model.NumBinVars,
                constraints=model.NumConstrs+model.NumGenConstrs,
                mip_start_supplied=False, gurobi_version='.'.join(map(str, gp.gurobi.version())))


def accept_milp_audit(result, objective):
    """Only an independently audited, cost-consistent solution is an upper bound."""
    info = result['info']
    audit = result.get('audit', {})
    if not audit.get('valid'):
        info.update(status='AUDIT_FAILED', proven_optimal=False, proven_infeasible=False,
                    objective=None, best_bound=None, mip_gap=None)
        return
    cost = audit['objective']
    if not math.isclose(cost, objective, abs_tol=1e-5, rel_tol=1e-7):
        raise RuntimeError('MILP objective disagrees with independent cost replay')
    bound = info.get('best_bound')
    if bound is not None and bound > cost + max(1e-5, abs(cost)*1e-7):
        raise RuntimeError('MILP lower bound exceeds the audited incumbent')
    info.update(objective=cost, milp_objective=objective,
                mip_gap=max(0., cost-bound)/max(1e-9, abs(cost)) if bound is not None else None)


def record_strategy(result, requested, *, time_limit, seed):
    """Keep algorithm identity separate from calendar strategy and solver family."""
    info = result['info']
    info['exact_strategy_requested'] = requested
    if requested == 'milp':
        info['exact_strategy'] = 'gurobi_k_enumeration' if 'count_search' in result else 'gurobi_milp'
        if 'count_search' in result:
            # A selected child's raw bound is not the bound of the union of K domains.
            # Raw solver evidence remains available on each visit.
            info.pop('gurobi_best_bound', None)
            info['best_bound_source'] = 'all_count_gurobi_bounds' if info.get('best_bound') is not None else None
            visits = result.get('count_search_visits', [])
            info['mip_runtime'] = sum(v.get('mip_runtime', 0.) for v in visits)
            info['mip_runtime_scope'] = 'all_visited_counts'
            info['build_time'] = sum(v.get('build_time', 0.)+v.get('construction_time', 0.) for v in visits)
            info['node_count'] = sum(v.get('node_count', 0.) for v in visits)
            info['model_size_scope'] = 'selected_count'
    else:
        info['exact_strategy'] = info.get('assignment_strategy',
            str(info.get('budget_strategy', 'default'))+'_k_search'
            if 'count_search' in result else 'default_milp')
        info.setdefault('solver_config', dict(
            profile='existing_defaults', time_limit=time_limit, seed=seed, threads=1,
            formulation='per_model_auto',
            formulation_features=info.get('formulation_features'),
            compact_e2=info.get('compact_e2'),
            mip_start_supplied=info.get('mip_start_supplied'),
            budget_strategy=info.get('budget_strategy'),
            per_count_details='count_search_visits' if 'count_search' in result else None,
        ))
    info.setdefault('solve_request', dict(time_limit=time_limit, seed=seed))
    return result
