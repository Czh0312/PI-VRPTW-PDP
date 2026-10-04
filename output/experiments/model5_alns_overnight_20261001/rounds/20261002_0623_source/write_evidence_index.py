"""Build a read-only index from sealed proofs; retain pending/failed outcomes."""
from datetime import datetime, timezone
import hashlib, json
from pathlib import Path

HERE = Path(__file__).resolve().parent

def read(p):
    return json.loads(p.read_text())

def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()

checkpoint = read(HERE / 'checkpoint.json')
entries = []
for probe in sorted((HERE / 'probes').iterdir()):
    if not probe.is_dir() or not (probe / 'plan.json').exists():
        continue
    plan = read(probe / 'plan.json')
    kind = ('native_public1500' if probe.name.startswith('public1500_') else
            'warm_component_or_continuation' if probe.name in ['budget01', 'dual01', 'dual02'] else
            'fresh_shared_initial_pair')
    seal_file = probe / 'final_verification.json'
    row = dict(probe=probe.name, execution_kind=kind,
               plan=str((probe / 'plan.json').resolve()), plan_sha256=sha(probe / 'plan.json'),
               registered_scope=plan.get('scope'), status='REGISTERED_OR_RUNNING')
    if seal_file.exists():
        seal = read(seal_file)
        assert seal['status'] == 'VERIFIED', probe.name
        files = seal.get('artifact_sha256', seal.get('artifacts_sha256'))
        assert files is not None
        for relative, digest in files.items():
            assert sha(probe / relative) == digest, (probe.name, relative)
        validation_file = probe / ('validation_v2.json' if probe.name == 'resume02' else 'validation.json')
        verification = read(validation_file)
        assert verification['status'] == 'VERIFIED'
        assert files[validation_file.name] == sha(validation_file)
        rows = verification.get('case_results', verification.get('cases', []))
        row.update(status='VERIFIED_SEALED', validation=str(validation_file.resolve()),
                   validation_sha256=sha(validation_file), final_seal=str(seal_file.resolve()),
                   final_seal_sha256=sha(seal_file), cases=rows,
                   proof_interpretation=verification['interpretation'],
                   model_checks=len(verification['model_checks']) if isinstance(verification['model_checks'], list) else verification['model_checks'],
                   whole_cycle_audits=verification['whole_cycle_audits'])
    elif (probe / 'failure_artifact_manifest.json').exists():
        partial_file = probe / 'failure_artifact_manifest.json'
        partial = read(partial_file)
        assert partial['status'] == 'SEALED_REFERENCE_ONLY_WITH_ORIGINAL_MASTER_FAILURE'
        for relative, digest in partial['artifact_sha256'].items():
            assert sha(probe / relative) == digest, (probe.name, relative)
        validation_file = probe / 'validation_reference_only.json'
        verification = read(validation_file)
        assert verification['status'] == 'VERIFIED_REFERENCE_ONLY'
        row.update(status='SEALED_REFERENCE_ONLY_ORIGINAL_MASTER_FAILED',
                   validation=str(validation_file.resolve()), cases=verification['case_results'],
                   proof_interpretation=verification['interpretation'],
                   excluded_original_master=verification['excluded_original_master'],
                   partial_evidence_seal=str(partial_file.resolve()),
                   model_checks=len(verification['model_checks']),
                   whole_cycle_audits=verification['whole_cycle_audits'],
                   original_failed_validation_log=str((probe / 'validation.log').resolve()),
                   raw_unverified_results=read(probe / 'summary.json'))
    else:
        if (probe / 'summary.json').exists():
            row['raw_unverified_results'] = read(probe / 'summary.json')
        if (probe / 'failure.json').exists():
            row['failure'] = read(probe / 'failure.json')
        if str(probe.resolve()) == checkpoint.get('current_probe'):
            row['active_process'] = checkpoint.get('running')
            row['tool_session_id'] = checkpoint.get('tool_session_id')
        registration = checkpoint.get(probe.name + '_registered')
        if registration:
            row['checkpoint_registration_status'] = registration['status']
    entries.append(row)

out = dict(updated_utc=datetime.now(timezone.utc).isoformat(),
           instance_scope='ALNS overnight100-customer5-satellite3-batch full-pool155 only; distinct exact-chat cases excluded.',
           best_cross_run=checkpoint['best_full'],
           best_shared_fresh1500=checkpoint.get('best_fresh1500_pipeline'),
           best_native_public1500=checkpoint.get('best_native_public1500_pipeline'),
           verified_native_runs=checkpoint.get('native_public1500_runs', []),
           experiments=entries,
           interpretation='Each pipeline keeps its own certified bound/gap and registered budget. Cross-run historical records, warm probes and fresh-shared-initial experiments are separate from native public calls. No pooled success rate or stability estimate.')
(HERE / 'evidence_index.json').write_text(json.dumps(out, indent=2, ensure_ascii=False, allow_nan=False) + '\n')
print('INDEXED', len(entries), 'registered experiments;', sum(row['status'] == 'VERIFIED_SEALED' for row in entries), 'sealed proofs')
