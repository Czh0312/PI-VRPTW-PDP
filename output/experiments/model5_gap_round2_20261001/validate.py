"""Reaudit UBs, independently replay proof leaves, combine identical E2 bounds."""
from dataclasses import asdict
import hashlib
import json
import math
from pathlib import Path
import sys

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[2]
OLD=HERE.parent/'model5_gap_target_20261001'
sys.path.insert(0,str(HERE/'source_snapshot'))
from configuration.loader import load_routing_case
from models.routing.common.cycle import load_instance
from models.routing.solvers.exact.multibatch_route_pricing import route_context
import validation_certificate as check


def read(path):
    return json.loads(Path(path).read_text())


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


check.asdict=asdict
check.route_context=route_context
manifest=read(HERE/'manifest.json')
for name,digest in manifest['source_sha256'].items():
    assert sha(HERE/'source_snapshot'/name)==digest
for name,digest in manifest['input_sha256'].items():
    assert sha(name)==digest
swap_manifest=read(HERE/'exchanges_180s/manifest.json')
for name,digest in swap_manifest['source_sha256'].items():
    assert sha(HERE/'swap_source_snapshot'/name)==digest
focus_manifest=read(HERE/'focused_pricing_150s/manifest.json')
assert focus_manifest['source_sha256']==manifest['source_sha256']
baseline=read(OLD/'joint_pool_validation.json')
assert baseline['met_cases']==2
cases=[]
problems={}
proof_leaves=[]
for row in baseline['cases']:
    if row['customers']!=100:
        continue
    key='100_'+row['pool']
    initial=read(row['result_path'])
    first=read(HERE/(key+'.json'))
    last=read(HERE/'exchanges_180s'/(key+'.json'))
    p=load_instance(*load_routing_case(first['job']['case']))
    problems[key]=p
    before=check.full_audit(p,initial['result'])
    middle=check.full_audit(p,first['result'])
    after=check.full_audit(p,last['result'])
    assert after['objective']<=middle['objective']+1e-6<=before['objective']+2e-6
    assert last['result']['info']['best_bound'] is None and last['result']['info']['mip_gap'] is None
    check.verify_certificate(p,dict(first,bound=first['certificate']['bound']),HERE)
    # Replay all candidate routes from every primal pass, including unused
    # routes and unchanged passes. Original paths must always be retained.
    for directory in (HERE/key/'pool',HERE/'exchanges_180s'/key):
        for pass_dir in directory.glob('pass_*'):
            pool=read(pass_dir/'columns.json');prior=read(pass_dir/'initial_result.json')
            assert len(pool)==len(prior['e2_trips'])
            for trip,candidates in zip(prior['e2_trips'],pool):
                original=[(e['order_id'],e['kind']) for e in trip['events']]
                assert any([tuple(k) for k in c['tasks']]==original for c in candidates)
                for column in candidates:
                    check.replay_column(p,trip['satellite_id'],column);check.columns+=1
    cases.append(dict(id=key,pool=row['pool'],old_upper=row['upper'],old_lower=row['lower'],
        old_gap_percent=row['gap_percent'],upper=after['objective'],
        result_path=str(HERE/'exchanges_180s'/(key+'.json')),
        e1_leaf=str(HERE/(key+'.json')),e1_bound=first['certificate']['e1_bound'],
        e1_relaxation_optimal=first['certificate']['e1_certificate']['relaxation_optimal']))
    proof_leaves.append((str(HERE/(key+'.json')),first))
# Earlier, fully verified certificates are unchanged; strengthen each E2
# component independently across the identical full/tight relaxations.
for key in ('00_42_cg','01_42_cg'):
    path=OLD/'strict_pricing_100s'/(key+'.json')
    assert sha(path)==baseline['artifact_sha256'][str(path.relative_to(OLD))]
    proof_leaves.append((str(path),read(path)))
signatures={site:check.signature(problems['100_full'],site) for site in problems['100_full'].satellites}
assert signatures=={site:check.signature(problems['100_tight'],site) for site in problems['100_tight'].satellites}
assert signatures==focus_manifest['all_site_signatures']
shared={site:dict(bound=0.0,source=None,method=None) for site in signatures}
for path,payload in proof_leaves:
    owner=load_instance(*load_routing_case(payload['job']['case']))
    for component in payload['certificate']['components']:
        site=component['satellite']
        assert check.signature(owner,site)==signatures[site]
        if component['bound']>shared[site]['bound']:
            shared[site]=dict(bound=component['bound'],source=path,method='verified_CG_or_distance_component')
for job in focus_manifest['jobs']:
    path=HERE/'focused_pricing_150s'/(job['satellite']+'.json')
    saved=read(path)
    assert saved['job']==job and saved['job']['relaxation_signature']==signatures[job['satellite']]
    first=read(HERE/'exchanges_180s/100_full.json')
    check.verify_focused_site(problems['100_full'],saved['site'],first,path.parent/job['satellite'])
    if saved['site']['bound']>shared[job['satellite']]['bound']:
        shared[job['satellite']]=dict(bound=saved['site']['bound'],source=str(path),method='focused_CG')
for case in cases:
    raw=case['e1_bound']+math.fsum(c['bound'] for c in shared.values())
    margin=1e-7*max(1,raw)
    lower=max(case['old_lower'],raw-margin)
    assert lower<=case['upper']+1e-6
    case.update(raw_lower=raw,margin=margin,lower=lower,
        gap_percent=100*(case['upper']-lower)/abs(case['upper']),
        meets_five_percent=100*(case['upper']-lower)/abs(case['upper'])<=5,
        feasible_cost_improvement_percent=100*(case['old_upper']-case['upper'])/abs(case['old_upper']))
    print(case,flush=True)
pair_dir=HERE/'pair_exchange_30s'
if pair_dir.exists():
    pair_manifest=read(pair_dir/'manifest.json')
    for name,digest in pair_manifest['source_sha256'].items():
        assert sha(pair_dir/'source_snapshot'/name)==digest
    for case in cases:
        path=pair_dir/(case['id']+'.json')
        saved=read(path)
        audited=check.full_audit(problems[case['id']],saved['result'])
        assert saved['lower']==case['lower']
        assert audited['objective']<=case['upper']+1e-6
        for pass_dir in (pair_dir/case['id']).glob('pass_*'):
            pools=read(pass_dir/'columns.json');prior=read(pass_dir/'initial_result.json')
            assert len(pools)==len(prior['e2_trips'])
            for trip,candidates in zip(prior['e2_trips'],pools):
                assert any([tuple(k) for k in c['tasks']]==[(e['order_id'],e['kind']) for e in trip['events']] for c in candidates)
                for column in candidates:
                    check.replay_column(problems[case['id']],trip['satellite_id'],column);check.columns+=1
        if audited['objective']<case['upper']-1e-6:
            case['upper_before_pair_exchange']=case['upper']
            case['upper']=audited['objective'];case['result_path']=str(path)
        case.update(gap_percent=100*(case['upper']-case['lower'])/abs(case['upper']),
            meets_five_percent=100*(case['upper']-case['lower'])/abs(case['upper'])<=5,
            feasible_cost_improvement_percent=100*(case['old_upper']-case['upper'])/abs(case['old_upper']))
        print('FINAL',case,flush=True)
core=read(ROOT/'output/experiments/model5_alns_round6_20261001/current_m5_sha256.json')['sha256']
frozen_core_matches={name:manifest['source_sha256'][name]==digest for name,digest in core.items()}
assert all(frozen_core_matches.values())
workspace_core_matches={name:sha(ROOT/name)==manifest['source_sha256'][name] for name in core}
tests=(HERE/'pytest_all_final.log').read_text().strip().splitlines()[-1]
assert tests.startswith('100 passed'),tests
report=dict(target_gap_percent=5,cases=cases,shared_E2_components=shared,
    E2_relaxation_signatures=signatures,whole_cycle_audits=check.audit_count,
    independently_replayed_columns=check.columns,checked_distance_witnesses=check.witnesses,
    E1_mps_checks=check.certificates,tests=tests,default_alns_edited_by_this_round=False,
    frozen_core_matches_round6=frozen_core_matches,workspace_core_matches_frozen=workspace_core_matches,
    focused_pricing_no_gain=all(read(HERE/'focused_pricing_150s'/(s+'.json'))['site']['bound']==0 for s in ('S1','S4')),
    full_target_achieved=False,statistical_fallacy_coverage='11/11 checked; exploratory extra-budget development cases',
    artifact_sha256={str(f.relative_to(HERE)):sha(f) for f in HERE.rglob('*')
        if f.is_file() and '__pycache__' not in f.parts and f.name!='validation.json'
        and not (f.suffix=='.log' and f.stem.startswith('validation'))})
(HERE/'validation.json').write_text(json.dumps(report,ensure_ascii=False,indent=2,allow_nan=False)+'\n')
print(dict(audits=check.audit_count,columns=check.columns,witnesses=check.witnesses,tests=tests))
