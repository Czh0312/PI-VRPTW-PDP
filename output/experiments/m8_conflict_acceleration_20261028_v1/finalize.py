"""Record complete paired evidence, certificates and actual validation counts."""
import csv
import json
from pathlib import Path
import statistics
import xml.etree.ElementTree as ET
import zipfile

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

from common import HERE,ROOT,OLD,read,save,sha


def spread(values):
    return dict(minimum=min(values),median=statistics.median(values),maximum=max(values))


def main():
    validation=[]
    for filename,expected in [('validation_sync.xml',12),('validation_evidence.xml',45)]:
        suites=list(ET.parse(HERE/filename).getroot().iter('testsuite'))
        count={key:sum(int(suite.attrib.get(key,0)) for suite in suites)
               for key in ['tests','failures','errors','skipped']}
        assert count==dict(tests=expected,failures=0,errors=0,skipped=0),count
        validation.append(dict(file=filename,passed=expected,failed=0,errors=0,skipped=0,sha256=sha(HERE/filename)))
    paths=sorted((HERE/'runs').rglob('summary.json'))
    trials=[read(path) for path in paths]
    assert len(trials)==26
    held=[row for row in trials if row['phase']=='confirmatory']
    assert len(held)==18
    groups=[]
    for case in ['S03','S08','S05']:
        for profile in ['baseline','sync']:
            rows=sorted([row for row in held if (row['case'],row['profile'])==(case,profile)],key=lambda r:r['seed'])
            assert [row['seed'] for row in rows]==[11,23,37]
            proven=[row for row in rows if row['global_optimality_certified']]
            groups.append(dict(case=case,profile=profile,proof_successes=len(proven),trials=3,
                algorithm_seconds=spread([row['algorithm_seconds'] for row in rows]),
                full_wall_seconds=spread([row['total_wall_seconds'] for row in rows]),
                proof_algorithm_seconds=spread([row['proof_seconds'] for row in proven]) if proven else None,
                final_gap=spread([row['gap_over_upper'] for row in rows]),
                patterns=sorted(set(row['patterns'] for row in rows))))
    save(HERE/'statistics.json',dict(trials=26,confirmatory_trials=18,seeds=[11,23,37],groups=groups,
        censored_timeouts='No finite proof time or exact speedup factor is assigned to a timed-out baseline run.'))
    with (HERE/'results.csv').open('w',encoding='utf-8-sig',newline='') as stream:
        fields=list(dict.fromkeys(key for row in trials for key in row))
        writer=csv.DictWriter(stream,fields)
        writer.writeheader()
        writer.writerows(trials)
    # Freeze source/input bytes once; no licence or global runtime files are copied.
    sources={}
    for path in paths:
        manifest=read(path.parent/'manifest.json')
        for category in ['driver_hashes','source_hashes']:
            for key,digest in manifest[category].items():
                target=Path(key) if Path(key).is_absolute() else ROOT/key
                assert sha(target)==digest
                sources[str(target.resolve())]=digest
        context=read(manifest['input_context'])
        sources.update(context['input_hashes'])
    for path in HERE.glob('*.py'):
        sources[str(path)]=sha(path)
    with zipfile.ZipFile(HERE/'frozen_sources_inputs.zip','w',compression=zipfile.ZIP_DEFLATED) as archive:
        for key,digest in sorted(sources.items()):
            target=Path(key)
            assert sha(target)==digest
            archive.write(target,str(target.relative_to(ROOT)))
    certificates={}
    for case in ['S03','S08','S05']:
        folder=HERE/'runs/confirmatory'/case/'sync/s23'
        row=read(folder/'summary.json')
        result=read(folder/'best_audited_result.json')
        certificate=dict(case=case,global_optimality_certified=row['global_optimality_certified'],
            original_K=row['original_K'],audited_upper_bound=row['audited_upper_bound'],
            global_lower_bound=row['global_lower_bound'],absolute_tolerance=1e-6,
            selected_K=result['cycle_batch_count'],container_pool=result['container_pool_size'],
            source_trial=str(folder.relative_to(HERE)),audited_plan_sha256=sha(folder/'best_audited_result.json'),
            independent_master_replay=read(folder/'independent_master_replay.json'),
            independent_input_only_bound=read(HERE/f'input_only_bound_{case}.json'),
            original_native_result_status=result['info']['status'],
            source_snapshot_sha256=sha(HERE/'frozen_sources_inputs.zip'),
            added_valid_conditions=['All pickups on one E2 trip share its unload time and first eligible recovery batch.',
                                    'A delivery-loaded E2 trip cannot recover before its delivery loading batch.'],
            proof_scope='Original all-K competitive domain preserved by existing U-based certificates; '
                        'new conditions are necessary consequences of the unchanged original model. '
                        'The optimum is not supplied as an objective floor.')
        result['external_global_optimality_certificate']=certificate
        save(HERE/f'certified_{case}.json',result)
        certificates[case]=certificate
    save(HERE/'certificates.json',certificates)
    save(HERE/'validation.json',dict(selected_scopes=['test_sync.py','test_evidence.py'],
        invocations=validation,distinct_passed=57,failed=0,errors=0,skipped=0,
        full_repository_suite_run=False,benchmark_trials=26,confirmatory_trials=18))
    colors=['#277db2','#e18c22','#279b74']
    fig,axes=plt.subplots(1,3,figsize=(11,3.9),layout='constrained')
    for ax,case in zip(axes,['S03','S08','S05']):
        for color,seed in zip(colors,[11,23,37]):
            for x,profile in enumerate(['baseline','sync']):
                row=next(r for r in held if (r['case'],r['profile'],r['seed'])==(case,profile,seed))
                y=row['proof_seconds'] if row['global_optimality_certified'] else row['seconds_budget']
                ax.scatter(x+(seed-23)/170,y,color=color,s=50,
                    marker='o' if row['global_optimality_certified'] else 'x',label=f'Seed {seed}' if x==1 else None)
        ax.set_xticks([0,1],['Previous','Sync links'])
        ax.set(title=case,ylabel='Algorithm time (s, log scale)')
        ax.set_yscale('log')
        ax.set_ylim(.1,300)
        ax.grid(axis='y',alpha=.25)
    axes[0].legend(frameon=False,fontsize=8)
    fig.suptitle('M8 matched 3-seed validation | x = no proof at budget | identical prior U/L')
    fig.savefig(HERE/'comparison.png',dpi=180)
    plt.close(fig)
    rejection=read(HERE/'historical_rejection_analysis.json')
    lines=['# 模型八：回收批次同步加强实验','',
        '新版在三个新种子（11、23、37）下，对 S03、S08、S05 均完成全域最优性证明。'
        '输入上下界、原始 K=1…7、完整子问题和 1e-6 绝对证明容差保持一致。','',
        '## 实际改动与原因','',
        '原完整模型已经规定，同一趟二级车的所有回收任务都在该趟结束时卸回仓库，'
        '并按第一个满足条件的截流时刻进入回收批次。原乐观主问题没有完整表达这层关系。'
        '新版仅对主问题增加两项有效必要条件：同趟带回的空箱必须同批回收；'
        '一趟车有配送任务时，其回收批次不能早于配送装载批次。原业务规则和最优解集合没有改变。','',
        f"旧长跑 S03 的 {rejection[0]['historical_unique_patterns']} 个不同完整组合中，"
        f"{rejection[0]['rejected_by_new_links']} 个违反新联系；S08 的 {rejection[1]['historical_unique_patterns']} 个中，"
        f"{rejection[1]['rejected_by_new_links']} 个违反。它们过去最终都被完整子问题确证在原成本上限下无解。"
        '现在这类组合可由主问题直接排除，不必再逐一创建、求解子模型。','',
        '本版实现的是这组主问题同步约束。没有额外引入 IIS 冲突提取或模型模板复用：'
        '新增联系已把本轮候选的子问题检验量降至 S03 的 2 个组合，以及 S08/S05 的 0 个组合，'
        '因而先验证这项最小改动的收益和正确性。','',
        '## 冻结后的新种子配对','',
        '种子 42 只用于探索；选定新约束后，冻结源码和执行顺序，再运行三个新种子。'
        '每次单线程，同时最多两个算例进程，前后顺序交替。S03/S08 每臂预算 180 秒，S05 为 60 秒，'
        '共 18 次配对运行；另有 8 次探索和历史长跑种子复现。','',
        '| 算例 | 方法 | 证明最优 | 算法时间范围 | 完整试验墙钟范围 | 检验组合数 |',
        '|---|---|---:|---:|---:|---:|']
    for group in groups:
        label='旧候选' if group['profile']=='baseline' else '同步加强'
        s=group['algorithm_seconds'];wall=group['full_wall_seconds']
        time_text=f"{s['minimum']:.3f}–{s['maximum']:.3f} 秒" if group['proof_successes'] else '180 秒内未证明'
        lines.append(f"| {group['case']} | {label} | {group['proof_successes']}/3 | {time_text} | "
            f"{wall['minimum']:.3f}–{wall['maximum']:.3f} 秒 | {', '.join(map(str,group['patterns']))} |")
    lines += ['',
        '超时视作未完成，不把 180 秒当成其真实最优证明时间，也不据此计算一个确定的证明加速倍数。'
        '墙钟包含单次输入加载、哈希、算法、最终审计等；不包含启动运行器和历史上下界、共享准备数据的生成。','',
        '**两组沿用同一组历史 U/L，没有将刚证明的最优值用作下界。**'
        '因此这是同信息条件下的证明阶段加速，不能当作从原始输入生成可行方案、预处理、证明全流程的总耗时。','',
        f'![配对结果]({(HERE/"comparison.png").as_posix()})','',
        '## 与此前长跑使用相同种子的复现','',
        '| 算例 | 种子 | 旧候选算法证明时间 | 同步加强算法证明时间 |',
        '|---|---:|---:|---:|']
    for case in ['S03','S08']:
        previous=read(ROOT/'output/experiments/m8_candidate_long_budget_20261028_v1'/case/'summary.json')
        current=next(row for row in trials if row['phase']=='matched_long_seed' and row['case']==case)
        lines.append(f"| {case} | {current['seed']} | {previous['algorithm_seconds']:.3f} 秒 | {current['algorithm_seconds']:.3f} 秒 |")
    lines += ['',
        '这张表是不同时间运行的历史复现，不能作为相同机器负载下的严格加速倍数。新种子配对表是本轮主要对照。','',
        '## 独立正确性复核','',
        '新增联系先通过 12 项选定验证：独立完整原模型的小算例最优计划投影、细化时钟、三个实际原方案，'
        '以及完整分解求解与独立最优值一致。最终证据测试另有 45 项通过，合计 57 项通过、0 失败、0 错误、0 跳过；'
        '没有运行整个仓库测试集。项目运行器预检 READY，实际 Gurobi 版本 11.0.3。','',
        '最终证据包括原始输入/源码哈希、18 次预声明配对、成本和两周期业务审计。'
        '每个新版试验还独立重建全部主问题约束和记录割：仅使用旧下界，不设置最优值下界或目标等式，'
        '并取消 eta 的上界，用新种子 101 再求全局界。所有不同的新子问题割还恢复原始未裁剪车次槽位重新求证。','',
        '额外重建原始全 K、完整车次库的输入型松弛，不固定 K/N、不提供历史 U/L。结果如下；'
        '时间只包含该松弛的原生优化。','',
        '| 算例 | 完整车次库列数 | 输入型全域下界 | 优化秒 | 与已审计方案成本闭合 |',
        '|---|---:|---:|---:|---|']
    for case in ['S03','S08','S05']:
        bound=read(HERE/f'input_only_bound_{case}.json')
        closed=abs(bound['lower_bound']-bound['audited_cost_used_only_for_postsolve_check'])<=1e-6
        lines.append(f"| {case} | {bound['full_route_columns']} | {bound['lower_bound']:.9f} | "
                     f"{bound['native_runtime']:.3f} | {'是' if closed else '否'} |")
    lines += ['',
        '原始数据直接给出的 S08/S05 下界与已审计可行成本一致，两者结合也形成不依赖历史下界或 U 缩域的最优证据。'
        '单独一个松弛下界并不提供原问题可行方案；上述检查仍没有计入从零寻找可行方案的时间。'
        '结果文件为 `input_only_bound_*.json`。','',
        '## 文件与复现','',
        '新版源代码：[master_sync.py]('+str((HERE/'master_sync.py').as_posix())+')；'
        '冻结算法调用：[decomposition_sync.py]('+str((HERE/'decomposition_sync.py').as_posix())+')。'
        '配对程序为 `run_confirmatory.py`；单例复现入口为 `run_sync.py`。例如：','',
        '```powershell',
        ".\\tools\\run.ps1 python -Arguments @('output/experiments/m8_conflict_acceleration_20261028_v1/run_sync.py', '--case', 'S03', '--seed', '11', '--seconds', '180', '--phase', 'manual')",
        '```','',
        '手动复现写入新目录，目录已存在时拒绝覆盖。`certified_S03.json`、`certified_S08.json`、`certified_S05.json` '
        '保留原方案并附加新全局证书；历史方案的原生状态保持原样。`certificates.json` 汇总范围与独立界复核。','',
        '生产默认入口尚未切换。这个复现入口使用本机已冻结的三个 7 单订单实例及历史证据；'
        '本轮没有验证 20/50 单，也不能仅凭三个种子给出所有实例尾部耗时的保证。','']
    (HERE/'report.md').write_text('\n'.join(lines),encoding='utf-8')
    save(HERE/'evidence_manifest.json',dict(evidence_files={str(path.relative_to(HERE)):sha(path)
        for path in sorted(HERE.rglob('*')) if path.is_file() and '__pycache__' not in path.parts
        and path.name!='evidence_manifest.json'}))
    print(json.dumps(dict(groups=groups,validation=validation),ensure_ascii=False))


if __name__=='__main__':
    main()
