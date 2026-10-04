"""Record the registered adoption gate without changing its denominator."""
import hashlib
import json
from pathlib import Path
import statistics
import time

OUT = Path(__file__).resolve().parent


def main():
    data = json.loads((OUT/'comparison.json').read_text())
    o = data['overview']
    verification = json.loads((OUT/'verification_final.json').read_text())
    assert verification['checked_records'] == 120
    assert verification['independent_audits_and_statistics_match']
    assert verification['frozen_inputs_and_sources_unchanged']
    passed = (o['jobs'] == 60 and o['before_valid'] == o['after_valid'] == 60
              and o['paired_mean_change_percent'] < 0
              and o['after_mean_cv_percent'] <= o['before_mean_cv_percent'])
    before = json.loads((OUT/'before/summary.json').read_text())
    after = json.loads((OUT/'after/summary.json').read_text())
    ratios = [(b['actual_iterations']/a['actual_iterations']-1)*100
              for a, b in zip(before, after) if a['actual_iterations']]
    decision = dict(created=time.strftime('%Y-%m-%dT%H:%M:%S'),
        status='ADOPTED_BY_REGISTERED_GATE' if passed else 'REJECTED_AFTER_FINAL_VALIDATION',
        candidate='candidate_04', default_adopted=passed, registered_gate='plan.json',
        selection='selection.json', overview=o,
        reason='All registered feasibility, mean paired distance and mean CV criteria passed.' if passed else
               'The complete registered comparison failed at least one distance/CV/feasibility criterion.',
        independently_verified_records=verification['valid_records'],
        exploratory_not_fully_blind=True, protocol_note='candidate_01/freeze.json',
        limitations=['Only three seeds per group; nested 50/100 cases and previously observed mother instances.',
                     'No large-case optimality certificate; do not claim universal stability or optimality.'])
    (OUT/'retention_decision.json').write_text(json.dumps(decision, ensure_ascii=False, indent=2)+'\n')
    diag = dict(iteration_change_mean_percent=statistics.mean(ratios),
        iteration_change_median_percent=statistics.median(ratios),
        max_solver_overrun_seconds=max(r['solver_runtime']-r['budget'] for r in after),
        timeouts=sum(r['status']=='OUTER_TIMEOUT' for r in after),
        tests_log='pytest_final.log', selected_intensification_sha256=hashlib.sha256(
            (OUT/'after/source/models/routing/solvers/alns/intensification.py').read_bytes()).hexdigest())
    (OUT/'final_diagnostics.json').write_text(json.dumps(diag, ensure_ascii=False, indent=2)+'\n')
    print(json.dumps({'decision': decision['status'], 'overview': o, 'diagnostics': diag},
                     ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
