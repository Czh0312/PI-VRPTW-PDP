"""Rebuild the combined review from both retained, frozen-code experiments."""
import hashlib
import json
from pathlib import Path
import shutil
import xml.etree.ElementTree as ET

HERE = Path(__file__).resolve().parent
FIRST = HERE.with_name('20261005_uniform_review_v1')
PROJECT = HERE.parents[2]


def read(path):
    return json.loads(path.read_text())


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2)+'\n')


def verify(study):
    experiment = read(study/'experiment.json')
    counts = {}
    for arm, provenance in experiment['arms'].items():
        root = study/arm
        for path, expected in provenance['source_hashes'].items():
            assert hashlib.sha256((root/'source_snapshot'/path).read_bytes()).hexdigest() == expected
        for record in provenance['input_hashes'].values():
            assert hashlib.sha256((root/record['snapshot']).read_bytes()).hexdigest() == record.get('snapshot_sha256', record['sha256'])
        counts[arm] = dict(source_files=len(provenance['source_hashes']), input_files=len(provenance['input_hashes']))
    arms = experiment['arms']
    assert {k:v['sha256'] for k,v in arms['before']['input_hashes'].items()} == {k:v['sha256'] for k,v in arms['after']['input_hashes'].items()}
    rows = read(study/'rows.json')
    assert len(rows) == len(experiment['jobs'])
    for row in rows:
        if row['valid']:
            audit = read(study/row['arm']/row['run']/'independent_audit.json')
            assert audit['valid'] and audit['periodic_replay']['valid'] and audit['periodic_replay']['cycles'] == 2
            assert abs(audit['cost']['total']-row['cost']) < 1e-7
    for pair in read(study/'paired_summary.json')['pairs']:
        a,b = pair['before'],pair['after']
        if a['valid'] and b['valid']:
            assert a['economic_profile_hash'] == b['economic_profile_hash']
    before_hashes,after_hashes = arms['before']['source_hashes'],arms['after']['source_hashes']
    return dict(hashes_verified=counts, total_runs=len(rows), valid_runs=sum(r['valid'] for r in rows),
                changed_source_files=sorted(k for k in before_hashes.keys() & after_hashes.keys()
                                            if before_hashes[k] != after_hashes[k]))


verification = {str(p.name):verify(p) for p in (FIRST,HERE)}
source_hashes = read(HERE/'experiment.json')['arms']['after']['source_hashes']
owned_files = ('models/routing/variants/model_6/alns/solver.py',
               'models/routing/variants/model_6/exact/model.py',
               'models/routing/variants/model_6/uniform.py',
               'experiments/runs/review_model6_search.py')
for path in owned_files:
    assert hashlib.sha256((PROJECT/path).read_bytes()).hexdigest() == source_hashes[path]
verification['final_edited_source_files_match_workspace'] = True
verification['other_source_changes_since_snapshot'] = [path for path, expected in source_hashes.items()
    if hashlib.sha256((PROJECT/path).read_bytes()).hexdigest() != expected]
test_source = Path('/private/tmp/model6_search_quality_full_v2.xml')
shutil.copy2(test_source, HERE/'full_tests.xml')
tests = ET.parse(test_source).getroot()
suites = list(tests.iter('testsuite'))
verification['tests'] = {key:sum(int(s.get(key,0)) for s in suites) for key in ('tests','failures','errors','skipped')}
assert verification['tests']['failures'] == verification['tests']['errors'] == 0
write(HERE/'independent_validation.json', verification)

pairs = []
for study, method in ((FIRST,'exact'), (HERE,'alns')):
    for pair in read(study/'paired_summary.json')['pairs']:
        if pair['method'] != method:
            continue
        pair['study'] = str(study)
        for arm in ('before','after'):
            row = pair[arm]
            if row['valid']:
                result = read(study/arm/row['run']/'result.json')
                scale = result['operating_policy']['time_tick_seconds']/60
                row.update(cycle_batch_count=result['cycle_batch_count'], delivery_batch_count=result['delivery_batch_count'],
                           actual_recovery_round_count=result['actual_recovery_round_count'],
                           phase1_minutes=result['shared_cutoff_offset_ticks']*scale,
                           batch_minutes=result['batch_duration_ticks']*scale)
                row['cutoff_polish_runtime'] = result['info'].get('cutoff_polish_runtime')
                row['cutoff_polish_improvements'] = result['info'].get('cutoff_polish_improvements')
        pairs.append(pair)
write(HERE/'final_pairs.json', pairs)

lines = ['# 模型6分界搜索检查与改进', '',
         '采用既定基础版：给定周期，K∈{1,2,3}，周期内等长批次，共用相对分界d。空箱按卸回前置仓时刻与截点比较；第二阶段仍可取箱，下一次供箱必须等总仓卸箱与清洗完成。费用未改，仍为未校准的合成单位。', '',
         '## 已修改', '',
         '- ALNS收集各批次的卸箱、回运结束、清洗赶上下次领箱及周期结束的临界点，并试探前后一个时间刻度。增加较晚回收的初始方案。',
         '- 共同分界细化仅对搜索所得最好方案执行，每个K最多预留10%预算；单次解码可能略微越界。修复首轮把16候选细化反复作为普通算子调用、挤占路线搜索的问题。',
         '- 精确MILP加入每趟行驶、服务、卸箱时长之和的有效下界，加强松弛，不改变整数可行域。极短时限下若根松弛尚未求完，已报告下界仍可能很弱。',
         '- 修复外层统计：算子尝试、改进、评价和缓存计数汇总所有K；细化统计单列，history只表示被选中的K。', '',
         '## 配对结果', '',
         '精确法每次24秒，ALNS每次10秒/最多5000次迭代，两个种子42/43；每次总预算由各K共享。两版均从零开始，串行运行并交替先后顺序。精确结果来自首轮，之后精确模型未再修改；ALNS来自预算修正后的复核轮。初轮ALNS全部结果保留在相邻的review_v1目录，没有择优混入最终结果。', '',
         '| 算例 | 方法/种子 | 修改前成本 | 修改后成本 | 箱数前→后 | 周期K前→后 |',
         '| --- | --- | ---: | ---: | --- | --- |']
for p in pairs:
    a,b = p['before'],p['after']
    cost = lambda r:f"{r['cost']:.3f}" if r['valid'] else '未找到可行解'
    lines.append(f"| {p['case']} | {p['method']}/{p['seed']} | {cost(a)} | {cost(b)} | {a['pool'] or '—'}→{b['pool'] or '—'} | {a.get('cycle_batch_count') or '—'}→{b.get('cycle_batch_count') or '—'} |")
lines += ['', '## 当前可交付方案', '',
          '在上述最终精确/ALNS尝试中，逐例选择最低成本方案；这不是最少箱数的证明。', '',
          '| 算例 | 箱数 | 周期批次数K | 配送批次数 | 每批长度/分钟 | 分界距批次开始/分钟 | 成本 |',
          '| --- | ---: | ---: | ---: | ---: | ---: | ---: |']
for case in dict.fromkeys(p['case'] for p in pairs):
    candidates = [p['after'] for p in pairs if p['case']==case and p['after']['valid']]
    best = min(candidates,key=lambda r:r['cost'])
    lines.append(f"| {case} | {best['pool']} | {best['cycle_batch_count']} | {best['delivery_batch_count']} | {best['batch_minutes']:.3f} | {best['phase1_minutes']:.3f} | {best['cost']:.3f} |")
lines += ['', '## 验证与限制', '',
          f"全部{sum(v['total_runs'] for k,v in verification.items() if isinstance(v,dict) and 'total_runs' in v)}次尝试均保留；所有有效方案通过连续两周期独立重放。源码与输入快照的哈希均核验通过，两版输入和费用口径相同。全量测试{verification['tests']['tests']}项通过。", '',
          '工作区同期另有成本报告及其他求解器改动，已在independent_validation.json单列来源差异；本实验使用冻结快照。两版的模型6物理与费用核心一致，本次修改的四个源码文件与最终快照一致。成本报告CLI变化不参与本次route求解调用。', '',
          '临界点覆盖有机制测试支撑，但有限候选局部细化不保证找到最优分界；每例两个随机种子和短预算不足以证明算法总体优越。K允许域仍只有1–3，不能宣称更大批次数无收益。成本最小不保证箱数、批次、路径同时最少；真实经济结论仍需校准费用。', '',
          'Material Passport：academic-research-suite/experiment-agent；validate；ANALYZED；model6_search_review_final。逐例保留所有种子、失败与探索轮，不作总体显著性或因果结论。11项解释检查覆盖Simpson、生态、Berkson、碰撞变量、基准率、均值回归、幸存者、多重搜索、分叉路径、相关与因果、反向因果。']
(HERE/'inspection_report.md').write_text('\n'.join(lines)+'\n')
print(json.dumps(verification,ensure_ascii=False,indent=2))
print(HERE/'inspection_report.md')
