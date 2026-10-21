"""Audit a fixed reference schedule at E4 pool points, without reoptimization."""
from pathlib import Path
import json

from experiments.analysis.analyze_thesis_stages import table
from experiments.runs import run_thesis_completion as driver
from models.common.cycle.audit import audit_solution, _audit
from models.common.cycle.instance import load_instance
from tools.configuration.loader import load_routing_case


def fixed_schedule_at_pool(result, fixed, pool):
    policy = {**result['operating_policy'], 'container_pool_size': pool}
    if json.dumps(policy, sort_keys=True) != json.dumps(fixed.operating_policy.as_dict(), sort_keys=True):
        raise ValueError('Projected reference changes a physical policy field other than pool')
    return {**result, 'model': fixed.model_number, 'container_pool_size': pool,
            'operating_policy': policy,
            'info': {**result['info'], 'objective': result['total_distance']}}


def threshold_with_reference(lower, members, reference_pool):
    formal = [r['fixed_pool'] for r in members if r['valid']]
    feasible = formal + ([reference_pool] if reference_pool is not None else [])
    upper = min(feasible) if feasible else None
    excluded = {r['fixed_pool'] for r in members if r['proven_infeasible']}
    if any(n in excluded for n in feasible):
        raise ValueError('Infeasibility certificate contradicts a matched feasible witness')
    unresolved = [n for n in range(lower, upper) if n not in excluded] if upper is not None else None
    return dict(necessary_lower_bound=lower,
                formal_smallest_feasible=min(formal) if formal else None,
                fixed_schedule_feasible_witness=reference_pool,
                feasible_upper_witness=upper,
                proven_threshold=upper if upper is not None and not unresolved else None,
                unresolved_lower_integers=str(unresolved))


def reference_overlay(study, rows, evidence_path, pool_reference_path):
    study = Path(study)
    evidence = {e['case']: e for e in driver.read(Path(evidence_path))}
    points = {(r['case'], r['pool']): r for r in driver.read(Path(pool_reference_path))}
    expected = {(r['reference_case'], r['fixed_pool']) for r in rows}
    if set(points) != expected:
        raise ValueError('Fixed-schedule reference does not match the registered E4 grid')
    checked_studies, references, outputs = set(), {}, []
    for (case, pool), point in sorted(points.items()):
        meta = evidence[case]
        result_path = Path(meta['reference_result'])
        if Path(point['reference_result']).resolve() != result_path.resolve():
            raise ValueError('Reference paths disagree')
        if case not in references:
            source = Path(meta['selected_study'])
            manifest = driver.read(source/'manifest.json')
            if source not in checked_studies:
                driver.verify(source, manifest)
                checked_studies.add(source)
            if not driver.complete_attempt(result_path.parent):
                raise ValueError('Reference attempt is incomplete')
            if driver.sha(result_path) != meta['reference_sha256']:
                raise ValueError('Preregistered reference hash changed')
            record = driver.read(result_path.parent/'audit.json')
            if record['result_sha256'] != meta['reference_sha256']:
                raise ValueError('Reference audit/result hash mismatch')
            result = driver.read(result_path)
            saved_row = driver.read(result_path.parent/'row.json')
            source_job = next(j for j in manifest['jobs'] if j['id'] == saved_row['id'])
            cfg, fleet = load_routing_case(source/source_job['case_path'], use_pool_config=False)
            audited = audit_solution(load_instance(cfg, fleet), result)
            if not audited['valid'] or not audited['periodic_replay']['valid']:
                raise ValueError('Reference failed fresh independent audit')
            if audited['minimum_pool_for_schedule'] != meta['minimum_pool_for_schedule']:
                raise ValueError('Reference minimum pool changed')
            references[case] = result, audited
        result, original_audit = references[case]
        members = [r for r in rows if r['reference_case'] == case and r['fixed_pool'] == pool]
        if len({r['case_path'] for r in members}) != 1:
            raise ValueError('Seed replicates use different registered configurations')
        cfg, fleet = load_routing_case(study/members[0]['case_path'], use_pool_config=False)
        problem = load_instance(cfg, fleet)
        assignment = result['order_batch_assignment']
        if cfg['joint_batch_options']['fixed_assignment'] != assignment:
            raise ValueError('Reference and E4 fixed assignments differ')
        if problem.pool_min != pool or problem.pool_max != pool:
            raise ValueError('Reference point is not fixed in the E4 problem')
        fixed = problem.project(assignment, pool, result['batch_cutoffs'], result.get('batch_loading_starts'))
        lower = max(fixed.batch_demands.values())
        if lower != point['necessary_lower_bound'] or any(r['necessary_lower_bound'] != lower for r in members):
            raise ValueError('Necessary simultaneous-issue lower bound differs')
        # Project only pool size. Routes, events, cutoff times and loading starts
        # are taken verbatim from the hash-pinned reference result.
        physical = _audit(fixed, fixed_schedule_at_pool(result, fixed, pool), repeat=True)
        feasible = physical['valid']
        expected_feasible = pool >= original_audit['minimum_pool_for_schedule']
        if feasible != expected_feasible or feasible != point['unchanged_schedule_feasible']:
            raise ValueError('Fixed-schedule feasibility differs from preregistered evidence')
        if point['minimum_pool_for_unchanged_schedule'] != original_audit['minimum_pool_for_schedule']:
            raise ValueError('Fixed-schedule minimum differs')
        if not feasible and any(e['code'] not in {'CLEAN_STOCK_SHORTAGE', 'CONTAINER_CONSERVATION', 'TERMINAL_OUTSTANDING'} for e in physical['errors']):
            raise ValueError(f'Reference has a non-pool physical mismatch: {case}, N={pool}: {physical["errors"]}')
        cost = problem.cost_profile.evaluate(physical, pool) if feasible else None
        if problem.cost_profile.hash != result['economic_profile_hash']:
            raise ValueError('E4 and reference economic profiles differ')
        driver.write(study/'reference_audits'/f'{case}_n{pool}.json', dict(
            reference_result=str(result_path), reference_sha256=meta['reference_sha256'],
            target_case_sha256=driver.sha(study/members[0]['case_path']),
            fixed_schedule=True, solver_called=False, pool=pool, audit=physical, cost=cost))
        outputs.append(dict(case=case, fixed_pool=pool, reference_minimum_pool=original_audit['minimum_pool_for_schedule'],
            fixed_schedule_feasible=feasible, fixed_schedule_cost=cost['total'] if cost else None,
            formal_planned=len(members), formal_valid=sum(r['valid'] for r in members),
            formal_terminal=sum(r['status'] not in ('NOT_RUN', 'RUNNING') for r in members),
            reference_sha256=meta['reference_sha256'], **(cost['components'] if cost else {})))
    table(study/'tables/pool_fixed_schedule_comparison.csv', outputs)
    thresholds = []
    from experiments.analysis.analyze_thesis_mechanisms import plotting
    plt = plotting()
    for index, case in enumerate(sorted(references)):
        members = [r for r in rows if r['reference_case'] == case]
        refs = [r for r in outputs if r['case'] == case]
        upper = min((r['fixed_pool'] for r in refs if r['fixed_schedule_feasible']), default=None)
        thresholds.append(dict(case=case, **threshold_with_reference(
            min(r['necessary_lower_bound'] for r in members), members, upper)))
        fig, (ax, status) = plt.subplots(2, 1, figsize=(8, 5), sharex=True, constrained_layout=True)
        for r in refs:
            n = r['fixed_pool']
            if r['fixed_schedule_feasible']:
                ax.scatter(n, r['fixed_schedule_cost'], marker='D', color='#c98536')
            values = [m['objective'] for m in members if m['fixed_pool'] == n and m['valid']]
            ax.scatter([n]*len(values), values, color='#4078a8', s=20)
            status.scatter(n, int(r['fixed_schedule_feasible']), marker='D', color='#c98536')
            status.scatter(n, r['formal_valid']/r['formal_planned'], color='#4078a8')
            status.annotate(f'{r["formal_valid"]}/{r["formal_planned"]}; terminal {r["formal_terminal"]}',
                            (n, r['formal_valid']/r['formal_planned']), xytext=(0, 6), textcoords='offset points', ha='center', fontsize=7)
        ax.scatter([], [], marker='D', color='#c98536', label='Fixed schedule (feasibility witness)')
        ax.scatter([], [], color='#4078a8', label='Formal reoptimization (first attempts)')
        ax.legend(fontsize=7)
        ax.set(title=case, ylabel='Audited synthetic total cost')
        pools = sorted(r['fixed_pool'] for r in refs)
        padding = max(1, pools[-1]-pools[0])*.25
        status.set(xlabel='Physical pool N', ylabel='Feasibility / success rate', ylim=(-.1, 1.2),
                   xticks=pools, xlim=(pools[0]-padding, pools[-1]+padding))
        for suffix in ('png', 'pdf'):
            fig.savefig(study/'figures'/f'e4_reference_overlay_{index:02d}.{suffix}', dpi=160)
        plt.close(fig)
    table(study/'tables/pool_thresholds_with_reference.csv', thresholds)
    driver.write(study/'pool_reference_analysis.json', dict(
        generated_at=driver.now(), solver_called=False,
        evidence_sha256=driver.sha(Path(evidence_path)),
        pool_reference_sha256=driver.sha(Path(pool_reference_path)),
        analysis_source_sha256=driver.sha(Path(__file__)),
        points=len(outputs), fixed_schedule_feasible=sum(r['fixed_schedule_feasible'] for r in outputs),
        formal_terminal=sum(r['status'] not in ('NOT_RUN', 'RUNNING') for r in rows),
        formal_planned=len(rows),
        limitation='Reference-only witnesses and repricing never replace formal first-attempt outcomes'))
