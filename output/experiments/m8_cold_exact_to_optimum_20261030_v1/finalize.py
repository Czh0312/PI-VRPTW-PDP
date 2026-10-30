"""Publish final cold-start times only after all nine audited native optima."""
from collections import Counter
import json
from pathlib import Path
import statistics
import xml.etree.ElementTree as ET

from protocol import HERE,ROOT,read,save,sha


def xml_counts(path):
    xml=ET.parse(path).getroot()
    suites=list(xml.iter('testsuite'))
    counts={k:sum(int(s.get(k,0)) for s in suites) for k in ('tests','failures','errors','skipped')}
    counts['passed']=counts['tests']-sum(counts[k] for k in ('failures','errors','skipped'))
    assert counts['tests'] and counts['failures']==counts['errors']==counts['skipped']==0
    return counts


def main():
    plan,status=read(HERE/'plan.json'),read(HERE/'batch_status.json')
    assert status['stage']=='complete' and status['completed']==plan['cases']
    assert all(sha(HERE/name)==digest for name,digest in plan['driver_hashes'].items())
    assert {p.relative_to(HERE/'source').as_posix():sha(p) for p in (HERE/'source').rglob('*.py')}==plan['source_hashes']
    assert all(sha(Path(path))==digest for path,digest in plan['input_hashes'].items())
    rows=[]
    for case in plan['cases']:
        folder=HERE/'runs'/case
        row,result,audit=(read(folder/name) for name in ('summary.json','result.json','audit.json'))
        assert row['proven_optimal'] and row['status']=='OPTIMAL'
        assert result['info']['complete_count_domain'] and result['info']['proven_optimal']
        assert audit['valid'] and audit['periodic_replay']['valid']
        assert abs(row['objective']-audit['objective'])<=1e-6
        assert abs(row['objective']-row['best_bound'])<=max(1e-6,abs(row['objective'])*1e-7)
        assert not row['historical_solution_read'] and not row['known_optimum_target_used']
        assert not row['external_initial_solution'] and row['blocked_read_count']==0
        rows.append(row)
    protocol=xml_counts(HERE/'protocol_checks.xml')
    validation=xml_counts(HERE/'evidence_checks.xml')
    times=[row['total_cold_solve_seconds'] for row in rows]
    stats=dict(cases=9,root_seeds=[42],native_global_optima=9,independent_two_cycle_audits=9,
        min_seconds=min(times),median_seconds=statistics.median(times),mean_seconds=statistics.mean(times),
        max_seconds=max(times),sum_case_seconds=sum(times),batch_elapsed_seconds=status['elapsed_batch_seconds'],
        historical_solutions_read=False,reference_optimum_target_used=False,practical_time_cap=None,
        protocol_checks=protocol,evidence_checks=validation,rows=rows)
    save(HERE/'final_statistics.json',stats)
    lines=['# 模型八：九个小规模算例冷启动求到全局最优的时间', '',
        '九例均只从原始输入启动，根种子42、单线程、串行；未读取历史可行方案、历史下界或参考最优值，'
        '没有提供MIP Start、变量提示或目标费用。用户要求持续直到证明最优，未设置实际时间上限。', '',
        '每例原生Gurobi OPTIMAL、完整原始K=1…7、业务及两周期独立审计均通过，**9/9完成全局最优性证明**。'
        '达到某个已知费用从未被用作停止条件。', '',
        '| 算例 | 首次原生整数解 / 秒 | 建模 / 秒 | 原生优化 / 秒 | 冷启动证明总时间 / 秒 | 总时间 / 分钟 | 最优费用 | K | 箱池N |',
        '| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |']
    for row in rows:
        first='—' if row['first_native_integer_solution_seconds'] is None else f"{row['first_native_integer_solution_seconds']:.3f}"
        lines.append(f"| {row['case']} | {first} | {row['build_seconds']:.3f} | {row['native_optimization_seconds']:.3f} "
            f"| {row['total_cold_solve_seconds']:.3f} | {row['total_cold_solve_seconds']/60:.3f} "
            f"| {row['objective']:.9f} | {row['K']} | {row['N']} |")
    lines.extend(['',f"最短 {min(times):.3f}秒，中位数 {statistics.median(times):.3f}秒，平均 {statistics.mean(times):.3f}秒，"
        f"最长 {max(times):.3f}秒；九例冷启动时间合计 {sum(times):.3f}秒。", '',
        '“冷启动证明总时间”从读取原始算例前开始，包含输入加载、预检查、完整统一MILP建模、'
        '原生优化至OPTIMAL、方案提取与算法内部审计。进程/模块初始化及额外最终独立审计另列于各summary.json。'
        '首次原生整数解为观察回调时刻，并非首次独立业务审计通过时刻。', '',
        '精确端为冻结的当前直接Gurobi公开入口，启用compact、physical_time、preprocessing和vehicle_symmetry；'
        '原始资源、费用和整数时钟未变，模型进入优化时NumStart=0、Threads=1、Seed=42、MIPGap=0、MIPGapAbs=0。'
        '观察回调只记录原生进度和日志，不提交解、割、提示或新的约束。', '',
        '先前几秒的“已有可行方案＋新下界闭合”证明时间属于另一种口径。本表只列本轮从原始输入开始，'
        '不使用已有方案的完整求解时间，不引用之前的路线或证书作为本轮证据。', '',
        '## 验证范围', '',
        f"环境预检READY，Python3.11.3、Gurobi11.0.3，许可及实际整数优化通过。协议检查：{protocol['passed']}通过、"
        f"{protocol['failures']}失败、{protocol['skipped']}跳过；最终本轮证据检查：{validation['passed']}通过、"
        f"{validation['failures']}失败、{validation['skipped']}跳过。未运行完整仓库测试套件。", '',
        f"冻结源文件{len(plan['source_hashes'])}个、原始输入{len(plan['input_hashes'])}个及驱动哈希在运行前后保持一致。"
        '每例均保留原生日志、回调进度、最终JSON、独立审计、输入读取路径及无MIP Start记录。', '',
        '准备阶段前两轮协议检查分别发现测试临时路径含中文的原生日志API限制、无限时限的JSON序列化问题，'
        '已在任何正式算例启动前修复并通过3项检查；首轮和第二轮失败XML保留，未改动数学模型或求解策略。', '',
        '本轮只有一个根种子，不据此声称多种子稳定性。浮点最优性以Gurobi原生容差及独立审计为准。', '',
        '[统计与逐例记录](final_statistics.json) · [原始计划和SHA256](plan.json) · '
        '[逐例原生日志与路线结果](runs) · [最终证据检查](evidence_checks.xml)', ''])
    (HERE/'final_report.md').write_text('\n'.join(lines),encoding='utf-8')
    files=[p for p in (HERE/'runs').rglob('*') if p.is_file()]
    files += [HERE/name for name in ('bench.py','protocol.py','plan.json','batch_status.json','final_statistics.json',
        'final_report.md','protocol_checks.xml','evidence_checks.xml','test_evidence.py','finalize.py')]
    save(HERE/'evidence_manifest.json',dict(files={p.relative_to(HERE).as_posix():sha(p) for p in files}))
    print(json.dumps(dict(completed=9,statistics={k:v for k,v in stats.items() if k!='rows'},report=str(HERE/'final_report.md')),
                     ensure_ascii=False))


if __name__=='__main__':
    main()
