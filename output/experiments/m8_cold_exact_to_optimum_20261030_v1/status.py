"""Render only this experiment's live records; never load historical solutions."""
import argparse
from datetime import datetime,timezone
import json
from pathlib import Path
import time

from protocol import HERE,read,save,sha


def fmt(value):
    return '—' if value is None else f'{value:.3f}'


def render():
    plan=read(HERE/'plan.json')
    batch=read(HERE/'batch_status.json') if (HERE/'batch_status.json').exists() else dict(stage='not_started')
    rows=[]
    for case in plan['cases']:
        folder=HERE/'runs'/case
        if (folder/'summary.json').exists():
            summary=read(folder/'summary.json')
            row=dict(case=case,stage='已证明最优' if summary['proven_optimal'] else summary['status'],
                first_integer_seconds=summary['first_native_integer_solution_seconds'],
                proof_seconds=summary['total_cold_solve_seconds'] if summary['proven_optimal'] else None,
                elapsed_seconds=summary['total_cold_solve_seconds'],objective=summary['objective'],
                bound=summary['best_bound'],native_gap_percent=None if summary['native_mip_gap'] is None else 100*summary['native_mip_gap'],
                proven_optimal=summary['proven_optimal'],valid=summary['valid'])
        elif (folder/'live.json').exists():
            live=read(folder/'live.json')
            row=dict(case=case,stage={'loading':'加载输入','building':'建模中','optimizing':'原生求解中'}.get(live['stage'],live['stage']),
                first_integer_seconds=live.get('first_integer_solution_seconds'),proof_seconds=None,
                elapsed_seconds=live.get('elapsed_seconds'),objective=live.get('native_incumbent'),
                bound=live.get('best_bound'),native_gap_percent=live.get('native_gap_percent'),
                proven_optimal=False,valid=None,live_updated_utc=live.get('updated_utc'))
        else:
            row=dict(case=case,stage='未开始',first_integer_seconds=None,proof_seconds=None,elapsed_seconds=None,
                objective=None,bound=None,native_gap_percent=None,proven_optimal=False,valid=None)
        rows.append(row)
    completed=sum(row['proven_optimal'] for row in rows)
    output=dict(updated_utc=datetime.now(timezone.utc).isoformat(),batch=batch,completed_optima=completed,total=9,rows=rows)
    save(HERE/'status.json',output)
    lines=['# 模型八：九例冷启动精确求解至最优', '',
        f'已完成 **{completed}/9** 个算例的原生最优性证明。当前批次状态：`{batch["stage"]}`。', '',
        '仅一个实验根种子42、单线程、串行，从原始输入开始；不读取历史可行方案、参考最优值或历史下界。'
        '用户已明确要求持续直到证明最优，不设置实际时间上限。每个算例只进行一次持续的原生统一MILP求解，'
        '不以达到此前见过的费用作为停止条件。', '',
        '| 算例 | 当前状态 | 首次原生整数解 / 秒 | 证明最优总耗时 / 秒 | 最近记录耗时 / 秒 | 当前原生费用 | 原生下界 | 原生 GAP |',
        '| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |']
    for row in rows:
        gap='—' if row['native_gap_percent'] is None else f"{row['native_gap_percent']:.4f}%"
        lines.append('| '+' | '.join([row['case'],row['stage'],fmt(row['first_integer_seconds']),fmt(row['proof_seconds']),
            fmt(row['elapsed_seconds']),fmt(row['objective']),fmt(row['bound']),gap])+' |')
    lines.extend(['', '只有原生OPTIMAL、完整原始K域、最终业务审计和两周期审计均通过，才标为“已证明最优”。'
        '进行中的整数解及下界仅为Gurobi原生记录，最终独立审计尚未执行；不将其当成已审计方案。', '',
        '“证明最优总耗时”包含原始输入加载、预检查、完整建模、优化、方案提取和内部审计；'
        '进程/模块初始化及额外最终独立审计另列在各summary.json。'
        '首次整数解为Gurobi回调记录，并非首次独立业务审计时刻。', '',
        '当前配置：紧凑表达、physical_time必要物理条件、时间域预处理、车辆对称性处理；'
        '完整原始K=1…7与时间精度保留。进入优化时检查NumStart=0、单线程、种子42、MIPGap=0、MIPGapAbs=0。'
        '回调只记录进度/日志，不提交解、不加割、不使用目标费用或外部证书。', '',
        f"源文件{len(plan['source_hashes'])}个、原始输入{len(plan['input_hashes'])}个及驱动SHA256均已冻结。"
        '原生产算法未修改。3项协议检查最终通过，0失败/错误/跳过。'
        '准备检查曾发现测试临时目录的中文日志路径、原生无限时限的JSON表示问题，均已在任何正式算例运行前修正；'
        '前两轮失败XML另存为protocol_checks_initial.xml和protocol_checks_second.xml。', '',
        '本文件在求解期间是进度报告，**不能当作全部九例的最终求解时间表**。'
        f'最近更新UTC：{output["updated_utc"]}。', '',
        '[当前状态JSON](status.json) · [原始计划及哈希](plan.json) · [逐例原生日志和结果](runs)', ''])
    (HERE/'report.md').write_text('\n'.join(lines),encoding='utf-8')
    return output


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['once','watch'],default='once',nargs='?')
    args=parser.parse_args()
    while True:
        output=render()
        print(json.dumps(dict(completed=output['completed_optima'],stage=output['batch']['stage'],
            current=output['batch'].get('current')),ensure_ascii=False),flush=True)
        if args.action=='once' or output['batch']['stage'] in ('complete','needs_attention'):
            break
        time.sleep(15)
