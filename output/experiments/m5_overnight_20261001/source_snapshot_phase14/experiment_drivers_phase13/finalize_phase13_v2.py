from pathlib import Path
import json,hashlib,sys,shutil,re
from datetime import datetime,timezone
HERE=Path(__file__).resolve().parent;ROOT=HERE.parents[2]
sys.path.insert(0,str(HERE/'source_snapshot_phase13'))
from configuration.loader import load_routing_case
from models.routing.common.cycle import load_instance
from models.routing.common.cycle.audit import audit_solution

def read(p):return json.loads(p.read_text())
def write(p,r):p.write_text(json.dumps(r,indent=2,ensure_ascii=False)+'\n')
def digest(p):return hashlib.sha256(p.read_bytes()).hexdigest()
# All workers have exited before this script starts. No solving here.
protected=[]
for count,case,result,combined in [(50,HERE/'inputs/case.json',HERE/'best_full_result.json',HERE/'combined_certificate.json'),(30,HERE/'medium25/1/inputs/case_relocated.json',HERE/'medium25/1/best_full_result.json',HERE/'medium25/1/combined_certificate.json'),(20,HERE/'medium25/0/inputs/case_relocated.json',HERE/'medium25/0/inputs/initial_result.json',HERE/'site30/combined_certificate.json')]:
 p=load_instance(*load_routing_case(str(case)));a=audit_solution(p,read(result));c=read(combined);assert a['valid'] and abs(a['objective']-c['upper_bound'])<1e-6
 if count==50:assert digest(result)=='7ffb79e95bcec3953544f9c0cc8298dc72bb429168c15d3659f731b63ed97ea1' and c['lower_bound']==2235.8130902493017
 protected.append(dict(customers=count,full_upper=a['objective'],lower=c['lower_bound'],upper_sha256=digest(result),full_audit_valid=True,complete_original_business=True,full_proven_optimal=False))
write(HERE/'phase13_protected_reaudit.json',dict(results=protected,no_unverified_certificate_replaced=True))
old=read(HERE/'site30/combined_certificate.json');rows=[]
for folder,oracle in [('master33','core32'),('master36','core35'),('master39','core38'),('master42','site40'),('master44','group43')]:
 c=read(HERE/folder/'certificate_pending_validation.json');v=read(HERE/folder/'independent_validation.json');assert v['promote_supported']
 r=old.copy();r.update(lower_bound=c['candidate_global_lower'],gap=(old['upper_bound']-c['candidate_global_lower'])/old['upper_bound'],previous_lower=c['old_global_lower'],lower_improvement=c['candidate_global_lower']-old['old_lower'],lower_certificate=c,lower_independent_validation=v,master_sha256=c['master_sha256'],global_master_initial_projection=str(HERE/folder/'independent_validation.json'),stronger_local_oracle_validation=str(HERE/oracle/'independent_validation.json'),scope='original_20_customer_M5_verified_conditional_global_lower_and_audited_full_upper')
 if folder!='master33':write(HERE/folder/'combined_certificate.json',r)
 rows.append(dict(master=folder,conditional_proof=oracle,verified_global_lower=r['lower_bound'],full_upper=r['upper_bound'],gap_percent=100*r['gap'],variables=c['variables'],rows=c['rows'],indicators=c['indicators']))
write(HERE/'phase13_progress.json',rows)
account=[]
for folder,reducer,oracle in [('master33','reduce32','core32'),('master36','reduce35','core35'),('master39','reduce38','core38')]:
 c=read(HERE/folder/'certificate_pending_validation.json');q=read(HERE/oracle/'certificate_pending_validation.json');red=read(HERE/reducer/'summary.json')
 wall=c['runtime']-q['runtime'];account.append(dict(master=folder,reducer_wall_seconds=red['runtime'],master_driver_wall_seconds=wall,end_to_end_reducer_plus_master=red['runtime']+wall,legacy_certificate_runtime_excludes_prior_deletion_trials=True,last_oracle_not_double_counted=True))
write(HERE/'phase13_runtime_accounting.json',dict(rounds=account,nominal_reducer_search_budget=120,per_deletion_solver_limit=15,master_and_previous_reduction_charged_budget=90,independent_validation_outside_algorithm_budget=True,matched_timing_claim=False,seed=42,replay_seed=43,threads=1))
last=rows[-1]
notes=f'''# 第十三段：缩小条件冲突与全路径联合验证

20 顾客原完整上界保持 **{last['full_upper']:.12f}**，已复核下界从第十二段的 **1429.7748906420645** 提高到 **{last['verified_global_lower']:.13f}**，gap 从 **0.535584388%** 降至 **{last['gap_percent']:.9f}%**。与今晚最初下界1426.648954231285相比，累计增加{last['verified_global_lower']-1426.648954231285:.12f}。30/50 顾客证书和完整方案保护复核通过；未宣称完整 M5 最优。

沿用已冻结并通过小例、投影、反例测试的物理站点与全路径分组接口，不改变仓数、容量、时间窗、使用/周转时长、回收截止、清洗库存或真实物理车辆规则。本段没有修改生产模块，无新增测试计数，累计404项相关测试保持；此前125项定向回归通过。三个新增主模型都保留原7268个完整路线选择域。

对三次新的整数完整任务划分，先在原 S1 全任务物理松弛中证明六组同时成立时不可行，再逐一解除分组条件。只有新的松弛返回INFEASIBLE才删除该条件；OPT可行和TIME_LIMIT均保留。三个最终冲突均只需两组，而其他 S1 任务保持自由，可遍历所有原路径、车辆和位置。三个缩减过程分别耗时23.73407975、25.707373625、18.962842666秒；每次总搜索上限120秒，每个删除候选15秒，独立复算使用种子43、单线程。存在超时删除尝试，所以**不声称最小冲突**。全部尝试、参数、证书、MPS和日志保留在reduce32/35/38。

core32/35/38独立复核均从冻结的无分组S1物理模型出发：每个新增44个分组变量、464条条件行；分别113、105、113条跨组弧关闭（以实际验证JSON为准），原组内弧、未分组任务之间的路径、原任务和真实车辆上限均保留。再用另一种子重现条件不可行。每条对应主模型切割仅增加3变量、6行，其精确任务集合标志从原完整覆盖行独立解码，AND和no-good逐项核对；原矩阵、目标、变量属性和120个指示约束不变。master33/36/39下界依次为1431.0483032339753、1431.9121228115432、1431.9451941261734。

随后site40的新S1六组已物理可行，条件成本保守下界283.12083496793576（原始283.1208632800221）。该物理模型矩阵与独立成本复算通过；master42在实际成本epigraph中加入条件成本行，但**没有进一步提高全局下界**。不反复求解这一已最优松弛。

将site40的全部13组接回一级供货、所有仓的真实车辆、回收和原清洗库存后，group43条件子问题不可行。独立前向时钟和Floyd上下界检查 **935个排列、35条不同可行路径**，保留所有成本支配路径；26个真实位置共368选择。完整独立矩阵核查保留原一级424变量、732行、120指示，新增池1245变量、1053行、1132指示（总1252指示）；种子43再次证明条件不可行。该证据不能单独归因于一级耦合，也可能来自其他仓物理排程，需要后续分辨。

master44新增全13组AND no-good，增加14变量28行。最终7746变量1134线性行120指示。独立核对全部新行、全部旧矩阵，并把原完整上界的时刻和实际E2成本投影进去、固定7738个已定义变量；剩余辅助变量可行、目标仍1437.473775767574。独立重算原始下界1432.1610015656324；正式界扣除0.0001432161001565632余量。受限子问题或松弛OPT均不作为完整M5最优证明。

本段是证书增强实验，复用已有最优数学控制证书，没有新的同总预算速度对照或加速声明。三次master的旧runtime字段只含主程序及最后一次oracle；准确删除搜索+主模型墙钟汇总在phase13_runtime_accounting.json，避免漏算和双算。中间next34工具第一次把列表任务读成字典而报错，修正版成功；site40可行后误调用不存在的reduce41脚本，仅产生缺文件日志，未执行模型或更新界。失败工具和日志原样保留。第一次收尾脚本在拼接分组验证文件路径时漏了目录，已完成保护审计后退出；v2只修复该路径，保存原脚本和失败日志，没有重复优化。

最终20顾客证书：master44/combined_certificate.json；所有新证据和源码由manifest_phase13_final.json冻结。checkpoint记录下一步：检查master44新整数任务覆盖后，优先单独检查S3分组物理排程，区分站点冲突与一级/清洗耦合；不要重跑已最优旧模型。07:00起仅收尾复核并写docs/m5_overnight_20261002.md，08:00前结束今晚自动续跑。
'''
# Populate cross-arc counts from evidence, avoiding editorial count drift.
counts=[read(HERE/f/'independent_validation.json')['cross_group_arc_bounds_checked'] for f in ['core32','core35','core38']]
notes=notes.replace('113、105、113条', '、'.join(map(str,counts))+'条')
(HERE/'phase13_notes.md').write_text(notes)
# Freeze source and experiment utility scripts, including failed attempts.
D=HERE/'source_snapshot_phase13/experiment_drivers_phase13';D.mkdir(exist_ok=True)
new_names=['site31.py','reduce32.py','master33.py','site34.py','reduce35.py','master36.py','site37.py','reduce38.py','master39.py','site40.py','master42.py','group43.py','master44.py','next34_attempt1.py','finalize_phase13.py','finalize_phase13_v2.py']
for name in new_names:shutil.copy2(HERE/name,D/name)
shutil.copytree(HERE/'validators_phase13',D/'validators_phase13',ignore=shutil.ignore_patterns('__pycache__'),dirs_exist_ok=True)
for p in Path('/private/tmp').glob('m5_*'):
 if p.is_file() and p.suffix=='.py':shutil.copy2(p,D/p.name)
folders=['source_snapshot_phase13','site31','reduce32','core32','master33','site34','reduce35','core35','master36','site37','reduce38','core38','master39','site40','master42','group43','master44','validators_phase13']
files=[]
for folder in folders:files.extend(p for p in (HERE/folder).rglob('*') if p.is_file() and '__pycache__' not in p.parts and p.suffix!='.pyc')
files.extend(HERE/n for n in new_names)
files.extend(p for p in HERE.glob('*.log') if p.name.startswith(('site31','reduce32','master33','site34','reduce35','master36','site37','reduce38','master39','site40','reduce41','master42','group43','master44','core32','core35','core38','finalize_phase13')))
files.extend(HERE/n for n in ['phase13_progress.json','phase13_notes.md','phase13_runtime_accounting.json','phase13_protected_reaudit.json'])
write(HERE/'manifest_phase13_final.json',dict(sha256={str(p):digest(p) for p in sorted(set(files))}))
cache={};checks=0;failures=[];aliases=[];manifests=[]
for manifest in sorted(HERE.glob('manifest*.json')):
 r=read(manifest);entries=r.get('sha256',r.get('source_and_input_sha256',r));count=0
 for path,sha in entries.items():
  if not isinstance(sha,str) or not re.fullmatch('[a-f0-9]{64}',sha):continue
  p=Path(path)
  if not p.is_absolute():p=(ROOT if path.startswith(('output/','models/','tests/','configuration/','config/','docs/')) else HERE)/p
  if manifest.name=='manifest_s1full16_input.json' and p==HERE/'best_full_result.json' and sha.startswith('a6d578'):
   aliases.append(dict(manifest=str(manifest),original_path=str(p),historical_input=str(HERE/'best_full_result_pre_pool22.json'),expected_sha256=sha));p=HERE/'best_full_result_pre_pool22.json'
  if str(p) not in cache:cache[str(p)]=digest(p) if p.exists() else None
  if cache[str(p)]!=sha:failures.append(dict(manifest=str(manifest),path=str(p),expected=sha,actual=cache[str(p)]))
  checks+=1;count+=1
 manifests.append(dict(path=str(manifest),entries=count))
write(HERE/'phase13_all_manifests_rechecked.json',dict(checked_entries=checks,unique_files=len(cache),manifests=manifests,historical_input_aliases=aliases,failures=failures,all_passed=not failures));assert not failures,failures[:2]
checkpoint=read(HERE/'checkpoint.json');checkpoint.update(status='phase13_saved_verified_partial_conflicts_and_full_group_cut',active_process=None,source_snapshot=str(HERE/'source_snapshot_phase13'),phase13_completed_at_utc=datetime.now(timezone.utc).isoformat(),phase13_notes=str(HERE/'phase13_notes.md'),phase13_manifest_recheck=str(HERE/'phase13_all_manifests_rechecked.json'),medium20_combined_certificate=str(HERE/'master44/combined_certificate.json'),medium20_known_lower=last['verified_global_lower'],medium20_known_upper=last['full_upper'],medium20_gap=last['gap_percent']/100,latest_note=f"20customer verified lower{last['verified_global_lower']},fullupper{last['full_upper']},gap{last['gap_percent']}%;three2group partial conflicts and new all-path13group oracle validated;30/50 protected.",next_action='No active worker. Readphase13 notes+checkpoint. Validate master44 integer taskcoverage before neworacle. PrioritizeS3 physicalgroup oracle to distinguishlocalwarehouse conflictfrom E1/clean coupling; fullS1site40 alreadyphysicalfeasible. Do not rerun optimalold relaxations or remove safety margins. 07:00Shanghai(23:00UTC) stoplargealgorithms, finalaudits anddocs/m5_overnight_20261002.md;08:00stop/pause automationm5. No newagents/commits/push/messages.')
write(HERE/'checkpoint.json',checkpoint)
print(json.dumps(dict(best20=last,protected=protected,manifest_entries=checks,unique_files=len(cache),all_passed=True),indent=2))
