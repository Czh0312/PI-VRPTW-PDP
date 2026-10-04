"""Re-audit all full schedules and independently price every used cut plane."""
from pathlib import Path
import hashlib
import json
import math
import sys
import time

ROOT = Path(__file__).resolve().parents[4]
OUT = Path(__file__).resolve().parent
BATCH = OUT/'full30'
sys.path.insert(0,str(BATCH/'source_snapshot'))
from configuration.loader import load_routing_case
from models.routing.common.cycle import load_instance
from models.routing.common.cycle.audit import audit_solution
from models.routing.common.cycle.precheck import travel_lower_bounds
from models.routing.solvers.exact.domains import assignment_domains
from models.routing.solvers.exact.single_batch_columns import problem_fingerprint
from models.routing.solvers.exact.single_batch_route_pricing import delivery_context
from models.routing.solvers.exact.single_batch_delivery_pricer import DeliveryMipPricer
from run import fixed_schedule_planes


def read(path):
    return json.loads(path.read_text())


def write(path,data):
    path.write_text(json.dumps(data,ensure_ascii=False,indent=2,allow_nan=False)+'\n')


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    target = BATCH/'verification_v2';target.mkdir()
    manifest = read(BATCH/'manifest.json')
    assert not (BATCH/'failure.json').exists()
    for p,h in manifest['source_sha256'].items():
        assert sha(BATCH/'source_snapshot'/p)==h
    for item in manifest['input_sha256'].values():
        assert sha(BATCH/item['snapshot'])==item['sha256']
    for name,h in manifest['experiment_source_sha256'].items():
        assert sha(OUT/name)==h==sha(BATCH/'experiment_source'/name)
    rows = read(BATCH/'comparison.json');assert len(rows)==manifest['job_count']==18
    grouped,records,planes = {},[],[]
    for row in rows:
        run = BATCH/row['case']/f'{row["method"]}_{row["seed"]}'
        assert row==read(run/'row.json')
        job,result = read(run/'job.json'),read(run/'result.json')
        cfg,fleet = load_routing_case(job['case_path']);p = load_instance(cfg,fleet)
        start,cert = read(Path(job['start_path'])),read(Path(job['certificate_path']))
        assert cert['problem_sha256']==problem_fingerprint(p)
        assert not cert['proven_optimal'] and not cert['proven_infeasible']
        assert row['start_sha256']==sha(Path(job['start_path']))
        assert row['certificate_sha256']==sha(Path(job['certificate_path']))
        projection = fixed_schedule_planes(p,start,cert)
        assert projection==row['plane_projection']
        audit = audit_solution(p,result)
        assert audit['valid'] and audit['periodic_replay']['valid']
        assert abs(audit['objective']-row['objective'])<1e-6
        assert cert['bound']<=row['bound']+1e-6 and row['bound']<=row['objective']+1e-6
        assert row['objective']<=row['initial_objective']+1e-6
        assert row['wall_time']<row['budget']+1
        assert not row['proven_infeasible']
        assert row['proven_optimal']==(row['status']=='OPTIMAL')
        assert row['dual_rows']==len(cert['dual_cuts'])
        assert result['info']['proof_scope']=='joint_customer_assignment_integer_time_cycle'
        assert result['info']['mip_start_supplied']
        grouped.setdefault((row['case'],row['seed']),[]).append(row)
        records.append(row)
    for group in grouped.values():
        assert len(group)==3
        assert len({r['start_sha256'] for r in group})==1
        assert len({r['scalar_floor'] for r in group})==1
        assert len({r['initial_objective'] for r in group})==1
    for case,origin in manifest['origins'].items():
        for item in origin['audited_candidates']+list(origin['certificate_origins'].values()):
            assert sha(Path(item['path']))==item['sha256']
        folder = BATCH/case
        cfg,fleet = load_routing_case(folder/'case.json');p = load_instance(cfg,fleet)
        assert len({o.customer for o in p.orders})==len(p.orders)
        travel = travel_lower_bounds(p);check = assignment_domains(p,travel)
        contexts = {s:delivery_context(p,s,check['domains'],check['windows'],travel)
                    for s in p.satellites if any(s in d for d in check['domains'].values())}
        union = read(folder/'certificate_union.json')
        seen = set()
        pricers = {}
        for cut in union['dual_cuts']:
            # Iteration label is provenance, not part of the inequality.
            identity = json.dumps({k:v for k,v in cut.items() if k!='iteration'},sort_keys=True)
            if identity in seen:continue
            seen.add(identity)
            terms = {(s,c):v for s,c,v in cut['assignment_terms']}
            for site,coefficient in cut['trip_terms']:
                context = contexts[site]
                cover = {n['key']:terms[site,n['order'].customer] for n in context['nodes']}
                # Directly price the transferred coefficients: no reconstruction
                # from the experimental label's minimum or interrupted history.
                deadline = time.perf_counter()+2
                try:
                    if site not in pricers:
                        pricers[site] = DeliveryMipPricer(p,context,seed=42,deadline=deadline)
                    pricer = pricers[site]
                    priced = pricer.price(cover,coefficient,deadline=deadline)
                except TimeoutError as error:
                    priced = dict(complete=False,infeasible=False,lower_bound=None,error=str(error))
                # At most N nonempty actual delivery trips cover N tasks.
                # This row's own margin absorbs the summed numeric correction.
                threshold = -cut['margin']/len(p.orders)
                lower = priced.get('lower_bound')
                valid = priced.get('infeasible',False) or (lower is not None and math.isfinite(lower)
                    and lower>=threshold)
                planes.append(dict(case=case,kind=cut['kind'],iteration=cut['iteration'],site=site,
                    coefficient=coefficient,row_margin=cut['margin'],per_trip_threshold=threshold,
                    independent=priced,proved_with_independent_MIP=valid))
                write(target/'plane_checks.json',planes)
        for pricer in pricers.values():pricer.dispose()
        print(f'priced all retained planes for {case}',flush=True)
    summary = dict(audits=len(records),periodic_replays=len(records),groups=len(grouped),
        integer_global_proofs=sum(r['proven_optimal'] for r in records),
        independent_plane_site_checks=len(planes),
        complete_MIP_prices=sum(r['independent'].get('complete',False) for r in planes),
        all_planes_independently_proved=all(r['proved_with_independent_MIP'] for r in planes),
        inconclusive=[{k:r[k] for k in ('case','kind','iteration','site')} for r in planes
            if not r['proved_with_independent_MIP']],
        source_scope='Full schedules and cuts checked using the same frozen runtime as timing; source/input/origin hashes verified.',
        limitation='Offline complete-kernel diagnostic; no root budget excluded speedup claim, no default adoption, no independent source generality.')
    write(target/'audited_rows.json',records);write(target/'summary.json',summary)
    print(json.dumps(summary,ensure_ascii=False))
    assert summary['all_planes_independently_proved'],'Retain inconclusive records; do not declare all plane proofs complete'


if __name__=='__main__':
    main()
