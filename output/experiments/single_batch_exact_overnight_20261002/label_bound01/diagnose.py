"""Replay historical complete-MIP pricing duals; count frontier reduction.

These are deterministic state-count diagnostics under external machine load,
not clean CPU benchmarks or end-to-end improvement claims.
"""
from pathlib import Path
import hashlib
import json
import math
import sys
import time

ROOT = Path(__file__).resolve().parents[4]
OUT = Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT))
from configuration.loader import load_routing_case
from models.routing.common.cycle import load_instance
from models.routing.solvers.exact.single_batch_columns import problem_fingerprint
from models.routing.solvers.exact.single_batch_route_pricing import price_delivery_routes
from tests.routing.test_single_batch_columns import contexts


def write(path,value):
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False)+'\n')


def main():
    out = OUT/'diagnostic'
    out.mkdir()
    records = []
    for size in (20,30):
        batch = 'final_short' if size==20 else 'final_long'
        origin = ROOT/f'output/experiments/single_batch_pool_symmetry_20261001/{batch}/zhou_Set2_100-5-1b_subset{size}_M2'
        case = origin/'case.json'
        cfg,fleet = load_routing_case(case)
        problem = load_instance(cfg,fleet)
        assert len({o.customer for o in problem.orders})==len(problem.orders)
        _,_,by_site = contexts(problem)
        for seed in (42,43):
            source = origin/f'columns_compact_{seed}/result.json'
            cert = json.loads(source.read_text())['info']['route_bound_certificate']
            assert cert['problem_sha256']==problem_fingerprint(problem)
            cuts = [c for c in cert['dual_cuts'] if c['kind']=='route'][-2:]
            for cut in cuts:
                row = next(h for h in cert['history'] if h['iteration']==cut['iteration'])
                assert row['phase']==2
                for site,trip_value in cut['trip_terms']:
                    priced = row['pricing'][site]
                    assert priced['complete'] and not priced['infeasible']
                    context = by_site[site]
                    term = {(s,c):v for s,c,v in cut['assignment_terms']}
                    prices = {node['key']:term[site,node['order'].customer] for node in context['nodes']}
                    fleet_price = min(0.0,trip_value-min(0.0,priced['lower_bound']))
                    name = f'n{size}_seed{seed}_iter{cut["iteration"]}_{site}'
                    folder = out/name
                    folder.mkdir()
                    oracle = priced['minimum_reduced_cost']
                    job = dict(case_path=str(case),source=str(source),source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
                        problem_sha256=cert['problem_sha256'],site=site,prices=prices,fleet_price=fleet_price,
                        iteration=cut['iteration'],certified_complete_mip_minimum=oracle,label_limit=50000,
                        scope='Fixed historical complete-MIP dual replay; external CPU load, no deadline or timing superiority claim')
                    write(folder/'job.json',job)
                    pair = {}
                    for method in (('baseline','bounded') if seed==42 else ('bounded','baseline')):
                        begin = time.process_time()
                        result = price_delivery_routes(problem,context,prices,fleet_price,label_limit=50000,
                            bound_pruning=method=='bounded')
                        result['cpu_seconds_observed'] = time.process_time()-begin
                        assert result['lower_bound']<=oracle+1e-8
                        if result['complete']:
                            assert math.isclose(result['minimum_reduced_cost'],oracle,abs_tol=1e-6)
                        write(folder/f'{method}.json',result)
                        pair[method] = result
                    old,new = pair['baseline'],pair['bounded']
                    record = dict(name=name,customers=size,seed=seed,site=site,iteration=cut['iteration'],
                        oracle=oracle,baseline_complete=old['complete'],bounded_complete=new['complete'],
                        baseline_generated=old['generated_labels'],bounded_generated=new['generated_labels'],
                        baseline_expanded=old['expanded_labels'],bounded_expanded=new['expanded_labels'],
                        baseline_lower=old['lower_bound'],bounded_lower=new['lower_bound'],
                        bound_closed=new['bound_closed'],minimum_safe=True,
                        baseline_cpu_observed=old['cpu_seconds_observed'],bounded_cpu_observed=new['cpu_seconds_observed'])
                    records.append(record)
                    write(out/'comparison.json',records)
                    print(f'{name}: expanded {old["expanded_labels"]}->{new["expanded_labels"]}, '
                          f'complete {old["complete"]}->{new["complete"]}',flush=True)
    write(out/'summary.json',dict(pricing_pairs=len(records),records=records,
        baseline_expanded=sum(r['baseline_expanded'] for r in records),
        bounded_expanded=sum(r['bounded_expanded'] for r in records),
        bounded_closures=sum(r['bound_closed'] for r in records),
        bound_and_complete_minimum_checks_passed=True,
        limitations='Historical duals from one source at two sizes; CPU load uncontrolled; no overall speed or default adoption claim.'))


if __name__=='__main__':
    main()
