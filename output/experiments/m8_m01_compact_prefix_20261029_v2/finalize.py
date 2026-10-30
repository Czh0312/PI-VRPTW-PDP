"""Keep native results immutable; combine only audited upper/valid lower bounds."""
from copy import deepcopy
from pathlib import Path
import json
import xml.etree.ElementTree as ET

from study import HERE, CASE, read, save, sha, load_instance, load_routing_case
from models.common.cycle.audit import audit_solution


def validation_counts():
    counts = {}
    for path in sorted(HERE.glob('validation_*.xml')):
        root = ET.parse(path).getroot()
        suites = [root] if root.tag == 'testsuite' else list(root.iter('testsuite'))
        counts[path.name] = {key:sum(int(s.get(key, 0)) for s in suites)
            for key in ('tests', 'failures', 'errors', 'skipped')}
    return counts


if __name__ == '__main__':
    p = load_instance(*load_routing_case(CASE, use_pool_config=False))
    native = read(HERE/'summary.json')
    choices = [(name, read(HERE/name)) for name in
        ('best_audited_result.json', 'heuristic_best_result.json',
         'parallel_best_audited_result.json') if (HERE/name).exists()]
    audited = [(name, result, audit_solution(p, result)) for name, result in choices]
    assert all(a['valid'] and a['periodic_replay']['valid'] for _, _, a in audited)
    name, best, audit = min(audited, key=lambda row:row[2]['objective'])
    summary = deepcopy(native)
    parallel = read(HERE/'parallel_summary.json') if (HERE/'parallel_summary.json').exists() else None
    upper = audit['objective']
    lower = max(native['global_lower_bound'], parallel['global_lower_bound']) if parallel else native['global_lower_bound']
    assert lower <= upper+1e-5
    summary.update(audited_upper_bound=upper,
        complete_model_audited_upper_bound=native['audited_upper_bound'],
        absolute_gap=max(0., upper-lower), gap_over_upper=max(0., upper-lower)/abs(upper),
        proven_optimal=upper-lower <= 1e-6,
        selected_K=best['cycle_batch_count'], selected_N=best['container_pool_size'],
        final_upper_bound_source=name, auxiliary_search_inside_same_wall_budget=True,
        auxiliary_exact_threads=parallel['threads'] if parallel else None,
        auxiliary_exact_summary=parallel,
        primary_single_thread_global_lower_bound=native['global_lower_bound'],
        validation_xml_counts=validation_counts(),
        note='Primary native objective/status/bound are unchanged. Auxiliary ALNS supplies audited upper bounds; the separate complete eight-thread search may supply either valid bound. Both auxiliary searches finish inside the primary wall-clock window.')
    manifest = read(HERE/'manifest.json')
    assert all(sha(path) == value for path, value in manifest['source_and_input_hashes'].items())
    final = deepcopy(best)
    final['info'] = dict(final.get('info', {}), objective=upper,
        status='OPTIMAL' if summary['proven_optimal'] else 'FEASIBLE',
        proven_optimal=summary['proven_optimal'], proven_infeasible=False,
        best_bound=lower, gap=summary['gap_over_upper'],
        proof_scope='original_M8_full_competitive_domain',
        upper_bound_source=name, native_status=native['native_status'])
    final['audit'] = audit
    save('summary_reviewed.json', summary)
    save('final_best_audited_result.json', final)
    save('final_audit.json', audit)
    print(json.dumps(dict(status=summary['status'], proven_optimal=summary['proven_optimal'],
        upper=upper, lower=lower, gap_percent=100*summary['gap_over_upper'],
        source=name), ensure_ascii=False), flush=True)
