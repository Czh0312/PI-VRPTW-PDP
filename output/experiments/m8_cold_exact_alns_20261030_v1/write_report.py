"""Write the cold-start comparison from measured evidence and validation XML."""
from collections import Counter
import hashlib
import json
from pathlib import Path
import statistics
import xml.etree.ElementTree as ET

HERE = Path(__file__).resolve().parent


def read(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def fmt(value, places=3):
    return '—' if value is None else f'{value:.{places}f}'


def counts(path):
    xml = ET.parse(path).getroot()
    suites = [xml] if xml.tag == 'testsuite' else list(xml.iter('testsuite'))
    result = {key:sum(int(s.get(key,0)) for s in suites) for key in ('tests','failures','errors','skipped')}
    result['passed'] = result['tests']-sum(result[k] for k in ('failures','errors','skipped'))
    assert result['tests'] and result['failures'] == result['errors'] == result['skipped'] == 0
    return result


def main():
    data, plan = read(HERE/'analysis.json'),read(HERE/'plan.json')
    protocol, evidence = counts(HERE/'protocol_checks.xml'),counts(HERE/'evidence_checks.xml')
    final = data['budgets']['30']['metrics']
    names = {'alns':'ALNS','exact':'Gurobi（紧凑＋预处理＋车辆对称性）'}
    lines = ['# 模型八：ALNS 与 Gurobi 同条件冷启动对照', '',
        '2026-10-30。S01–S09全部九个小规模算例；唯一根种子42，5秒和30秒两档，'
        '36个独立进程串行执行。两种方法均只读取原始输入，完全不读取历史可行方案；'
        '每个预算重新从头开始，也不复用上一档的结果。ALNS会根据总预算分配构造与跨K搜索时间，'
        '因此独立30秒运行不保证逐例优于独立5秒运行。', '',
        '## 本轮实测', '',
        '| 总预算 | 方法 | 可行解 / 9 | 命中已知最优费用 / 9 | 原生最优性证明 / 9 | 九例平均 GAP |',
        '| --- | --- | ---: | ---: | ---: | ---: |']
    for seconds, group in data['budgets'].items():
        for method in ('alns','exact'):
            m = group['metrics'][method]
            gap = '—（有未求出可行解）' if m['mean_gap_all_cases_percent'] is None else f"{m['mean_gap_all_cases_percent']:.4f}%"
            lines.append(f"| {seconds}s | {names[method]} | {m['feasible']} | {m['known_optimum_hits']} "
                         f"| {m['native_optimal_proofs']} | {gap} |")
    lines.extend(['', '“命中最优费用”由实验结束后与已证明的最优值比较；ALNS不提供全局最优性证明。'
        'Gurobi的“原生证明”须同时有独立业务审计通过的最终方案和覆盖全原始K域的OPTIMAL状态。'
        '没有找到可行方案时，GAP不可定义，不能写成0或只取容易算例后称为九例平均。', ''])
    if final['alns']['feasible'] > final['exact']['feasible']:
        lines.append('在本轮短预算冷启动中，ALNS更快提供可执行方案。'
                     '此前依据几秒证书耗时推断Gurobi冷启动更快，缺少同口径依据；这轮数据不支持该推断。')
    elif final['alns']['feasible'] == final['exact']['feasible'] == 9:
        better = 'ALNS' if final['alns']['mean_gap_all_cases_percent'] < final['exact']['mean_gap_all_cases_percent'] else 'Gurobi'
        lines.append(f'在本轮30秒冷启动中，两种方法都返回九例可行方案，{better}的平均费用差距更小。')
    else:
        lines.append('两种方法的可行解成功率、费用质量和证明能力应分别比较，不能仅凭某一例的返回时间判断整体效率。')
    lines.extend(['', '这只是单根种子、每条件一次的当前九例结果，不能外推为所有种子、预算或规模的速度排序。', '',
        '## 逐例对照', ''])
    for seconds, group in data['budgets'].items():
        lines.extend([f'### {seconds}秒：输入加载、建模及算法内部工作共用预算', '',
            '| 算例 | ALNS费用 | ALNS GAP | Gurobi费用 | Gurobi GAP | Gurobi状态 |',
            '| --- | ---: | ---: | ---: | ---: | --- |'])
        for p in group['pairs']:
            a = '—' if p['alns_gap_percent'] is None else f"{p['alns_gap_percent']:.3f}%"
            e = '—' if p['exact_gap_percent'] is None else f"{p['exact_gap_percent']:.3f}%"
            lines.append(f"| {p['case']} | {fmt(p['alns_cost'],6)} | {a} | {fmt(p['exact_cost'],6)} | {e} | {p['exact_status']} |")
        lines.extend(['', 'TIME_LIMIT_BUILD表示预算在建模期间耗尽；TIME_LIMIT且费用为空表示进入优化后仍没有返回可行解。'
                      '这两种状态都不代表算例不可行。', ''])
    lines.extend(['## 命中已知最优费用的记录', '',
        '| 预算 | 方法 | 算例 | 已记录命中时刻 / 秒 |', '| --- | --- | --- | ---: |'])
    hits = [r for r in data['rows'] if r['known_optimum_hit']]
    for row in hits:
        lines.append(f"| {row['budget_seconds']:g}s | {row['method']} | {row['case']} | {fmt(row['recorded_time_to_known_optimum_seconds'])} |")
    if not hits:
        lines.append('| — | — | 本轮无命中 | — |')
    lines.extend(['', '时刻来自求解期间保存的原生日志，事后才与最优值匹配。'
        'ALNS为内部审计后保存记录，Gurobi为原生整数解事件；不能解释成连续轨迹的精确首次业务审计时刻。'
        'Gurobi最优性证明仍独立列示。', ''])
    lines.extend(['## 时间记录', '',
        '| 预算 | 方法 | 实际返回范围 / 秒 | 平均 CPU / 秒 | 平均独立进程总耗时 / 秒 |',
        '| --- | --- | ---: | ---: | ---: |'])
    for seconds, group in data['budgets'].items():
        for method,m in group['metrics'].items():
            lo,hi = m['wall_seconds_range']
            lines.append(f"| {seconds}s | {method} | {lo:.4f}–{hi:.4f} | {m['mean_cpu_seconds']:.4f} | {m['mean_process_wall_seconds']:.4f} |")
    lines.extend(['', '总预算从读取原始算例前开始；输入加载后把剩余时间交给公开求解入口。'
        '包含预检查、Gurobi建模与优化，或ALNS构造、搜索、内部热启动、精修和内部审计。'
        'Python进程/模块初始化及最终独立审计另行计时，不占两种方法相同的求解预算。'
        '模型清理、提取和审计收尾可能略超过名义时限，以上如实报告实际返回时间。'
        '独立进程总耗时还包含模块导入、最终审计及写盘，单独列出。', '',
        'Gurobi建模与优化分项：', '',
        '| 档位 | 算例 | 建模 / 秒 | 原生优化 / 秒 | 状态 |',
        '| --- | --- | ---: | ---: | --- |'])
    for row in data['rows']:
        if row['method'] == 'exact':
            lines.append(f"| {row['budget_seconds']:g}s | {row['case']} | {fmt(row['build_seconds'])} | {fmt(row['mip_seconds'])} | {row['status']} |")
    lines.extend(['', '建模被限时中止时入口未返回完整建模统计，分项以空白表示；其开销已计入实际返回时间。'
        '原生MIPSOL事件与ALNS内部审计后的保存事件保存在各运行的events.json。'
        '两者的首条记录具有不同审计粒度，因此不把它们直接当作相同的首次业务审计通过时间。'
        '最终费用始终以原输入域的独立审计为准。', '',
        '## 同条件与冷启动约束', '',
        '- 同一批原始数据、费用、资源、原始K=1…7、整数物理时钟与未额外收紧的决策网格。',
        '- Gurobi使用当前公开 `solve_exact` 直接统一MILP：`compact`、`physical_time`、'
        '`preprocessing=True`、`vehicle_symmetry=True`、单线程、种子42；所有优化前模型 `NumStart=0`。',
        '- ALNS使用当前公开 `solve_alns`，`adaptive`调度、迭代上限10^9、外部 `initial_result=None`；'
        '内部热启动及跨K复用仅使用本轮从原始输入新生成的方案，所有工作共用本轮总预算。',
        '- 每次运行新进程；各例交替两方法的先后顺序，30秒档再反转顺序；不并发运行两种求解任务。'
        '系统负载和墙钟截止仍会影响单次结果。',
        '- 使用工作区文件读取白名单：只允许已冻结原始输入及源代码读取，禁止历史结果和证书内容进入工作进程。'
        '每个工作进程实际读取的输入路径保存在summary.json；读取历史结果会直接使运行失败。',
        '- Gurobi增加的MIPSOL回调只向内存记录时间与目标值，不提交解、不加割、不修改变量界或参数。'
        'ALNS沿用已有历史记录；未改变两种算法的搜索实现。',
        '- 所有36次搜索结束后，单独的analyze.py才读取历史summary.json中的最优费用和证明元数据，'
        '仅作GAP及命中统计；本轮没有读取历史路线JSON。', '',
        '本次测的是当前正式直接Gurobi模型，并启用了已验证的输入推导优化；'
        '此前约2–14秒的研究程序主要将新下界与历史可行方案闭合，不是本次冷启动求解入口，不能混用耗时。', '',
        '## 复核与复现', '',
        f"冻结310个源文件、81个输入文件及驱动SHA256，运行前后均一致。环境预检READY：Python3.11.3、Gurobi11.0.3，"
        '许可初始化与实际整数优化成功。', '',
        f"选定协议检查 `test_protocol.py`：**{protocol['passed']}通过、{protocol['failures']}失败、{protocol['skipped']}跳过**；"
        f"选定证据检查 `test_evidence.py`：**{evidence['passed']}通过、{evidence['failures']}失败、{evidence['skipped']}跳过**。"
        '未运行完整仓库测试套件。证据检查不再求解这些算例，也未增加实验种子。', '',
        '[数值汇总](analysis.json) · [原始实验计划与哈希](plan.json) · [全部结果](runs) · '
        '[驱动](bench.py) · [证据测试](test_evidence.py)', ''])
    (HERE/'report.md').write_text('\n'.join(lines),encoding='utf-8')
    evidence_files = [p for folder in ('runs','logs') for p in (HERE/folder).rglob('*') if p.is_file()]
    evidence_files += [HERE/p for p in ('bench.py','plan.json','analysis.json','completion.json','report.md',
        'protocol_checks.xml','evidence_checks.xml','test_protocol.py','test_evidence.py','analyze.py')]
    manifest = {p.relative_to(HERE).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in evidence_files}
    (HERE/'evidence_manifest.json').write_text(json.dumps(dict(files=manifest, protocol_tests=protocol,
        evidence_tests=evidence),ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(dict(report=str(HERE/'report.md'),protocol=protocol,evidence=evidence),ensure_ascii=False))


if __name__ == '__main__':
    main()
