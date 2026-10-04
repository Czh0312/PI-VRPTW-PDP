"""Independently audit shared floors, source binding and physical column pools."""
from copy import deepcopy
import json
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import validate as check
from configuration.loader import load_routing_case
from models.routing.common.cycle import load_instance
from validation_columns import close, replay_column

DEST = HERE/'column_recovery'
records = []
audits = columns = 0
for name, digest in check.read(DEST/'plan.json')['source_sha256'].items():
    assert check.sha(DEST/'source_snapshot'/name) == digest
for record in check.read(DEST/'summary.json'):
    if record['failure']:
        records.append(record); continue
    payload = check.read(DEST/(record['id']+'.json')); job = payload['job']
    for name, digest in job['source_sha256'].items(): assert check.sha(Path(name)) == digest
    original = check.read(HERE/'fresh'/(job['id']+'.json'))
    p = load_instance(*load_routing_case(original['job']['case'], use_pool_config=False))
    shortest = check.floyd(p)
    result = payload['result']; audit = check.checked_audit(p, result); audits += 1
    assert result['info']['best_bound'] is None and not result['info']['proven_optimal']
    if job['full_source']:
        source = check.read(HERE/'fresh'/(job['full_source']+'.json'))
        q = load_instance(*load_routing_case(source['job']['case'], use_pool_config=False))
        other = check.floyd(q)
        for site in p.satellites:
            left = check.independent_context(p, site, shortest); right = check.independent_context(q, site, other)
            assert left == right
            assert [p.fleet.e2_weight_cap, p.fleet.e2_volume_cap] == [q.fleet.e2_weight_cap, q.fleet.e2_volume_cap]
            for field in ('cycle_start', 'cycle_end', 'e2_return_unload_time', 'e2_turnaround_time'):
                assert getattr(p.operating_policy, field) == getattr(q.operating_policy, field)
            places = [site]+list({n['order'].customer for n in left['nodes']})
            for a in places:
                for b in places: assert p.distance(a, b) == q.distance(a, b) and p.travel(a, b) == q.travel(a, b)
    ref = payload['reference_certificate']
    for row in ref['sites']: check.check_site(p, row, check.independent_context(p, row['satellite'], shortest))
    for leaf in payload['condition_leaves']:
        check.check_site(p, leaf['certificate'], check.independent_context(p, leaf['site'], shortest, leaf))
    proof = check.check_joint(p, result, ref, payload['joint_certificate'],
        DEST/(job['id']+'_models')/'joint.mps.gz',
        HERE/'fresh'/(job['id']+'_models')/'reference/e1_inventory.mps')
    for directory in sorted((DEST/(job['id']+'_models')/'primal').glob('pass_*')):
        initial = check.read(directory/'initial_result.json'); output = check.read(directory/'output_result.json')
        check.checked_audit(p, initial); check.checked_audit(p, output); audits += 2
        if not (directory/'columns.json').exists():
            assert output['info']['route_pool_status'] == 'TIME_LIMIT_BUILD'
            close(output['info']['objective'], initial['info']['objective']); continue
        for trip, pool in zip(initial['e2_trips'], check.read(directory/'columns.json')):
            tasks = [(e['order_id'], e['kind']) for e in trip['events']]
            assert any([tuple(k) for k in c['tasks']] == tasks for c in pool)
            for column in pool: replay_column(p, trip['satellite_id'], column); columns += 1
    lower = max(ref['bound'], payload['joint_certificate']['bound'])
    close(payload['bound'], lower); close(payload['upper_bound'], audit['objective'])
    close(payload['gap'], max(0, audit['objective']-lower)/max(1e-9, abs(audit['objective'])))
    assert payload['wall_seconds'] <= 150.5
    records.append(dict(id=job['id'], upper=payload['upper_bound'], lower=lower,
        gap_percent=100*payload['gap'], proof=proof, independently_equal_E2_domains=bool(job['full_source'])))
summary = dict(status='VERIFIED', cases=records, whole_cycle_audits=audits,
               independently_replayed_columns=columns, repaired_dual_checks=check.capacity.repair_checks)
(HERE/'validation_recovery.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2)+'\n')
print({k: v for k, v in summary.items() if k != 'cases'})
