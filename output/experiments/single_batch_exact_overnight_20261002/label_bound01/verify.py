"""Frozen provenance, full schedules and independent integer-MIP price checks."""
from pathlib import Path
import argparse
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
from models.routing.common.cycle.audit import audit_solution
from models.routing.common.cycle.precheck import travel_lower_bounds
from models.routing.solvers.exact.domains import assignment_domains
from models.routing.solvers.exact.single_batch_route_pricing import delivery_context,price_delivery_routes
from models.routing.solvers.exact.single_batch_columns import problem_fingerprint
from models.routing.solvers.exact.single_batch_delivery_pricer import DeliveryMipPricer


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read(path):
    return json.loads(path.read_text())


def write(path,value):
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False)+'\n')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--batch',default='full60')
    args = parser.parse_args()
    batch = OUT/args.batch
    target = batch/'verification'
    target.mkdir()
    manifest = read(batch/'manifest.json')
    assert sha(OUT/'compare.py')==manifest['script_sha256']
    for item in manifest['input_sha256'].values():
        assert sha(batch/item['snapshot'])==item['sha256']
    for path,value in manifest['source_sha256'].items():
        assert sha(batch/'source_snapshot'/path)==value
    rows = read(batch/'comparison.json')
    assert len(rows)==manifest['job_count'] and manifest['threads']==1
    records,prices,groups = [],[],{}
    for index,row in enumerate(rows):
        run = batch/row['case']/f'{row["method"]}_{row["seed"]}'
        assert row==read(run/'row.json')
        job,result = read(run/'job.json'),read(run/'result.json')
        cfg,fleet = load_routing_case(job['case_path'])
        p = load_instance(cfg,fleet)
        assert len({o.customer for o in p.orders})==len(p.orders)
        initial = audit_solution(p,read(Path(job['start_path'])))
        audit = audit_solution(p,result)
        assert initial['valid'] and initial['periodic_replay']['valid']
        assert audit['valid'] and audit['periodic_replay']['valid']
        assert audit['objective']<=initial['objective']+1e-6
        assert math.isclose(audit['objective'],row['objective'],abs_tol=1e-6)
        info = result['info'];cert = info['route_bound_certificate']
        assert cert['problem_sha256']==problem_fingerprint(p)
        assert cert['proof_scope']=='B1_delivery_route_relaxation' and not cert['proven_optimal']
        assert not cert['proven_infeasible'] and not info['proven_infeasible']
        assert not info.get('route_pool_primal',{}).get('global_proof',False)
        assert info['proven_optimal']==(info['status']=='OPTIMAL')
        assert cert['bound']<=info['best_bound']<=audit['objective']+1e-6
        assert cert['label_bound_pruning']==(row['method']=='bounded')
        assert row['wall_time']<row['budget']+1
        record = {**row,'start_sha256':sha(Path(job['start_path'])),'problem_sha256':cert['problem_sha256']}
        records.append(record);groups.setdefault((row['case'],row['seed']),{})[row['method']] = record
        travel = travel_lower_bounds(p);check = assignment_domains(p,travel)
        contexts = {s:delivery_context(p,s,check['domains'],check['windows'],travel)
                    for s in p.satellites if any(s in d for d in check['domains'].values())}
        cuts = [c for c in cert.get('dual_cuts',[]) if c['kind']=='route']
        if cuts:
            final = cuts[-1]
            history = next(h for h in cert['history'] if h['iteration']==final['iteration'])
            terms = {(s,c):v for s,c,v in final['assignment_terms']}
            for site,coefficient in final['trip_terms']:
                priced = history['pricing'].get(site)
                if priced is None:
                    continue
                context = contexts[site]
                cover = {n['key']:terms[site,n['order'].customer] for n in context['nodes']}
                fleet_price = min(0.0,coefficient-min(0.0,priced['lower_bound']))
                deadline = time.perf_counter()+4
                pricer = DeliveryMipPricer(p,context,seed=42,deadline=deadline)
                try:
                    independent = pricer.price(cover,fleet_price,deadline=deadline)
                finally:
                    pricer.dispose()
                checked = independent['complete'] and not independent['infeasible']
                if checked:
                    minimum = independent['minimum_reduced_cost']
                    assert priced['lower_bound']<=minimum+1e-8
                    if priced['complete']:
                        assert math.isclose(priced['minimum_reduced_cost'],minimum,abs_tol=1e-6)
                else:
                    # Independent full enumeration is slower, but its complete
                    # value can settle a price if the MIP's four seconds expire.
                    exact = price_delivery_routes(p,context,cover,fleet_price,label_limit=1000000)
                    assert exact['complete'], 'Large pricing cross-check inconclusive; retained'
                    assert priced['lower_bound']<=exact['minimum_reduced_cost']+1e-8
                    checked = True
                item = dict(run=f'{row["case"]}/{row["method"]}_{row["seed"]}',site=site,
                    iteration=history['iteration'],reported_lower=priced['lower_bound'],
                    reported_complete=priced['complete'],reported_minimum=priced['minimum_reduced_cost'],
                    independent=independent,checked=checked)
                prices.append(item)
                write(target/'pricing_checks.json',prices)
        write(target/'audited_rows.json',records)
        print(f'audited {index+1}/{len(rows)} {row["case"]} {row["method"]}/{row["seed"]}',flush=True)
    pairs = []
    for (case,seed),group in groups.items():
        old,new = group['baseline'],group['bounded']
        assert old['start_sha256']==new['start_sha256'] and old['problem_sha256']==new['problem_sha256']
        pairs.append(dict(case=case,customers=old['customers'],seed=seed,
            objective_before=old['objective'],objective_after=new['objective'],
            bound_before=old['bound'],bound_after=new['bound'],gap_before=old['gap'],gap_after=new['gap'],
            root_before=old['root_runtime'],root_after=new['root_runtime'],
            root_complete_before=old['root_complete'],root_complete_after=new['root_complete'],
            distance_improved=new['objective']<old['objective']-1e-6,
            distance_regressed=new['objective']>old['objective']+1e-6))
    summary = dict(audits=len(records),replays=len(records),pricing_checks=len(prices),
        complete_mip_price_checks=sum(r['independent']['complete'] for r in prices),
        all_pricing_checks_passed=all(r['checked'] for r in prices),
        integer_global_optimal_proofs=sum(r['proven_optimal'] for r in records),records=records,pairs=pairs,
        limitation='Final retained dual plane checked, not a claim of exporting/replaying every historical master dual or clean machine timing.')
    write(target/'summary.json',summary)
    print(json.dumps({k:v for k,v in summary.items() if k not in ('records','pairs')},ensure_ascii=False))


if __name__=='__main__':
    main()
