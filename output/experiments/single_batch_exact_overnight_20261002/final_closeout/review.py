"""Final read-only integrity/ledger/default review; no solver or tests."""
from pathlib import Path
from datetime import datetime, timezone
import ast
import hashlib
import json
import math
import time

OUT = Path(__file__).resolve().parent
NIGHT = OUT.parent
ROOT = NIGHT.parents[2]


def read(p):
    return json.loads(p.read_text())


def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def defaults(file, function):
    tree = ast.parse((ROOT/file).read_text())
    node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == function)
    result = {}
    args = node.args.posonlyargs+node.args.args
    for arg, value in zip(args[len(args)-len(node.args.defaults):], node.args.defaults):
        result[arg.arg] = ast.literal_eval(value)
    for arg, value in zip(node.args.kwonlyargs, node.args.kw_defaults):
        if value is not None:
            result[arg.arg] = ast.literal_eval(value)
    return result


def main():
    assert not (OUT/'review_summary.json').exists(), 'Keep first final review unchanged'
    write_plan = dict(registered_utc=datetime.now(timezone.utc).isoformat(),
                      scope='Final read-only hashes/defaults/arithmetic; no model build, solve, test or experiment.',
                      preserve_historical_manifests=True, no_retry_or_promoted_result=True)
    assert not (OUT/'plan.json').exists()
    (OUT/'plan.json').write_text(json.dumps(write_plan, indent=2)+'\n')
    begun = time.perf_counter()
    earlier = read(NIGHT/'closing_evidence_review01/summary.json')
    targets = [(ROOT/s['manifest']) for s in earlier['stages']]
    targets += [NIGHT/'m4_price_balance01/final_manifest.json',
                NIGHT/'closing_evidence_review01/final_manifest.json']
    checked = []
    seals = []
    for manifest_path in targets:
        data = read(manifest_path)
        files = data.get('files_sha256', data)
        for relative, expected in files.items():
            file_path = manifest_path.parent/relative if 'files_sha256' in data else ROOT/relative
            actual = sha(file_path) if file_path.is_file() else None
            checked.append(dict(manifest=str(manifest_path.relative_to(ROOT)), file=str(file_path),
                                expected_sha256=expected, actual_sha256=actual, matched=actual == expected))
        seals.append(dict(manifest=str(manifest_path.relative_to(ROOT)), sha256=sha(manifest_path),
                          entries=len(files)))
    nested = read(NIGHT/'closing_evidence_review01/nested_entries.json')
    nested_failures = [r for r in nested if not Path(r['file']).is_file() or sha(Path(r['file'])) != r['expected_sha256']]
    records = read(NIGHT/'morning_summary01/all_integer_records.json')
    ledger_failures = []
    for row in records:
        for file_key, hash_key in (('result', 'result_sha256'), ('case_file', 'case_sha256')):
            if not (ROOT/row[file_key]).is_file() or sha(ROOT/row[file_key]) != row[hash_key]:
                ledger_failures.append(dict(row=row, bad_field=file_key))
    groups = {}
    for row in records:
        groups.setdefault(row['problem_sha256'], []).append(row)
    best = read(NIGHT/'morning_summary01/best_bounds.json')
    candidates = next(v for v in best.values() if isinstance(v, list))
    assert len(candidates) == len(groups) == 8
    table_checks = []
    for row in candidates:
        original = groups[row['problem_sha256']]
        upper = min(r['UB'] for r in original)
        lower = max(r['LB'] for r in original)
        gap = max(0.0, (upper-lower)/abs(upper))
        assert abs(upper-row['UB']) < 1e-7 and abs(lower-row['LB']) < 1e-7
        assert abs(gap-row['Gap']) < 1e-10
        table_checks.append(dict(problem_sha256=row['problem_sha256'], model=row['model'],
                                 UB=upper, LB=lower, Gap=gap,
                                 original_integer_proof=any(r['original_integer_proof'] for r in original)))
    cp = read(NIGHT/'checkpoint.json')
    owned = {p: sha(ROOT/p) == h for p, h in cp['owned_sha256'].items()}
    requirements = [
        ('models/routing/solvers/exact/solver.py', 'solve_exact',
         {'_single_batch_cycle_cuts': False, '_single_batch_causal_bounds': False,
          '_single_batch_e1_certificate': None}),
        ('models/routing/solvers/exact/single_batch_route_pricing.py', 'price_delivery_routes',
         {'bound_pruning': False}),
        ('models/routing/solvers/exact/single_batch_columns.py', 'root_route_bound',
         {'label_bound_pruning': False}),
        ('models/routing/solvers/exact/single_batch_pickup_bound.py', 'root_pickup_route_bound',
         {'heuristic_columns': False, 'e1_route_cover': False}),
        ('models/routing/solvers/exact/single_batch_e1_bound.py', 'e1_route_cover_bound', {'clocked': False}),
        ('models/routing/solvers/exact/single_batch_e1_bound.py', 'solve_exact_with_e1_cover', {'clocked': False}),
    ]
    option_checks = []
    for file, function, expected in requirements:
        actual = defaults(file, function)
        for key, value in expected.items():
            assert actual[key] is value
        option_checks.append(dict(file=file, function=function, expected_defaults=expected, passed=True))
    budget = read(NIGHT/'morning_summary01/final_budget_0657.json')
    assert budget['comparisons'] == 138 and budget['registered_seconds'] == 3560
    assert abs(budget['measured_stage_seconds']-2839.224004374) < 1e-8
    assert not cp['active_process'] and not cp['active_tool_session_id']
    assert not cp['balance_pipeline_session'] and not cp['balance_pipeline_pid']
    summary = dict(observed_utc=datetime.now(timezone.utc).isoformat(), final_seals=seals,
        final_seal_entries=len(checked), all_final_seal_entries_match=all(r['matched'] for r in checked),
        seal_mismatches=[r for r in checked if not r['matched']], nested_entries=len(nested),
        nested_mismatches=nested_failures, original_integer_records=len(records),
        ledger_hash_failures=ledger_failures, full_problem_fingerprints=len(groups),
        best_interval_table=table_checks, proven_distinct_original_cases=sum(r['original_integer_proof'] for r in table_checks),
        owned=owned, all_eleven_owned_unchanged=all(owned.values()), defaults=option_checks,
        no_own_active_solver=True, no_solver_created=True, seconds=time.perf_counter()-begun,
        limitation='Final integrity/arithmetic/default review, not new mathematical proof or reexecution of regression. Snapshot-version coverage and negative-price limitations retained.')
    (OUT/'review_entries.json').write_text(json.dumps(checked, indent=2)+'\n')
    (OUT/'review_summary.json').write_text(json.dumps(summary, indent=2)+'\n')
    assert summary['all_final_seal_entries_match'] and not nested_failures and not ledger_failures and all(owned.values())
    print(json.dumps({k: v for k, v in summary.items() if k not in ('owned', 'defaults', 'best_interval_table')}, indent=2))


if __name__ == '__main__':
    main()
