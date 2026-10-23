"""Single-model batch-count selection; no external K search or certificates.

Numbered models supply their own fixed-count formulation and extraction.
This module only assembles their disjunction and handles the public contract.
"""
import time

from models.common.gurobi_disjunction import append_block, check_deadline
from models.common.solver_config import MilpOptions, validate_budget, read_milp_info, accept_milp_audit
from models.common.cycle.joint import joint_empty_result


def build_unified_model(problem, *, build_fixed, verbose=False, deadline=None, compact_e2=False,
                        count_screening=False):
    import gurobipy as gp
    from gurobipy import GRB

    if not isinstance(count_screening, bool):
        raise ValueError('count_screening must be boolean')
    counts = tuple(problem.batch_count_candidates)
    if not counts or len(set(counts)) != len(counts):
        raise ValueError('Expected a nonempty unique batch-count domain')
    check_deadline(deadline)
    model = gp.Model(f'model_{problem.model_number}_unified_batch_count')
    try:
        model.Params.OutputFlag = int(verbose)
        model.Params.Threads = 1
        model.Params.MIPGap = 0
        model.Params.MIPGapAbs = 0
        selectors = model.addVars(counts, vtype=GRB.BINARY, name='select_count')
        model.addConstr(selectors.sum() == 1, name='select_one_count')
        count_var = model.addVar(vtype=GRB.INTEGER, lb=min(counts), ub=max(counts), name='batch_count')
        model.addConstr(count_var == gp.quicksum(k*selectors[k] for k in counts), name='chosen_batch_count')
        blocks, objective, screened = {}, gp.LinExpr(), {}
        for count in counts:
            check_deadline(deadline)
            child = problem.for_count(count)
            if count_screening:
                from models.common.cycle.count_screening import infeasible_count_certificate
                certificate = infeasible_count_certificate(child)
                check_deadline(deadline)
                if certificate is not None:
                    # Keep the original K domain and its explicit proof. This
                    # substitutes a known-infeasible block with selector=0.
                    model.addConstr(selectors[count] == 0, name=f'K{count}_assignment_infeasible')
                    screened[count] = certificate
                    continue
            source, context = build_fixed(child, verbose=False, deadline=deadline,
                                          compact_e2=compact_e2)
            try:
                mapped, cost = append_block(model, source, context, selectors[count],
                                            prefix=f'K{count}_', deadline=deadline)
                blocks[count] = dict(problem=child, context=mapped)
                objective += cost
            finally:
                source.dispose()
        pool = model.addVar(vtype=GRB.INTEGER, lb=problem.pool_min, ub=problem.pool_max,
                            name='container_pool')
        model.addConstr(pool == gp.quicksum(b['context']['pool'] for b in blocks.values()),
                        name='chosen_container_pool')
        model.setObjective(objective, GRB.MINIMIZE)
        model.update()
        check_deadline(deadline)
        return model, dict(problem=problem, selectors=selectors, batch_count=count_var,
                           pool=pool, blocks=blocks, batch_count_candidates=counts,
                           screened_counts=screened)
    except BaseException:
        model.dispose()
        raise


def _identity(problem, result):
    result.update(model=problem.model_number, solver='exact',
                  model_module=f'models.routing.model_{problem.model_number}.exact',
                  solver_module=f'models.routing.model_{problem.model_number}.exact.solver')
    if getattr(problem, 'uniform_cycle', False):
        result.update(schedule_mode='uniform_cycle', batch_mode='uniform_cycle',
                      uniform_cycle_config=problem.uniform_contract)
    else:
        from models.common.cycle.calendar import STRATEGIES
        result.update(strategy=STRATEGIES[problem.model_number], schedule_mode=problem.schedule_mode,
                      batch_mode=problem.schedule_mode, calendar_config=problem.calendar_contract)


def solve_unified_milp(problem, *, build_fixed, extract_fixed, time_limit=60, seed=42,
                       verbose=False, milp_options=None, count_screening=False):
    validate_budget(time_limit, seed)
    options = MilpOptions.resolve(milp_options)
    begun = time.perf_counter()
    counts = list(problem.batch_count_candidates)
    config = options.metadata(time_limit, seed)
    config.update(batch_count_formulation='one_hot_disjunctive_MILP', k_search=None,
                  retained_model_bounds=False, formulation_options=dict(
                      compact_e2=options.formulation == 'compact', strengthening=False,
                      count_screening=count_screening))
    result = joint_empty_result(problem, 'exact')
    result['cycle_batch_count'] = None
    scope = f'full_model_{problem.model_number}_all_K_joint_cost_integer_time_MILP'

    def finish(result, complete):
        _identity(problem, result)
        result['info'].update(solver_config=config, runtime=time.perf_counter()-begun,
                              proof_scope=scope, batch_count_candidates=counts,
                              complete_count_domain=complete, model_size_scope='all_counts',
                              mip_runtime_scope='single_unified_model')
        return result

    try:
        model, context = build_unified_model(problem, build_fixed=build_fixed, verbose=verbose,
                                             deadline=begun+time_limit,
                                             compact_e2=options.formulation == 'compact',
                                             count_screening=count_screening)
    except TimeoutError:
        result['info'].update(status='TIME_LIMIT_BUILD', objective=None, best_bound=None,
                              proven_optimal=False, proven_infeasible=False, mip_gap=None)
        return finish(result, False)
    try:
        model.Params.Threads = options.threads
        model.Params.Seed = seed
        build_time = time.perf_counter()-begun
        model.Params.TimeLimit = max(0., time_limit-build_time)
        model.optimize()
        info = read_milp_info(model, proof_scope=scope, build_time=build_time)
        info.update(built_batch_counts=list(context['blocks']),
                    screened_batch_counts=context['screened_counts'])
        result['info'] = info
        extraction_begun = time.perf_counter()
        if model.SolCount:
            selected = [k for k, var in context['selectors'].items() if var.X > .5]
            if len(selected) != 1:
                raise RuntimeError('Unified incumbent must select exactly one batch count')
            count = selected[0]
            block = context['blocks'][count]
            result = extract_fixed(block['problem'], model, block['context'], info)
            if result.get('audit', {}).get('valid'):
                if getattr(problem, 'uniform_cycle', False):
                    from models.common.cycle.uniform import attach_uniform_decisions
                    attach_uniform_decisions(problem, result, count)
                else:
                    from models.common.cycle.calendar import attach_calendar_decisions
                    boundaries = [b['start'] for b in result['batches']]
                    boundaries.append(block['problem'].operating_policy.cycle_end)
                    attach_calendar_decisions(problem, result, count, boundaries)
                _identity(problem, result)
                from models.common.cycle.audit import audit_solution
                result['audit'] = audit_solution(problem, result)
            accept_milp_audit(result, float(model.ObjVal))
        result['info']['extraction_audit_time'] = time.perf_counter()-extraction_begun
        return finish(result, True)
    finally:
        model.dispose()
