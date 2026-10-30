"""Report the two additional M3 medium attempts, with the prior M01 labelled."""
from pathlib import Path
import hashlib
import json
import xml.etree.ElementTree as ET

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[2]


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def display(value, digits=3):
    return f'{value:.{digits}f}' if value is not None else '无'


if __name__ == '__main__':
    plan, rows = read(OUT / 'plan.json'), read(OUT / 'rows.json')
    assert len(rows) == 2
    for path, digest in plan['source_hashes_and_inputs'].items():
        assert sha(ROOT / path) == digest, path
    suites = ET.parse(OUT / 'validation.xml').getroot().findall('testsuite')
    counts = {key: sum(int(s.attrib.get(key, 0)) for s in suites)
              for key in ('tests','failures','errors','skipped')}
    prior_path = ROOT / 'output/experiments/m3_m01_exact_300s_20261029_v1/summary.json'
    prior = read(prior_path)
    summary = dict(new_cases=2, new_exact_calls=2, seeds=[42],
        new_proven_optimal=sum(row['proven_optimal'] for row in rows),
        new_audited_feasible=sum(row['audited_feasible'] for row in rows),
        new_total_wall_seconds=sum(row['total_wall_seconds'] for row in rows),
        tests=counts, full_suite_run=False, small_acceleration_applied=False,
        prior_M01_reference=dict(path=prior_path.relative_to(ROOT).as_posix(), sha256=sha(prior_path)))
    (OUT / 'validation.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
    lines = ['# 模型3：补测中规模 M02、M03', '',
        '2026-10-29。本轮仅新增M02、M03两次精确求解；均20单、种子42、单线程、expanded表达、'
        '每例300秒预算，串行执行。不供应历史方案或上下界。最优证明来自原完整模型，另作业务、成本和两周期审计。', '',
        '当前新增车次枚举只支持最多9单，两例均记录applied=false、outside_small_fixed_assignment_scope，'
        '回退完整MILP。未修改生产模型、固定分仓、日历、箱池、整数时间或费用。', '',
        '| 算例 | 总耗时/秒 | 状态 | 可行成本 | 下界 | GAP | 证最优 |',
        '|---|---:|---|---:|---:|---:|---|']
    for label, row in [('M01（前次，仅引用）', prior)] + [(row['case'], row) for row in rows]:
        gap = f"{100*row['mip_gap']:.3f}%" if row['mip_gap'] is not None else '无可行解'
        lines.append(f"| {label} | {row['total_wall_seconds']:.3f} | {row['status']} | "
                     f"{display(row['objective'], 6)} | {display(row['best_bound'], 6)} | {gap} | "
                     f"{'是' if row['proven_optimal'] else '否'} |")
    lines += ['', '**结构与实际模型规模。**', '',
              '| 本轮算例 | 各仓订单数 | 变量 | 二进制变量 | 约束合计 |',
              '|---|---|---:|---:|---:|']
    for row in rows:
        groups = ', '.join(f'{site}:{count}' for site, count in row['satellite_order_counts'].items())
        lines.append(f"| {row['case']} | {groups} | {row['variables']} | {row['binary_variables']} | {row['constraints']} |")
    lines += ['', '表中是加载算例、建模、优化、提取和独立审计的总墙钟；Python/运行器启动、预检、'
        '源码归档及后续证据测试不计入。两例成本来自不同物理网络，不用其绝对成本评价算法速度。'
        '单种子单次结果只代表本次输入和机器负载。', '',
        'TIME_LIMIT若出现，表示在本次预算内未证明最优，不能解释为不可行；其可行方案仅作为上界。'
        '没有可行解时不计算GAP，也不把空方案称为已审计方案。', '',
        f"本轮选定test_evidence.py：{counts['tests']-counts['failures']-counts['errors']-counts['skipped']} passed、"
        f"{counts['failures']} failed、{counts['errors']} errors、{counts['skipped']} skipped；未运行全仓库测试。"
        '项目预检READY，Gurobi11.0.3实际整数求解通过。', '',
        'M02/、M03/分别包含完整原生日志、原业务结果、独立审计、统计与结果哈希；'
        'rows.json汇总本轮两次；plan.json/source_inputs.zip冻结原输入与源码；'
        'validation.xml/validation.json为实际验证范围；evidence_manifest.json为归档校验。', '']
    (OUT / 'report.md').write_text('\n'.join(lines), encoding='utf-8')
    files = {p.relative_to(OUT).as_posix(): sha(p) for p in OUT.rglob('*')
             if p.is_file() and '__pycache__' not in p.parts and p.name != 'evidence_manifest.json'}
    (OUT / 'evidence_manifest.json').write_text(json.dumps(dict(files=files), ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(summary, ensure_ascii=False), flush=True)
