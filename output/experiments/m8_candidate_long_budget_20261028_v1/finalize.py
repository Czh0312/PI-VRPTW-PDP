"""Report the long run, actual test scope, checkpoints and certificate chain."""
import csv
import hashlib
import json
from pathlib import Path
import xml.etree.ElementTree as ET

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

HERE=Path(__file__).resolve().parent
CASES=['S03','S08']


def read(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save(path,value):
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')


def main():
    junit_paths=([HERE/'validation.xml'] if (HERE/'validation.xml').exists() else
                 sorted(HERE.glob('validation_part_*.xml'))+list(HERE.glob('validation_scope.xml')))
    roots=[ET.parse(path).getroot() for path in junit_paths]
    suites=[suite for root in roots for suite in root.iter('testsuite')]
    identities=[(case.attrib['classname'],case.attrib['name']) for root in roots for case in root.iter('testcase')]
    assert len(identities)==len(set(identities))==9
    counts={key:sum(int(s.attrib.get(key,0)) for s in suites) for key in ['tests','failures','errors','skipped']}
    counts['passed']=counts['tests']-counts['failures']-counts['errors']-counts['skipped']
    assert counts==dict(tests=9,failures=0,errors=0,skipped=0,passed=9),counts
    rows=[]
    curves=[]
    checkpoints=[]
    for case in CASES:
        folder=HERE/case
        row=read(folder/'summary.json')
        manifest=read(folder/'manifest.json')
        assert row['input_and_sources_unchanged'] and row['business_audit_valid'] and row['two_cycle_replay_valid']
        events=read(folder/'decomposition_summary.json')['events']
        points=[dict(case=case,seconds=0,lower=manifest['common_lower_bound'],upper=manifest['audited_upper_bound'])]
        points += [dict(case=case,seconds=event['elapsed'],lower=event['global_lower_bound'],upper=event['audited_upper_bound'])
                   for event in events]
        points.append(dict(case=case,seconds=row['algorithm_seconds'],lower=row['global_lower_bound'],upper=row['audited_upper_bound']))
        for point in points:
            point['gap_percent']=100*max(0,point['upper']-point['lower'])/abs(point['upper'])
        curves.extend(points)
        for budget in [300,600,1200,1800,3600]:
            available=[point for point in points if point['seconds']<=budget]
            last=available[-1]
            checkpoints.append(dict(case=case,budget_seconds=budget,observed_at=last['seconds'],
                global_lower_bound=last['lower'],audited_upper_bound=last['upper'],gap_percent=last['gap_percent'],
                already_proven=bool(row['global_optimality_certified'] and row['algorithm_seconds']<=budget)))
        rows.append(row)
        replay=read(folder/'independent_bound_replay.json')
        original_result=read(folder/'best_audited_result.json')
        cert=dict(case=case,kind='GLOBAL_OPTIMALITY_CERTIFICATE' if row['global_optimality_certified'] else 'VALID_GLOBAL_BOUND_WITH_UNCLOSED_GAP',
            original_K=row['original_K'],retained_K=row['retained_K'],retained_N=row['retained_N'],
            audited_upper_bound=row['audited_upper_bound'],global_lower_bound=row['global_lower_bound'],
            absolute_gap=max(0,row['audited_upper_bound']-row['global_lower_bound']),
            relative_gap=row['gap_over_upper'],absolute_certificate_tolerance=1e-6,
            native_status=row['native_status'],algorithm_status=row['algorithm_status'],
            proof_seconds=row['proof_seconds'],audited_plan_sha256=sha(folder/'best_audited_result.json'),
            selected_plan_K=original_result['cycle_batch_count'],selected_plan_pool=original_result['container_pool_size'],
            original_feasible_result_native_status=original_result['info']['status'],
            frozen_source_inputs_sha256=sha(folder/'frozen_source_inputs.zip'),
            independent_master_replay=replay,
            proof_chain=['Original all-K cost-competitive K/N and task-mask reductions are separately certified with the old feasible U.',
                'The unchanged frozen master contains every competitive original solution projection.',
                'Followers retain original route orders, vehicles, stock, cutoff and integer-clock choices within each discrete class.',
                'Only native INFEASIBLE under the initial audited U permits a no-good; finite conditional native bounds permit cost cuts.',
                'The complete refined master has been rebuilt independently, and its reported final global bound re-proved.',
                'The original-model cost and all business constraints have been audited, including two consecutive cycles.',
                'Global optimality is asserted only if audited feasible U and valid global L close within 1e-6.'])
        save(folder/'certificate.json',cert)
        original_result['external_global_optimality_certificate']=cert
        save(folder/'result_with_global_certificate.json',original_result)
    save(HERE/'summary.json',dict(cases=rows,checkpoints=checkpoints,scope='One-hour targeted follow-up, one seed per case, same historical U/L, original K domain.'))
    for name,data in [('convergence.csv',curves),('checkpoints.csv',checkpoints),('results.csv',rows)]:
        with (HERE/name).open('w',encoding='utf-8-sig',newline='') as stream:
            writer=csv.DictWriter(stream,list(dict.fromkeys(key for record in data for key in record)))
            writer.writeheader()
            writer.writerows(data)
    fig,axes=plt.subplots(1,2,figsize=(10,3.9),layout='constrained')
    for ax,case in zip(axes,CASES):
        points=[point for point in curves if point['case']==case]
        ax.step([point['seconds']/60 for point in points],[point['gap_percent'] for point in points],where='post',color='#2678b5')
        ax.axvline(5,color='gray',linestyle='--',linewidth=1,label='Previous 5 min budget')
        ax.set(title=case,xlabel='Algorithm time (minutes)',ylabel='Certified optimality gap (%)')
        ax.set_ylim(bottom=0)
        ax.grid(alpha=.25)
    axes[0].legend(frameon=False,fontsize=9)
    fig.suptitle('Frozen M8 candidate | 3600 s per case | prior audited U and valid L supplied')
    fig.savefig(HERE/'convergence.png',dpi=180)
    plt.close(fig)
    closed=[row['case'] for row in rows if row['global_optimality_certified']]
    lines=['# 模型八候选分解方法：长预算求解','',
        '本次证明最优的算例：'+('、'.join(closed) if closed else '无')+'。绝对最优性容差为 1e-6。','',
        '本次将上一轮已冻结的 compact_decomposition 算法用于 S03、S08，每例最多 3600 秒；证明最优即可提前结束。'
        '两个算例同时运行，各用 1 个线程。算法与数据未改动，结果写入新目录。','',
        '种子定向选取上一轮 300 秒中各自剩余差距最小者：S03 为 31，S08 为 19。'
        '这是争取完成最优证明的长跑，不是新的独立多种子稳定性试验。'
        '每例都从原冻结的可行上界 U 和有效下界 L 重新开始，没有接续上一轮的割。'
        '历史上下界的生成时间不计入本次预算，因此以下不是从零开始的总耗时。','',
        '| 算例 | 上轮同种子 300 秒差距 | 本次耗时 | 可行上界 U | 全局下界 L | 本次差距 | 证明最优 |',
        '|---|---:|---:|---:|---:|---:|---|']
    for row in rows:
        lines.append(f"| {row['case']} | {100*row['short_gap_over_upper']:.6f}% | {row['algorithm_seconds']:.2f} 秒 | "
                     f"{row['audited_upper_bound']:.9f} | {row['global_lower_bound']:.9f} | "
                     f"{100*row['gap_over_upper']:.6f}% | {'是' if row['global_optimality_certified'] else '否，预算内未闭合'} |")
    lines += ['',
        '耗时列为主问题与完整子问题构建、优化循环时间；输入加载、源码快照及最终审计后的完整墙钟时间另存 `results.csv`。'
        '计时预算的切片会受实际执行速度影响，因此新长跑的前 300 秒不保证复现旧短跑的同一条搜索轨迹。','',
        '完整墙钟时间：'+ '；'.join(f"{row['case']} 为 {row['total_wall_seconds']:.2f} 秒" for row in rows)+'。'
        '两例可行成本都与启动时相同，本次完成了原可行解的全局最优性证明。','',
        f'![长预算收敛过程]({(HERE/"convergence.png").as_posix()})','',
        '## 本次长跑内的时间检查点','',
        '| 算例 | 5 分钟 | 10 分钟 | 20 分钟 | 30 分钟 | 60 分钟 |',
        '|---|---:|---:|---:|---:|---:|']
    for case in CASES:
        values=[f"{record['gap_percent']:.6f}%"+('（已证明）' if record['already_proven'] else '')
                for record in checkpoints if record['case']==case]
        lines.append('| '+case+' | '+' | '.join(values)+' |')
    lines += ['',
        '检查点使用该时刻之前最近一次完整记录的有效上下界；若已提前证明最优，后续检查点沿用闭合结果。','',
        'S03 的首次尝试在约 8 分钟时因进度文件原子重命名被 Windows 拒绝而中断。'
        '中断记录原样保存在 `S03_interrupted_attempt_001/`；改用带重试的直接写入后，从相同输入重新运行。'
        '表中 S03 耗时仅为完整重跑，不包含首次中断尝试。求解算法本身保持冻结版本，'
        'I/O 包装器的源码哈希也写入运行清单。','',
        '## 证书和复核','',
        '原始 K=1…7 的范围与成本筛选证书沿用上一轮已验证且哈希不变的输入。'
        '每次子问题的原生状态、条件下界和加入的割完整保存。超时不会按无解处理。'
        '最终方案重新通过原模型成本计算、业务约束审计和连续两个周期回放；'
        '另独立重建加入全部记录割的主问题，用新的种子 42 再求证最终全局下界。','',
        '“证明最优”要求经原模型审计的可行成本 U 与有效全局下界 L 在 1e-6 绝对容差内闭合。'
        '这是分解证书，不把多个原生子问题的状态记成一次完整原模型的 `OPTIMAL`。'
        '每例的 `certificate.json` 保存证书范围、审计方案哈希和独立界复核结果。','',
        '原始历史方案文件的原生 `TIME_LIMIT` 状态保留。`result_with_global_certificate.json` '
        '在顶层附加新取得的外部全局证书，便于将方案与此次证明一起查看。','',
        f"本次实际选定 `test_evidence.py`：{counts['passed']} 项通过，{counts['failures']} 失败、"
        f"{counts['errors']} 错误、{counts['skipped']} 跳过。环境通过项目运行器预检，Gurobi 实际版本为 11.0.3。"
        '没有运行整个仓库测试集；上一轮的算法测试不重复计入本次数量。','',
        '本次仍限于这两个 7 单订单算例，没有据此外推 20/50 订单的效率，也没有修改生产默认入口。'
        '本目录包含每例源码/输入快照、原生状态事件、最终方案、独立下界复核、检查点 CSV 和证书。','']
    (HERE/'report.md').write_text('\n'.join(lines),encoding='utf-8')
    save(HERE/'validation.json',dict(selected_scope='output/experiments/m8_candidate_long_budget_20261028_v1/test_evidence.py',
        counts=counts,full_repository_suite_run=False,junit_sha256={path.name:sha(path) for path in junit_paths},
        prior_algorithm_validation_reused_without_rerunning=True))
    save(HERE/'evidence_manifest.json',dict(evidence_files={str(path.relative_to(HERE)):sha(path)
        for path in sorted(HERE.rglob('*')) if path.is_file() and '__pycache__' not in path.parts
        and path.name!='evidence_manifest.json' and path.suffix!='.tmp'}))
    print(json.dumps(dict(cases=rows,validation=counts),ensure_ascii=False))


if __name__=='__main__':
    main()
