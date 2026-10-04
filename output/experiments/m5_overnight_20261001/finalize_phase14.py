from pathlib import Path
from datetime import datetime,timezone
import sys,json,math,hashlib,shutil
import gurobipy as gp
H=Path(__file__).resolve().parent;ROOT=H.parents[2];sys.path.insert(0,str(H/'source_snapshot_phase14'))
from configuration.loader import load_routing_case
from models.routing.common.cycle import load_instance
from models.routing.common.cycle.audit import audit_solution

def read(p):return json.loads(p.read_text())
def write(p,r):p.write_text(json.dumps(r,indent=2,ensure_ascii=False)+'\n')
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def expr(e):return {e.getVar(i).VarName:e.getCoeff(i) for i in range(e.size())}
def rows(m):return {r.ConstrName:(r.Sense,r.RHS,expr(m.getRow(r))) for r in m.getConstrs()}
def indicators(m):
 d={}
 for r in m.getGenConstrs():
  v,value,e,sense,rhs=m.getGenConstrIndicator(r);d[r.GenConstrName]=(v.VarName,value,sense,rhs,expr(e))
 return d
# Compare the two finished integer experiments directly. No solving.
a=gp.read(str(H/'master49/master.mps.gz'));b=gp.read(str(H/'control50/master.mps.gz'))
try:
 for attr in ['VarName','LB','UB','VType','Obj']:assert a.getAttr(attr,a.getVars())==b.getAttr(attr,b.getVars())
 r=rows(a);assert r.pop('core47_cut_no_good')==('<',0,{'core47_cut_group_0':1});assert r==rows(b) and indicators(a)==indicators(b) and a.ObjCon==b.ObjCon and a.ModelSense==b.ModelSense
 paired=dict(all_variable_attributes_bounds_types_objective_identical=True,all_other_linear_and_indicator_rows_identical=True,only_deleted_row='core47_cut_no_good',same_input_initial_seed_threads_and_90_second_master_budget=True,no_timing_speed_claim=True,control_raw_bound=read(H/'control50/certificate_pending_validation.json')['raw_bound'],treatment_raw_bound=read(H/'master49/certificate_pending_validation.json')['raw_bound'])
 paired['marginal_raw_bound_gain']=paired['treatment_raw_bound']-paired['control_raw_bound']
finally:a.dispose();b.dispose()
write(H/'phase14_paired_cut_validation.json',paired)
# The inherited control50 candidate field carries a previously certified global
# bound from another model. It is not the isolated control result.
write(H/'control50/isolated_control_clarification.json',dict(raw_bound=paired['control_raw_bound'],isolated_conservative_control_bound=paired['control_raw_bound']-read(H/'control50/certificate_pending_validation.json')['margin'],inherited_candidate_field_uses_previous_external_global_certificate=True,only_raw_model_bound_used_for_paired_comparison=True,prior_physical_oracles_not_charged_to_either_integer_master_experiment=True,configured_master_budget_per_method=90))
old=read(H/'master44/combined_certificate.json')
for folder in ['master48','master49']:
 c=read(H/folder/'certificate_pending_validation.json');v=read(H/folder/'independent_validation.json');assert v['promote_supported'];r=old.copy();r.update(lower_bound=c['candidate_global_lower'],gap=(old['upper_bound']-c['candidate_global_lower'])/old['upper_bound'],previous_lower=c['old_global_lower'],lower_improvement=c['candidate_global_lower']-old['old_lower'],lower_certificate=c,lower_independent_validation=v,master_sha256=c['master_sha256'],global_master_initial_projection=str(H/folder/'independent_validation.json'),stronger_local_oracle_validation=str(H/'core47/independent_validation.json'),original_local_projection_validation=str(H/'site45/original_projection_validation.json'),scope='original_20_customer_M5_verified_complete_joint_cut_integrality_lower_and_audited_full_upper',small_integer_projection_validation=str(H/'small_integrality52/validation.json'),conservative_margin_preserved=True,full_proven_optimal=False,full_proven_infeasible=False);write(H/folder/'combined_certificate.json',r)
assert read(H/'small_integrality52/validation.json')['all_passed'] and read(H/'control51/independent_validation.json')['promote_supported']
protected=[]
for n,case,result,combined in [(50,H/'inputs/case.json',H/'best_full_result.json',H/'combined_certificate.json'),(30,H/'medium25/1/inputs/case_relocated.json',H/'medium25/1/best_full_result.json',H/'medium25/1/combined_certificate.json'),(20,H/'medium25/0/inputs/case_relocated.json',H/'medium25/0/inputs/initial_result.json',H/'master49/combined_certificate.json')]:
 p=load_instance(*load_routing_case(str(case)));audit=audit_solution(p,read(result));c=read(combined);assert audit['valid'] and abs(audit['objective']-c['upper_bound'])<1e-6
 if n==50:assert sha(result)=='7ffb79e95bcec3953544f9c0cc8298dc72bb429168c15d3659f731b63ed97ea1' and c['lower_bound']==2235.8130902493017
 protected.append(dict(customers=n,lower=c['lower_bound'],upper=c['upper_bound'],gap=c['gap'],gap_percent=100*c['gap'],full_upper_sha256=sha(result),full_audit_valid=True,full_proven_optimal=False))
write(H/'phase14_final_bounds.json',dict(results=protected,all_full_schedules_reaudited=True,all_best_certificates_preserved=True))
notes='''# 第十四段与今晚收尾

20顾客最新主问题的S1列是分数值，第一版prepare_phase14拒绝将其当完整整数分组。保留失败源码；v2只从仍为整数且任务精确覆盖的S3/S4提取分组，S1分数列明确不构造条件。继承此前已验证私有模块和404项相关不同回归；本段未修改生产模块。另补验7份冻结已知完整最优小例：全部选择整数化后，界安全、所有完整最优方案以0/1选择投影成功，记录small_integrality52/validation.json。这是额外投影复核，不虚增旧pytest数量。

S3原5组全物理子问题不可行，逐一放松分组后得到仅一组(C29P,C88D,C88P)的条件冲突，其余S3任务仍自由。reduce46删除搜索21.232487416秒，单次15秒、总120秒；一次删除超时未接受，不声称最小冲突。独立检查原无分组S3模型1499变量：core47只加20个条件分配变量、341行、关闭37条跨组弧。独立种子43再现不可行。另从原完整局部模型1823变量直接核查仅删除E1变量和触及它们的行，原网络、车队、订单、目标与剩余系数不变；局部箱池33大于局部需求11，没有人为缩小池。证明site45/original_projection_validation.json。

master48仅增加一个精确任务组标志和两行，保留原完整7268选择域、其他变量矩阵和原120指示；全局保守界1434.354707721595。原完整方案固定7739个已定义变量的投影仍可行，目标1437.473775767574。S3列仍有分数，所以master49把全部7268个完整路线选择的类型从C改为B，原上下界、目标、所有行和指示不变。任何完整M5方案在完整列域中每趟选择一个代表列，其0/1投影仍保留；列成本可以低于真实路径，但实际成本epigraph保持。独立从40条原任务覆盖行解码全部7268变量，检查唯一类型变化和完整初解投影，另用种子43复算。原始界1437.473775767574，扣0.00014374737757675738余量后的全局界1437.4736320201964，完整上界1437.473775767574，gap约0.000010%。完整最优性仍不声明。

收益归因的两份新控制均同原输入、同已审计初解、90秒主模型预算、种子42、单线程顺序执行；验证在所有计时后。control50与master49完全相同的整数化、所有触发器和旧切割，只删除core47_cut_no_good这一行；独立直接矩阵比较通过。原始控制界1432.3072816144345，对应处理1437.473775767574，单行在整数化模型中的差额5.1664941531395。control50候选字段继承较强外部旧证书，不能把该字段当隔离控制结果；isolated_control_clarification.json说明，只用原始本模型界归因。control51仅将原始联合模型全部列整数化、没有任何今晚的新条件切割，原始界1426.8744831916165，表明整数化本身不能消除剩余物理gap。三次主模型实测约0.54/0.56/0.56秒，不作加速或从零全过程用时声明；历史物理子问题和独立复核分别记账。

50及30顾客最好证书、完整日程重新审计通过，不重跑其已最优松弛。北京时间07:00停止新大规模算法，之后仅做既有证据复核、哈希冻结和docs/m5_overnight_20261002.md。所有试验参数、失败准备器、输入、源码、日志、MPS、结果和独立复核保留。随后暂停m5今晚自动续跑，不创建跨日任务。
'''
(H/'phase14_notes.md').write_text(notes)
# Build the requested report after the 07:00 cutoff, preserving any existing file.
doc=ROOT/'docs/m5_overnight_20261002.md';assert not doc.exists()
by={r['customers']:r for r in protected}
report=f'''# 多批次 M5 精确求解：2026-10-01 晚间续跑汇总

今晚把50顾客的完整可行上界从2247.646367634466降低到 **2235.813359002067**，独立复核下界从2235.4231373718553提高到 **2235.8130902493017**；gap从0.54382355%降至 **0.00001202036%**。30顾客与20顾客也均达到所保留的数值余量附近。没有删除保守余量，**尚未宣称完整M5严格最优**。

## 最好证书

| 原冻结实例 | 已复核全局下界 | 已审计完整上界 | gap（百分比） | 证书 |
|---|---:|---:|---:|---|
|20顾客|{by[20]['lower']:.13f}|{by[20]['upper']:.12f}|{by[20]['gap_percent']:.11f}%|[合并证书](../output/experiments/m5_overnight_20261001/master49/combined_certificate.json)|
|30顾客|{by[30]['lower']:.13f}|{by[30]['upper']:.13f}|{by[30]['gap_percent']:.11f}%|[合并证书](../output/experiments/m5_overnight_20261001/medium25/1/combined_certificate.json)|
|50顾客|{by[50]['lower']:.13f}|{by[50]['upper']:.12f}|{by[50]['gap_percent']:.11f}%|[合并证书](../output/experiments/m5_overnight_20261001/combined_certificate.json)|

20顾客今晚初始下界1426.648954231285、上界1437.473775767574；30顾客初始上界1654.8220896736802，今晚下降30.84358990855253。三例的最新完整方案均在最后阶段重新周期审计通过，见[最终界和方案哈希](../output/experiments/m5_overnight_20261001/phase14_final_bounds.json)。50最好方案SHA为`7ffb79e95bcec3953544f9c0cc8298dc72bb429168c15d3659f731b63ed97ea1`，与认证文件一致；旧方案和证书另存，未覆盖原实验。

50剩余绝对差0.00026875276535065495，等于全局保守余量0.00022358133128363988与S1覆盖保守余量0.00004517143401017165之和（余数约5.68e-14）。20差额0.0001437473776、30差额0.0001623978501也处于保守余量尺度。这是差额核算，不能作为删除余量或将浮点求解器状态升级为严格数学证明的依据。

## 生效的算法联系

采用可核查的条件物理子问题和逻辑Benders式切割：任务分组条件保留每组全部原允许访问顺序、原整数时刻、真实车辆分配和串行；只放松一级路径及局部箱池。完整日程必须能投影到这个子问题。供货和截止前回仓数量条件采用双向整数分类，精确任务集合匹配采用二元标志和AND。只有证明对应放松子问题不可行，才禁用该条件；限时未知或某个受限路径失败均不生成no-good。

条件成本下界约束各仓实际成本变量theta，theta同时支配压缩覆盖成本。成本支配删除后的便宜代表列可能不能物理执行，不能把真实日程下界直接写到压缩路线成本上。全路径分组模型还连接完整一级供货、回收截止、清洗库存和车辆跨空槽准备/周转，并以因果秩保护零时长边界。

50顾客先由真实车辆重排和回收时刻调整减少一级访问，再通过路线池保留更多低成本覆盖路径改善二层成本；上界经历2238.1568127099035、2235.9725985686455、2235.813359002067。S3任务分组配合第二截止前最多零箱条件及其部分组推广，将全局下界提高到2235.8130902493017。完整供货/库存与物理车辆日程均保留。新完整上界的四仓原模型固定投影、原一级真实截止数量/清洗投影、认证下界主模型投影全部通过。

20顾客先用全访问顺序条件排程代替固定顺序失败推断，再把冲突缩小到两个S1组；最后找到S3单组(C29P,C88D,C88P)冲突，其余任务自由。这条单组切割只有一个新增标志和两行。全部7268个原完整路线选择整数化后，原始松弛值达到完整上界；独立从原40条覆盖行核对全部类型变化、完整方案0/1投影及另一种子界复算。它仍是包含有效必要条件的联合模型，不是把物理受限模型最优状态当成完整M5证明。

## 收益归因与预算

耗时对照顺序运行，同输入、同初解、相同阶段总预算、单线程，不以独立复核的并发负载作性能比较。新数学证书复用旧已最优完整域，未重复求解不变松弛来制造收益。

|20顾客控制/处理|原始本模型下界|对照解释|
|---|---:|---|
|原始联合模型仅全部列整数化|1426.8744831916165|没有今晚的新条件切割，整数化本身不足|
|保留旧切割、全部整数化，只删最后S3单组no-good|1432.3072816144345|其余变量属性、矩阵、目标、全部指示完全相同|
|保留S3单组no-good并全部整数化|1437.473775767574|单行在该整数模型中的增益5.1664941531395|

后两组同一90秒主模型预算、种子42，已有物理oracle成本不计入两边此次主模型阶段；实测均约0.54–0.56秒，不声称加速或从零总耗时。控制文件继承的外部最好界不用于隔离控制归因，仅用其本模型原始界。[直接配对矩阵核查](../output/experiments/m5_overnight_20261001/phase14_paired_cut_validation.json)与[控制字段说明](../output/experiments/m5_overnight_20261001/control50/isolated_control_clarification.json)保留。

50覆盖保留策略用2142列达到与2658列池相同完整上界，少19.41%列，但实测7.35秒比6.92秒更慢，没有加速结论。30顾客同90秒、种子42对照：普通32池1456列、完整上界1636.042837733466；覆盖保留1542列、完整上界1623.9784997651277。20同组对照均无上界改善且保留策略更慢。不能宣传此策略普遍提升性能。

## 未带来收益或不能使用的尝试

- 无完整任务分组的S1/S3原物理弧子问题界太弱，240/180秒未得到新证据；保留弱界或限时未知，不生成切割。
- 扩大候选、增加可选趟位、重复固定路径时刻优化，多次只保持原上界；没有增加真实车辆或业务趟数上限。
- 相同完整候选的重复提案没有增量收益。50最终池提升来自保留原候选，不能归因于额外八条已存在的物理提案。
- 固定有序路径不可行不代表无序任务集合的全部访问顺序不可行；未把此类失败直接接到压缩主问题。
- 全路径服务包络原对照两种子仅小幅改善、第三种子限时退步，仍默认关闭。三任务子集行、S1供货阈值和部分成本地板尝试未进一步提高最好界。
- 20某轮S1条件成本切割没有增益；分组缩减有删除候选超时，不声称最小冲突。发现分数列时拒绝当整数分组。

失败的参数调用、目录/字段读取、验证器假设及收尾日志哈希问题均保存原源码和失败日志，再以新版本修复；没有隐去失败或改写旧冻结实验。逐段细节见[第一至十四段记录目录](../output/experiments/m5_overnight_20261001/)。

## 复核和源码

相关不同回归累计404项；最后新增的全路径/条件切割定向组合125项通过。末段另对7份已冻结独立完整最优小例核查完整列整数化的安全下界和0/1完整方案投影，见[小例验证](../output/experiments/m5_overnight_20261001/small_integrality52/validation.json)。这些是补充数学投影复核，不冒充重新运行整个共享路由测试集。

大例改进均经过矩阵逐项核查、独立界复算和完整日程审计；完整上界另外检查有向成本、配送/回收精确覆盖、跨趟使用时长、真实车辆准备/周转占用，以及原物理仓/一级模型固定投影。S3单组子问题还从原1823变量局部完整模型核对1499变量放松投影；新增分配、全部组内弧和未分组任务路径均保留。[原局部矩阵复核](../output/experiments/m5_overnight_20261001/site45/original_projection_validation.json)、[单组证明](../output/experiments/m5_overnight_20261001/core47/independent_validation.json)、[最终20主模型验证](../output/experiments/m5_overnight_20261001/master49/independent_validation.json)可追溯。

新增私有模块位于models/routing/solvers/exact：multibatch_physical_site、multibatch_physical_cuts、multibatch_vehicle_retime、multibatch_task_pairs、multibatch_physical_ceiling、multibatch_subset_rows、multibatch_cover_pool、multibatch_cover_schedule、multibatch_group_schedule、multibatch_group_cuts。当前公开默认入口未启用这些实验性切割；最终源码和驱动保存在source_snapshot_phase14。全部输入/参数/日志/结果/MPS及哈希留在本聊天实验目录，检查点是[checkpoint.json](../output/experiments/m5_overnight_20261001/checkpoint.json)。

## 停止和遗留工作

北京时间07:00起只收尾复核，最迟08:00前暂停m5今晚续跑，无活动求解进程，不创建跨日安排。没有提交、推送、发布、外部消息或子代理；共享目录其他修改保留。自动安排的最终状态及全部完成文件哈希会存入检查点和最终停止记录。

后续如由用户明确恢复：从已有证书、完整方案和冻结快照继续，避免重跑相同最优松弛。优先把经审计的分组oracle、冲突缩减和整数主问题组合成可恢复的受预算控制流程，并扩展不同输入/种子的同预算收益证据；公开集成前保留所有业务规则和完整投影检查。严格最优证明需要更强的可核查数值/数学证书，不能通过取消保守余量获得。
'''
doc.write_text(report)
# Freeze only finished logs: the current finalizer has no redirected log.
D=H/'source_snapshot_phase14/experiment_drivers_phase14';D.mkdir()
for name in ['prepare_phase14.py','prepare_phase14_v2.py','site45.py','reduce46.py','master48.py','master49.py','control50.py','control51.py','finalize_phase14.py']:shutil.copy2(H/name,D/name)
shutil.copytree(H/'validators_phase14',D/'validators_phase14',ignore=shutil.ignore_patterns('__pycache__'),dirs_exist_ok=True)
for name in ['m5_prepare48.py','m5_prepare49.py','m5_control50.py']:shutil.copy2(Path('/private/tmp')/name,D/name)
files=[doc,H/'phase14_notes.md',H/'phase14_final_bounds.json',H/'phase14_paired_cut_validation.json']
for folder in ['source_snapshot_phase14','site45','reduce46','core47','master48','master49','control50','control51','small_integrality52','validators_phase14']:
 files.extend(f for f in (H/folder).rglob('*') if f.is_file() and '__pycache__' not in f.parts and f.suffix!='.pyc')
files.extend(H/n for n in ['prepare_phase14.py','prepare_phase14_v2.py','site45.py','reduce46.py','master48.py','master49.py','control50.py','control51.py','finalize_phase14.py'])
files.extend(f for f in H.glob('*.log') if f.name.startswith(('site45','reduce46','core47','master48','master49','control50','control51','small_integrality52')))
write(H/'manifest_phase14_final.json',dict(sha256={str(f):sha(f) for f in sorted(set(files))},all_workers_exited_before_freeze=True))
c=read(H/'checkpoint.json');c.update(status='overnight_final_report_saved_pending_hash_and_automation_pause',active_process=None,phase14_completed_at_utc=datetime.now(timezone.utc).isoformat(),source_snapshot=str(H/'source_snapshot_phase14'),phase14_notes=str(H/'phase14_notes.md'),final_report=str(doc),medium20_combined_certificate=str(H/'master49/combined_certificate.json'),medium20_known_lower=by[20]['lower'],medium20_known_upper=by[20]['upper'],medium20_gap=by[20]['gap'],latest_note='All20/30/50certified gaps at retained numerical safety margins;20complete7268route integrality+validatedS3single groupcut;complete schedules reaudit passed; no fullstrict optimality;07:00newlargealgorithms stopped.',next_action='Only final completed-file hash verification and pauseexistingautomationm5. No active worker. Do not start newalgorithms or cross-day tasks; preserve allcertificates and safety margins. Later workrequires explicituser resumption.');write(H/'checkpoint.json',c)
print(json.dumps(dict(final_bounds=protected,report=str(doc),manifest_files=len(set(files)),paired=paired),indent=2))
