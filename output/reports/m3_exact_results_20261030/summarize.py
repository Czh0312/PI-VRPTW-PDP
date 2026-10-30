"""Collect completed model-3 exact runs; never run an optimizer."""
import csv
import hashlib
import json
from pathlib import Path
import statistics

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[2]
EXPERIMENTS = ROOT / 'output/experiments'
STUDY = EXPERIMENTS / 'm3_network_strengthening_20261029_v1'


def read(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save_csv(path, rows, fields):
    with path.open('w', newline='', encoding='utf-8-sig') as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction='ignore')
        writer.writeheader()
        writer.writerows(rows)


all_rows = read(STUDY / 'rows.json')
assert len(all_rows) == 21
verified = {}
for row in all_rows:
    folder = STUDY / 'runs' / row['id']
    hashes = read(folder / 'completion.json')['files']
    for name, digest in hashes.items():
        assert sha(folder / name) == digest, (row['id'], name)
    result = read(folder / 'result.json')
    audit = read(folder / 'independent_audit.json')
    assert audit['valid'] and audit['periodic_replay']['valid']
    assert abs(audit['objective'] - row['objective']) < 1e-7
    assert result['model'] == 3 and result['solver'] == 'exact'
    assert result['info']['status'] == row['status'] and row['seed'] == 42
    assert bool(result['info']['proven_optimal']) == bool(row['optimal'])
    assert result['info']['proof_scope'] == 'full_model_3_integer_time_MILP'
    verified[row['id']] = hashes

current = sorted((r for r in all_rows if r['version'] == 'after'),
                 key=lambda r: (r['orders'], r['case'], r['budget']))
assert len(current) == 20
assert len({r['case'] for r in current}) == 19
assert sum(r['optimal'] for r in current) == 18
fields = ['case', 'size', 'orders', 'budget', 'seed', 'status', 'optimal',
    'total_seconds', 'proof_seconds', 'build_seconds', 'preparation_seconds',
    'main_mip_seconds', 'objective', 'bound', 'gap', 'id']
save_csv(OUT / 'current_default_runs.csv', current, fields)

history_sources = [
    ('m3_small_before_after_300s_20261029_v1', '早期小规模原版/候选版配对'),
    ('m3_general_acceleration_20261029_v1', '前一通用加强版跨规模'),
    ('m3_all_medium_exact_1800s_20261029_v1', '前一通用加强版全部中规模'),
    ('m3_network_strengthening_20261029_v1', '当前网络加强版及M03配对基线'),
]
history = []
for study, label in history_sources:
    path = EXPERIMENTS / study / 'runs.csv'
    with path.open(encoding='utf-8-sig', newline='') as stream:
        for row in csv.DictReader(stream):
            history.append(dict(row, study=study, study_label=label,
                                source_csv=str(path.relative_to(ROOT))))
history_fields = ['study', 'study_label', 'case', 'version', 'orders', 'seed', 'budget',
    'status', 'optimal', 'feasible', 'total_seconds', 'proof_seconds', 'build_seconds',
    'preparation_seconds', 'main_mip_seconds', 'objective', 'bound', 'gap', 'source_csv']
save_csv(OUT / 'formal_comparison_records.csv', history, history_fields)

old_medium = {}
with (EXPERIMENTS / 'm3_all_medium_exact_1800s_20261029_v1/runs.csv').open(encoding='utf-8-sig',newline='') as stream:
    old_medium = {r['case']: r for r in csv.DictReader(stream)}
regular = [r for r in current if r['budget'] == 300]
small = [r for r in regular if r['size'] == 'small']
medium = [r for r in regular if r['size'] == 'medium']
metadata = dict(
    model=3, objective='cost', batch_count=1, seed=42, threads=1,
    current_default_calls=len(current), current_default_distinct_cases=19,
    optimal_distinct_cases=18, audited_feasible_distinct_cases=19,
    missing_current_default_cases=[f'L{i:02}' for i in range(2,10)],
    current_source_study=STUDY.name, verified_completion_hashes=verified,
    formal_history_records=len(history), history_sources=[s for s,_ in history_sources],
    scope='Current default version is complete for its 20 completed formal calls; '
          'historical CSV includes only the four explicitly listed formal studies, not every exploratory run.',
)
(OUT / 'verification.json').write_text(json.dumps(metadata,ensure_ascii=False,indent=2),encoding='utf-8')
lines = ['# 模型3当前精确求解结果总表', '',
    '仅模型3，单批次、固定分仓、容器循环、成本目标。当前网络加强版：'
    '种子42、单线程，19个不同算例，共20次已完成正式运行；M03有300秒和1800秒两次独立运行。',
    '本次只整理已有记录，没有新增算例求解。', '',
    '总耗时计入案例加载、建模、下界/切割/初解准备、完整主模型求解、解提取和独立成本/两周期审计。'
    '解释器启动、环境预检和源码哈希核对另计。预算包含准备工作；停止与审计可能使总时间略超预算。',
    '主MILP耗时是总耗时的一部分，不包含之前的建模、辅助LP和初解求解。'
    'TIME_LIMIT的耗时是实际停止时间，不是证明最优所需时间。', '',
    '| 算例 | 单数 | 总预算/秒 | 总耗时/秒 | 主MILP/秒 | 状态 | 可行成本 | 全局下界 | GAP |',
    '| --- | ---: | ---: | ---: | ---: | --- | ---: | ---: | ---: |']
for r in current:
    status = '已证明最优' if r['optimal'] else '限时，未证明最优'
    lines.append(f'| {r["case"]} | {r["orders"]} | {r["budget"]} | {r["total_seconds"]:.3f} | '
        f'{r["main_mip_seconds"]:.3f} | {status} | {r["objective"]:.6f} | {r["bound"]:.6f} | {100*r["gap"]:.4f}% |')
lines += ['', '## 覆盖范围', '',
    f'- 小规模S01–S09：9/9证明最优，总耗时范围{min(r["total_seconds"] for r in small):.3f}–'
    f'{max(r["total_seconds"] for r in small):.3f}秒，9例合计{sum(r["total_seconds"] for r in small):.3f}秒。',
    '- 中规模M01–M09：8/9证明最优；已最优8例耗时7.922–75.960秒。M03额外1800秒确认仍未证明最优。',
    '- 大规模只跑过当前版本的L01：276.390秒证明最优。L02–L09没有当前版本正式运行记录，未填入推测结果。',
    '- 19个不同算例全部有通过独立成本、连续两周期审计的可行解；最优标记来自完整原业务MILP原生证明。', '',
    '## M03前后对照', '',
    '| 版本 | 预算/秒 | 总耗时/秒 | 可行成本 | 下界 | GAP |',
    '| --- | ---: | ---: | ---: | ---: | ---: |']
before = next(r for r in all_rows if r['version']=='before')
for label,r in [('前一通用版（本次重新运行）',before),
                ('当前网络加强版',next(r for r in current if r['case']=='M03' and r['budget']==300))]:
    lines.append(f'| {label} | 300 | {r["total_seconds"]:.3f} | {r["objective"]:.6f} | {r["bound"]:.6f} | {100*r["gap"]:.4f}% |')
old = old_medium['M03']
new = next(r for r in current if r['case']=='M03' and r['budget']==1800)
lines += [f'| 前一通用版（历史记录） | 1800 | {float(old["total_seconds"]):.3f} | {float(old["objective"]):.6f} | '
    f'{float(old["bound"]):.6f} | {100*float(old["gap"]):.4f}% |',
    f'| 当前网络加强版 | 1800 | {new["total_seconds"]:.3f} | {new["objective"]:.6f} | '
    f'{new["bound"]:.6f} | {100*new["gap"]:.4f}% |', '',
    'M03四条均为限时未证最优。两次当前版分别新建模型，1800秒运行没有续接300秒的搜索状态。', '',
    '## 其他中规模的历史时间参考', '',
    '旧时间来自前一通用加强版的1800秒上限实验；新时间来自本次300秒上限。'
    '下列8例两边均证明最优，目标值一致，但不是本次重新执行的配对速度实验。', '',
    '| 算例 | 前一通用版总秒数 | 当前版总秒数 | 最优成本 |',
    '| --- | ---: | ---: | ---: |']
for r in medium:
    if r['case']=='M03':
        continue
    old = old_medium[r['case']]
    assert old['optimal']=='True' and abs(float(old['objective'])-r['objective'])<1e-7
    lines.append(f'| {r["case"]} | {float(old["total_seconds"]):.3f} | {r["total_seconds"]:.3f} | {r["objective"]:.6f} |')
lines += ['', '## 原始证据与历史记录', '',
    '- `current_default_runs.csv`包含当前默认版全部20次正式调用的时间分项、状态、上下界和运行ID。',
    f'- `formal_comparison_records.csv`收录明确列出的4轮正式实验，共{len(history)}条历史/当前运行记录；'
    '不同源码版本单列，不以最优值相同合并耗时，不包含全部研究原型。',
    '- `verification.json`记录统计范围和逐次完成文件哈希核验。本次全部21份完成文件（含配对基线）一致。',
    '- 当前版既有回归证据：5个选定文件135通过、0失败、0错误、0跳过；本次没有重跑测试。',
    '- 原始结果、逐次独立审计和原生日志：`output/experiments/m3_network_strengthening_20261029_v1/runs/`。', '',
    '历史正式CSV来源：', '']
for source,label in history_sources:
    lines.append(f'- {label}：`output/experiments/{source}/runs.csv`')
lines += ['', '单种子结果不表示已验证多种子稳定性。容易实例可能因额外加强成本而变慢；未证明最优的运行不计作精确最优解。', '']
(OUT / 'report.md').write_text('\n'.join(lines),encoding='utf-8')
print(json.dumps({k:v for k,v in metadata.items() if k!='verified_completion_hashes'},ensure_ascii=False),flush=True)
