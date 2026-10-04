from pathlib import Path
import hashlib
import json
import math
import shutil
import sys
import xml.etree.ElementTree as ET

ROOT=Path('/Users/chenzeheng/Desktop/2E-VRPTW-PDD')
sys.path.insert(0,str(ROOT))
from configuration.loader import load_routing_case
from models.routing.common.cycle import load_instance
from models.routing.common.cycle.audit import audit_solution
from models.cost.joint_batch import JointCostProfile

out=ROOT/'output/model6_comparison/20261005_uniform_v1_final'
plan=json.loads((out/'experiment.json').read_text())
rows=json.loads((out/'rows.json').read_text())
jobs={j['run']:j for j in plan['jobs']}
checks=[]
for row in rows:
    config,fleet=load_routing_case(jobs[row['run']]['case_path'],use_pool_config=False)
    problem=load_instance(config,fleet)
    result=json.loads((out/row['run']/'result.json').read_text())
    if not row['valid']:
        assert result['info']['objective'] is None
        checks.append(dict(run=row['run'],valid=False,status=row['status'],scope='No incumbent; not a physical feasibility proof'))
        continue
    audit=audit_solution(problem,result)
    assert audit['valid'],(row['run'],audit['errors'])
    assert audit['periodic_replay']['valid']
    economics=JointCostProfile(json.loads(Path(config['economic_config']).read_text()))
    pool=result['container_pool_size'] if problem.model_number==6 else problem.operating_policy.container_pool_size
    cost=audit['cost'] if problem.model_number==6 else economics.evaluate(audit,pool)
    assert math.isclose(row['cost'],cost['total'],abs_tol=1e-6,rel_tol=1e-9)
    assert row['pool']==pool and row['minimum_pool_for_schedule']==audit['minimum_pool_for_schedule']
    assert math.isclose(row['distance'],sum(audit['distances'].values()),abs_tol=1e-7)
    if problem.model_number==6:
        batches=result['batches']
        assert len(batches)==result['cycle_batch_count']
        assert len({b['end']-b['start'] for b in batches})==1
        assert len({b['cutoff']-b['start'] for b in batches})==1
        assert len({b['end']-b['cutoff'] for b in batches})==1
        assert batches[0]['start']==problem.operating_policy.cycle_start
        assert batches[-1]['end']==problem.operating_policy.cycle_end
        assert all(a['end']==b['start'] for a,b in zip(batches,batches[1:]))
        recovered=len({r['batch_id'] for r in result['recovery_rounds'] if r['quantity']>0})
        assert recovered==result['actual_recovery_round_count']<=result['cycle_batch_count']
        assert [r['batch_count'] for r in result['count_search']]==list(problem.batch_count_candidates)
        if row['proven_optimal']:
            assert all(r['proven_optimal'] or r['proven_infeasible'] for r in result['count_search'])
    checks.append(dict(run=row['run'],valid=True,cost=cost['total'],periodic_replay=True,
                       cycle_batch_count=result.get('cycle_batch_count'),pool=pool))
source_checks=0
for name,expected in plan['source_hashes'].items():
    assert hashlib.sha256((out/'source_snapshot'/name).read_bytes()).hexdigest()==expected
    assert hashlib.sha256((ROOT/name).read_bytes()).hexdigest()==expected,name
    source_checks+=1
input_checks=0
for name,item in plan['input_hashes'].items():
    assert hashlib.sha256((out/item['snapshot']).read_bytes()).hexdigest()==item['sha256']
    assert hashlib.sha256(Path(name).read_bytes()).hexdigest()==item['sha256'],name
    input_checks+=1
assert (out/'inputs/manifest.json').read_bytes()==(ROOT/'data/model6/uniform_v1_20261005/manifest.json').read_bytes()
xml=Path('/private/tmp/model6_uniform_full_tests_final.xml')
suite=ET.parse(xml).getroot().find('testsuite')
assert suite is not None and suite.get('failures')=='0' and suite.get('errors')=='0'
shutil.copy2(xml,out/'full_tests.xml')
shutil.copy2(Path(__file__),out/'validation_script.py')
payload=dict(status='ANALYZED',valid_runs=sum(c['valid'] for c in checks),attempted_runs=len(checks),
             cost_and_continuous_two_cycle_replay=checks,source_hashes_verified=source_checks,
             source_matches_current=True,input_hashes_verified=input_checks,manifest_matches_current=True,
             tests={k:suite.get(k) for k in ('tests','failures','errors','skipped','time')},
             timing_note='Final tests finished before this study; two solver workers share resources',
             trial_note='Earlier 20261005_uniform_v1 preserved with superseded notice; corrected recovery counts in this final rerun',
             limitations=['Configured K=1,2,3 and integer seconds', 'Costs are synthetic units',
                          'Short search budgets do not prove convergence or globally minimum box counts',
                          'Independent replay and hash checks do not reproduce stochastic solver runs'])
(out/'independent_validation.json').write_text(json.dumps(payload,ensure_ascii=False,indent=2)+'\n')
with (out/'uniform_report.md').open('a') as report:
    report.write(f"\n独立验证：{payload['valid_runs']}/{len(checks)}有效运行通过成本与连续两周期重放；{source_checks}份源码和{input_checks}份输入哈希匹配当前文件。全项目{suite.get('tests')}项测试通过，无失败。最终测试在本轮优化之前完成。\n")
print(json.dumps({k:v for k,v in payload.items() if k!='cost_and_continuous_two_cycle_replay'},ensure_ascii=False))
