"""Evidence ledger of full integer results; cross-budget bests are not speed comparisons."""
from pathlib import Path
from datetime import datetime, timezone
import hashlib
import json
import math
import sys

OUT = Path(__file__).resolve().parent
NIGHT = OUT.parent
ROOT = NIGHT.parents[2]
sys.path.insert(0, str(NIGHT/'m4_dual_repair01/source_snapshot'))
from configuration.loader import load_routing_case
from models.routing.common.cycle import load_instance
from models.routing.solvers.exact.single_batch_columns import problem_fingerprint


def read(p): return json.loads(p.read_text())
def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()
def write(p, v): p.write_text(json.dumps(v, indent=2, allow_nan=False)+'\n')


def main():
    paths = []
    previous = ROOT/'output/experiments/single_batch_short_cycles_20261001'
    for row in read(previous/'summary.json')['records']:
        paths.append((previous/row['batch']/row['case']/f'{row["method"]}_{row["seed"]}'/'result.json',
                      row['budget'], 'prior36_full_model_audits_and_replays', row.get('problem_sha256')))
    for batch in ['label_bound01/full60', 'label_bound01/full60_v2', 'certificate_ablation01/full30',
                  'e1_component_kernel01/full15_30', 'e1_clocked_component_kernel01/full15_30']:
        for row in read(NIGHT/batch/'comparison.json'):
            method = row.get('method', 'candidate' if row.get('candidate') else 'baseline')
            name = f'{method}_{row["budget"]}_{row["seed"]}' if batch.startswith('e1_') else f'{method}_{row["seed"]}'
            paths.append((NIGHT/batch/row['case']/name/'result.json', row['budget'], batch+'/verification', row.get('problem_sha256')))
    for batch in ['pickup_column_supply01/independent_setup20', 'e1_route_cover01/independent_setup20']:
        paths.append((NIGHT/batch/'result.json', 20, 'setup_original_schedule_verified', None))
    records = []; contexts = {}; seen = set()
    for file, budget, audit_reference, expected_fp in paths:
        assert file not in seen; seen.add(file)
        result = read(file); info = result['info']
        case = file.parent.parent/'case.json'
        if not case.exists(): case = file.parent/'case.json'
        if case not in contexts:
            cfg, fleet = load_routing_case(case); p = load_instance(cfg, fleet)
            contexts[case] = p
        p = contexts[case]; fp = problem_fingerprint(p)
        if expected_fp: assert expected_fp == fp
        cert = info.get('route_bound_certificate')
        if cert and cert.get('problem_sha256'): assert cert['problem_sha256'] == fp
        ub, lb = float(info['objective']), float(info['best_bound'])
        assert math.isfinite(ub) and math.isfinite(lb) and lb <= ub+1e-6
        records.append(dict(model=p.model_number, case=p.name if hasattr(p, 'name') else case.parent.name,
                            problem_sha256=fp, UB=ub, LB=lb, Gap=max(0.0, ub-lb)/abs(ub),
                            budget=budget, original_integer_proof=bool(info['proven_optimal']),
                            proof_scope=info['proof_scope'], result=str(file.relative_to(ROOT)),
                            result_sha256=sha(file), case_file=str(case.relative_to(ROOT)), case_sha256=sha(case),
                            existing_audit_reference=audit_reference))
    groups = {}
    for row in records: groups.setdefault((row['model'], row['problem_sha256']), []).append(row)
    bests = []
    for (model, fp), rows in sorted(groups.items()):
        upper = min(rows, key=lambda r: r['UB']); lower = max(rows, key=lambda r: r['LB'])
        bests.append(dict(model=model, problem_sha256=fp, case=upper['case'], records=len(rows),
                          UB=upper['UB'], LB=lower['LB'], Gap=max(0.0, upper['UB']-lower['LB'])/abs(upper['UB']),
                          upper_witness=upper, lower_witness=lower,
                          original_proof_records=sum(r['original_integer_proof'] for r in rows)))
    summary = dict(as_of_utc=datetime.now(timezone.utc).isoformat(), full_integer_records=len(records),
                   distinct_input_model_fingerprints=len(bests), adopted_new_defaults=[], bests=bests,
                   limitation='Only identical full problem fingerprints are combined. Best UB/LB may come from different budgets, methods or offline ablation; never a same-budget speed claim. Existing independent audits referenced; no new integer solving. Prior M1/M4 records explicitly included as prior evidence.')
    write(OUT/'all_integer_records.json', records); write(OUT/'best_bounds.json', summary)
    print(json.dumps({k:v for k,v in summary.items() if k!='bests'}))
    for row in bests: print(row['model'], row['case'], row['UB'], row['LB'], row['Gap'], row['original_proof_records'], row['problem_sha256'])


if __name__ == '__main__': main()
