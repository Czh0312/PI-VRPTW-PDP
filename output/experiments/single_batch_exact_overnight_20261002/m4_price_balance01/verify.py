"""Frozen pair/schedule/dual checks; independent MIP only for positive witnesses."""
import math
import time
from common import OUT,read,write,sha,frozen_cases,load_case,gate
from balance import transformed_prices,explicit_dual_bound
from models.routing.common.cycle.audit import audit_solution
from models.routing.solvers.exact.single_batch_pickup_bound import pickup_contexts,project_original_schedule
from models.routing.solvers.exact.single_batch_columns import problem_fingerprint
from models.routing.solvers.exact.multibatch_route_pricing import TripPricingModel


def main():
    target=OUT/'prices5';out=OUT/'verification';out.mkdir()
    manifest=read(target/'manifest.json')
    for name,h in manifest['driver_hashes'].items():assert sha(OUT/name)==h==sha(target/name)
    prepared=read(OUT/'prepared_manifest.json');assert sha(OUT/'prepared_manifest.json')==manifest['prepared_manifest_sha256']
    for path,h in prepared['runtime_source_sha256'].items():assert sha(OUT/'source_snapshot'/path)==h
    for path,v in prepared['input_and_original_logs_sha256'].items():assert sha(OUT/path)==v['sha256']
    rows=read(target/'comparison.json');assert len(rows)==16;problems={}
    for _,case,folder in frozen_cases():problems[case]=(load_case(folder),folder)
    checks=[];independent=[];pairs={};known=read(OUT/'analytic_ceiling.json')['rows']
    for row in rows:
        state=f'state_{row["historical_budget"]}_{row["seed"]}';run=target/row['case']/state/('balanced' if row['balanced'] else 'original')
        assert read(run/'row.json')==row
        result=read(run/'result.json');job=read(run/'job.json');history=read(job['fixed_history_path'])
        p,folder=problems[row['case']];start=read(folder/'start.json');a=audit_solution(p,start)
        assert a['valid'] and a['periodic_replay']['valid'];_,contexts,check=pickup_contexts(p);assert check['passed']
        columns=project_original_schedule(p,start,contexts);assert sum(len(c['tasks']) for _,c in columns)==40
        assert row['problem_sha256']==result['problem_sha256']==problem_fingerprint(p)
        assert row['start_sha256']==sha(folder/'start.json') and row['fixed_history_sha256']==sha(job['fixed_history_path'])
        prices,gamma=transformed_prices(p,history,row['balanced'])
        assert result['prices']=={s:dict(tasks=[[list(k),v] for k,v in pi.items()],fleet=nu) for s,(pi,nu) in prices.items()}
        assert result['assignment_duals']==gamma and not result['old_rho_reused']
        cert=explicit_dual_bound(prices,gamma,job['maximum_trips'],result['pricing_lower_bounds'])
        for key,value in cert.items():assert abs(value-result[key])<1e-7
        assert result['e2_bound']<=a['distances']['e2']+1e-6 and not result['proven_integer_optimal']
        ceiling=next(k['raw_certificate_upper_ceiling'] for k in known if (k['case'],k['state'],k['balanced'])==
            (row['case'],f'baseline_{row["historical_budget"]}_{row["seed"]}',row['balanced']))
        assert result['raw_corrected']<=ceiling+1e-6
        if result['e2_bound']>0:
            for site,(pi,nu) in prices.items():
                gate('independent positive fixed-dual pricing witness')
                deadline=time.perf_counter()+3
                pricer=TripPricingModel(p,contexts[site],seed=42,deadline=deadline)
                try:rec=pricer.price(pi,nu,time_limit=max(0,deadline-time.perf_counter()))
                finally:pricer.dispose()
                lower=rec['reduced_cost'] if rec['optimal'] else rec['lower_bound']
                valid=rec['infeasible'] or result['pricing_lower_bounds'][site]<=lower+1e-6
                independent.append(dict(case=row['case'],state=state,balanced=row['balanced'],site=site,
                    reported=result['pricing_lower_bounds'][site],independent=rec,checked=valid))
                write(out/'independent_positive_prices.json',independent)
        checks.append(dict(case=row['case'],state=state,balanced=row['balanced'],original_all_tasks=40,
            audited=True,replay=True,raw_certificate_formula=True,original_input_fingerprint=True,
            e2_bound=result['e2_bound'],original_integer_proof=False))
        pairs.setdefault((row['case'],row['historical_budget'],row['seed']),{})[row['balanced']]=row
    comparisons=[]
    for (case,budget,seed),pair in pairs.items():
        assert set(pair)=={False,True};b,c=pair[False],pair[True]
        assert b['start_sha256']==c['start_sha256'] and b['fixed_history_sha256']==c['fixed_history_sha256']
        comparisons.append(dict(case=case,historical_budget=budget,seed=seed,original=b,balanced=c,
            raw_corrected_change=c['raw_corrected']-b['raw_corrected'],e2_bound_change=c['e2_bound']-b['e2_bound']))
    assert len(comparisons)==8
    summary=dict(successful_pricing_jobs=16,matched_pairs=8,original_schedule_audits=16,periodic_replays=16,
        positive_witness_independent_prices=len(independent),independent_checked=all(x['checked'] for x in independent),
        positive_E2_jobs=sum(x['e2_bound']>0 for x in checks),original_integer_proofs=0,
        limitation='All zero E2 floors are valid by nonnegative distance; discarded negative large planes not independently repriced. Fixed-dual test is not full CG/integer speed evidence.')
    write(out/'summary.json',summary);write(out/'pairs.json',comparisons);write(out/'checks.json',checks)
    assert summary['independent_checked'];print(summary)


if __name__=='__main__':main()
