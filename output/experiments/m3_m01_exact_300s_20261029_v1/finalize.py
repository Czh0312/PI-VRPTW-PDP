"""Summarize the original-domain medium attempt and actual acceleration scope."""
import hashlib
import json
from pathlib import Path
import xml.etree.ElementTree as ET

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[2]


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


if __name__ == '__main__':
    row, plan = read(OUT / 'summary.json'), read(OUT / 'plan.json')
    for name, expected in plan['hashes'].items():
        assert hashlib.sha256((ROOT / name).read_bytes()).hexdigest() == expected
    suites = ET.parse(OUT / 'validation.xml').getroot().findall('testsuite')
    counts = {key: sum(int(s.attrib.get(key, 0)) for s in suites)
              for key in ('tests','failures','errors','skipped')}
    def number(value):
        return f'{value:.6f}' if value is not None else '无'
    lines = ['# 模型3中规模 M01：当前默认精确入口实测', '',
        '2026-10-29。20单，分别归属于两个仓，各10单；单批、固定归属、固定日历、固定箱池20；'
        '直接优化原成本函数。只运行这一例、一个种子42、单线程，预算300秒，expanded表达。', '',
        '**加速适用范围。** 调用了当前优化后的默认精确入口，但新增车次枚举只适用于最多9单。'
        '本例20单，实际记录 applied=false、reason=outside_small_fixed_assignment_scope，'
        '正常回退完整原MILP；没有读取历史方案、缩小业务范围或强制提高枚举上限。'
        '本轮结果不能解释为新增小规模车次加速已经适配中规模。', '',
        '| 指标 | 结果 |', '|---|---:|',
        f"| 原生状态 | {row['status']} |",
        f"| 已证明最优 | {'是' if row['proven_optimal'] else '否'} |",
        f"| 有独立审计可行方案 | {'是' if row['audited_feasible'] else '否'} |",
        f"| 总墙钟秒 | {row['total_wall_seconds']:.3f} |",
        f"| 建模秒 | {row['build_time']:.3f} |",
        f"| 主模型优化秒 | {row['mip_runtime']:.3f} |",
        f"| 可行成本 | {number(row['objective'])} |",
        f"| Gurobi 下界 | {number(row['best_bound'])} |",
        f"| 相对差距 | {100*row['mip_gap']:.3f}% |" if row['mip_gap'] is not None else '| 相对差距 | 无可行解，不能计算 |',
        f"| 变量 | {row['variables']} |", f"| 二进制变量 | {row['binary_variables']} |",
        f"| 约束合计 | {row['constraints']} |", f"| 搜索节点 | {row['node_count']} |", '']
    if row['status'] == 'TIME_LIMIT':
        lines += ['时间限制表示本次未完成最优性证明；若无可行方案，也仅表示本次未找到可行解，不能据此判定问题不可行。', '']
    lines += ['计时从重新加载案例开始，包含完整建模、优化、解提取和末端独立审计；'
        '不含Python/运行器启动、环境预检和源码归档。完整原生日志见 gurobi.log。', '',
        f"本次仅选择本目录 test_evidence.py：{counts['tests']-counts['failures']-counts['errors']-counts['skipped']} passed、"
        f"{counts['failures']} failed、{counts['errors']} errors、{counts['skipped']} skipped。"
        '没有重跑全仓库测试；单次优化和独立审计不冒充整个精确测试集。项目预检READY，Gurobi11.0.3实际整数优化通过。', '',
        'plan.json/source_inputs.zip：冻结输入、源码和设置；result.json：原业务结果；summary.json：完整统计；'
        'independent_audit.json：有方案时的末端独立审计；validation.xml：实际专项测试；evidence_manifest.json：输出哈希。', '']
    (OUT / 'report.md').write_text('\n'.join(lines), encoding='utf-8')
    files = {p.relative_to(OUT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
             for p in OUT.rglob('*') if p.is_file() and '__pycache__' not in p.parts and p.name != 'evidence_manifest.json'}
    (OUT / 'evidence_manifest.json').write_text(json.dumps(dict(files=files), ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(dict(status=row['status'], optimal=row['proven_optimal'], objective=row['objective'],
                         bound=row['best_bound'], gap=row['mip_gap'], validation=counts), ensure_ascii=False), flush=True)
