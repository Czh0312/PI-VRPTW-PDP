"""Independent trip-partition and original tiny integer oracle validation."""
from pathlib import Path
import hashlib
import json
import math
import sys

ROOT = Path(__file__).resolve().parents[4]
OUT = Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT))
from certificate_sets import certificate_sets
from models.routing.solvers.exact.single_batch_columns import root_route_bound
from models.routing.solvers.exact.solver import solve_exact
from models.routing.common.cycle.audit import audit_solution
from tests.routing.test_single_batch_dual_cuts import check_all_partitions
from tests.routing.test_single_batch_solver_audit import tiny_problem,enumerate_tiny


def main():
    assert not (OUT/'proof.json').exists(),'Do not overwrite a completed proof'
    records = []
    for model in (1,2):
        for seed in range(8):
            p = tiny_problem(model,seed)
            oracle = enumerate_tiny(p)
            left = root_route_bound(p,time_limit=3,dual_cuts=True)
            right = root_route_bound(p,time_limit=3,dual_cuts=True,label_bound_pruning=True)
            if not math.isfinite(oracle):
                records.append(dict(model=model,seed=seed,finite=False,
                    left_infeasible=left['proven_infeasible'],right_infeasible=right['proven_infeasible']))
                continue
            assert not left['proven_infeasible'] and not right['proven_infeasible']
            options = certificate_sets(p,left,right)
            for name,cert in options.items():
                assert cert['bound']<=oracle+1e-8
                count = check_all_partitions(p,cert)
                assert count>0
                result = solve_exact(p,time_limit=3,seed=seed,verbose=False,
                    _single_batch_route_certificate=cert)
                audit = audit_solution(p,result)
                assert audit['valid'] and audit['periodic_replay']['valid']
                assert result['info']['proven_optimal'] and abs(audit['objective']-oracle)<1e-6
                records.append(dict(model=model,seed=seed,method=name,partitions_times_rows=count,
                    oracle=oracle,objective=audit['objective'],bound=result['info']['best_bound'],
                    audited=True,replay=True,full_integer_proof=True))
            wrong = dict(left,problem_sha256='wrong')
            try:
                certificate_sets(p,left,wrong)
            except ValueError:
                pass
            else:
                raise AssertionError('Mismatched fingerprint accepted')
    summary = dict(records=records,full_integer_tiny_proofs=sum(r.get('full_integer_proof',False) for r in records),
        partition_row_checks=sum(r.get('partitions_times_rows',0) for r in records),
        helper_sha256=hashlib.sha256((OUT/'certificate_sets.py').read_bytes()).hexdigest(),
        scope='Independent tiny original-integer oracle plus all relaxed trip partitions; no large proof claim')
    (OUT/'proof.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n')
    print({k:v for k,v in summary.items() if k!='records'})


if __name__=='__main__':
    main()
