"""Seal this diagnostic's evidence without changing prior experiments."""
from pathlib import Path
import gzip
import hashlib
import json
import re
from experiment import OUT,ROOT,sha,write,now,verify

verify()
assert not (OUT/'running.lock').exists()
assert not (OUT/'final_manifest.json').exists()
report=ROOT/'docs/alns_single_batch_same_state_diagnostic_20261005.md'
checks={}
for phase,n in [('verification',16),('controls',4),('diagnostic',16)]:
    fresh=json.loads((OUT/(phase+'_fresh_audit.json')).read_text())
    checks[phase+'_fresh_audits']=len(fresh)==n and all(r['valid'] and r['equal'] for r in fresh)
    workers=json.loads((OUT/phase/'workers.json').read_text())
    checks[phase+'_workers_finished']=all(r['returncode']==0 for r in workers)
    for path in (OUT/phase).glob('[0-9]*.json'):
        raw=json.loads(path.read_text())
        checks[str(path.relative_to(OUT))+'_original_audit']=raw['audit']['valid'] and raw['audit']['periodic_replay']['valid']
        if phase in ('controls','diagnostic'):
            snapshot=OUT/phase/(path.name.split('_')[0]+'_shared_state.pkl.gz')
            checks[str(path.relative_to(OUT))+'_snapshot']=hashlib.sha256(gzip.decompress(snapshot.read_bytes())).hexdigest()==raw['branch']['state_sha256']
latest=json.loads((OUT/'latest_fresh_audit.json').read_text())
checks['latest36']=latest['passed'] and len(latest['checks'])==36
for rel,digest in json.loads((OUT/'latest_source_manifest.json').read_text()).items():
    checks['latest_source/'+rel]=sha(OUT/'latest_source'/rel)==digest
for rel,digest in json.loads((OUT/'chain_registration_hashes.json').read_text()).items():
    checks['chain_registration/'+rel]=sha(OUT/rel)==digest
owned=json.loads((OUT/'production_preservation.json').read_text())['owned_unchanged']
baseline=json.loads((OUT/'baseline_source_manifest.json').read_text())
for rel in owned:checks['owned/'+rel]=sha(ROOT/rel)==baseline[rel]
obs=json.loads((OUT/'acceptance_observations.json').read_text())['counts']
checks['observation_partition']=obs['proposals']==obs['proposed_feasible']+obs['proposed_infeasible']==obs['current_feasible']
checks['infeasible_probability']=obs['proposed_infeasible']==obs['infeasible_from_feasible']==obs['zero_sa_probability']==67052
chain=json.loads((OUT/'chain_decision.json').read_text())
checks['chain_rejected']=not chain['eligible_for_online_trial'] and not chain['adopted'] and chain['improved_states']==0 and chain['total_states']==8
checks['tests201']='201 passed' in (OUT/'targeted_tests.log').read_text()
checks['tests4']='4 passed' in (OUT/'chain_tests.log').read_text()
assert all(checks.values()),[k for k,v in checks.items() if not v]
write(OUT/'decision.json',dict(time=now(),adopted=False,diversity='REMAINS_REJECTED',
    chain_repair='FAILED_MECHANISM_ADMISSION',severity='ADDITIVE_ONLY_DEPRIORITIZED; penalty replacement untested',
    original_wall_clock_rejection_unchanged=True,production_changed=False))
write(OUT/'checkpoint.json',dict(status='completed_diagnostics_no_candidate_adopted',completed=now(),
    active_process=None,pending_runs=[],same_state_pairs=8,same_state_outcomes=16,
    saved_solutions_fresh_audited=36,latest_shared_context_audited=36,chain_repairs=100,
    chain_feasible=0,production_changed=False,automation_created_or_restarted=False,
    report=str(report.relative_to(ROOT))))
(OUT/'progress.md').write_text('''# 本轮完成

- 同状态8对诊断完成，16解有效；多样化短档平均+0.5314%，长档−0.0014%，不采用。
- 201项已有定向检查、4项新修复诊断检查通过；8对关闭入口复现、2对同策略分支控制通过。
- 133203次去除重复前缀后的提案决策中，67052次不可行提案的退火概率均为0。追加非负罚分不改变这些拒绝；替换罚分未测试。
- 实际缓存中的13个拒绝提案生成100个关联车辆修复，0个完整可行，不进入在线试验。
- 36个保存解在实验冻结代码及最新公共代码快照中均无缓存重解码、完整审计有效，目标一致。
- 生产保护文件未改；旧试验、拒绝结论和他人修改保留。所有已知本窗口进程结束，无待运行作业，无新增自动化。
- 再继续时先读本轮报告并冻结届时公共代码；不要重跑已完成目录或把固定迭代结果当同墙钟收益。
''')
for target in re.findall(r'\]\(([^)]+)\)',report.read_text()):
    if '://' not in target:checks['report_link/'+target]=(report.parent/target).resolve().exists()
assert all(checks.values())
write(OUT/'final_verification.json',dict(time=now(),passed=True,checks=checks,
    note='Evidence/hash/consistency checks are not a count of software tests.'))
excluded={'baseline_source','instrumented_source','latest_source','__pycache__','.pytest_cache'}
files=[p for p in OUT.rglob('*') if p.is_file() and not any(x in excluded for x in p.relative_to(OUT).parts)]
hashes={str(p.relative_to(ROOT)):sha(p) for p in sorted(files)}
hashes[str(report.relative_to(ROOT))]=sha(report)
write(OUT/'final_manifest.json',hashes)
print('Closed:',len(checks),'evidence checks;',len(hashes),'artifact hashes;no pending processes or runs')
