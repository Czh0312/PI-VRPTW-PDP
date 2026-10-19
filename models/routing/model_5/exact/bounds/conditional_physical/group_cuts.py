"""Externally validated all-path group oracle implications in a joint master.

The certificate must be independently checked against the SAME original
problem. Exact binary task-set flags preserve each complete full schedule's
projection, even when original route choices are continuous. Cost implications
apply to E1 objective plus actual E2 site epigraphs, not compressed path cost.
"""
import math
from models.routing.model_5.exact.bounds.conditional_physical.physical_site import normalize_trip_groups
from models.routing.model_5.exact.bounds.conditional_physical.physical_cuts import lift_physical_costs, project_physical_costs
from models.common.cycle.audit import audit_solution


def add_group_schedule_cut(problem,model,context,certificate,*,name='group_schedule_cut'):
    import gurobipy as gp
    if (problem.model_number!=5 or problem.free_assignment or not problem.reusable
            or len(problem.operating_policy.batches)<=1):
        raise ValueError('Fixed reusable multibatch M5 required')
    if certificate.get('scope')!='M5_conditional_all_path_complete_task_groups_total_cost':
        raise ValueError('Validated all-path complete-group oracle required')
    if not certificate.get('permutations_complete') or not certificate.get('canonical_position_completeness'):
        raise ValueError('Complete visiting orders and physical positions required')
    active={o.satellite for o in problem.orders}
    groups=certificate.get('groups')
    if not isinstance(groups,dict) or set(groups)!=active or set(context['routes'])!=active:
        raise ValueError('All active sites required')
    normalized={s:normalize_trip_groups(problem,s,g) for s,g in groups.items()}
    bound=certificate.get('conditional_lower_bound',0)
    if not math.isfinite(bound) or bound<0:
        raise ValueError('Finite nonnegative conditional total bound required')
    infeasible=certificate.get('conditional_infeasible',False)
    if infeasible and certificate.get('status')!=gp.GRB.INFEASIBLE:
        raise ValueError('No-good requires a proved complete conditional infeasibility')
    lift_physical_costs(model,context)
    total=model.getObjective()
    if not isinstance(total,gp.LinExpr):raise ValueError('Linear total objective required')
    triggers=[]
    for site,site_groups in sorted(normalized.items()):
        data=context['routes'][site]
        for index,group in enumerate(site_groups):
            expression=gp.quicksum(v for (i,_),v in data['choices'].items()
                if frozenset(data['rows'][i]['keys'])==frozenset(group))
            flag=model.addVar(vtype=gp.GRB.BINARY,name=f'{name}_{site}_group_{index}')
            model.addConstr(flag==expression,name=f'{name}_{site}_match_{index}')
            triggers.append(dict(satellite=site,group=group,flag=flag,expression=expression))
    if not triggers:raise ValueError('Nonempty complete groups required')
    enabled=model.addVar(vtype=gp.GRB.BINARY,name=name+'_active')
    for index,t in enumerate(triggers):model.addConstr(enabled<=t['flag'],name=f'{name}_and_upper_{index}')
    model.addConstr(enabled>=gp.quicksum(t['flag'] for t in triggers)-len(triggers)+1,name=name+'_and_lower')
    if infeasible:
        row=model.addConstr(enabled==0,name=name+'_no_good')
    else:
        row=model.addGenConstrIndicator(enabled,True,total,gp.GRB.GREATER_EQUAL,bound,name=name+'_total_floor')
    model.update()
    return dict(scope='experimental_validated_complete_group_implication',triggers=triggers,
        active=enabled,row=row,total=total,bound=bound,infeasible=infeasible,
        cost_target='E1_plus_actual_E2_epigraph',proven_optimal=False,proven_infeasible=False)


def project_group_schedule_cut(problem,context,initial,cut,*,fix=False):
    if not audit_solution(problem,initial)['valid']:
        raise ValueError('An audited complete original schedule required')
    costs=project_physical_costs(problem,context,initial,fix=fix)
    actual={site:{frozenset((e['order_id'],e['kind']) for e in t['events'])
        for t in initial['e2_trips'] if t['satellite_id']==site} for site in context['routes']}
    flags=[]
    for trigger in cut['triggers']:
        value=int(frozenset(trigger['group']) in actual[trigger['satellite']]);flags.append(value)
        trigger['flag'].Start=value
        if fix:trigger['flag'].LB=trigger['flag'].UB=value
    enabled=int(all(flags));cut['active'].Start=enabled
    if fix:cut['active'].LB=cut['active'].UB=enabled
    if enabled and cut['infeasible']:
        raise ValueError('Audited schedule contradicts an externally supplied infeasibility certificate')
    return dict(actual_site_costs=costs['actual_site_costs'],group_flags=flags,active=enabled,
        full_proven_optimal=False)
