"""Compare M1/M2 and M3/M4 using registered, matching, audited runs."""
from __future__ import annotations

import argparse
import csv
from datetime import datetime
import io
import json
import math
from pathlib import Path
import statistics
import subprocess
import sys
import time
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[2]
SIZES = {'small': '小型', 'medium': '中型', 'large': '大型'}
MODES = ('exact', 'alns_best', 'alns_mean')
LABELS = {'exact': '精确时限可行解', 'alns_best': 'ALNS三种子最好', 'alns_mean': 'ALNS三种子均值'}


def timestamp():
    return datetime.now(ZoneInfo('Asia/Shanghai')).isoformat(timespec='seconds')


def number(raw):
    if raw in ('', None):
        return None
    value = float(raw)
    if not math.isfinite(value):
        raise ValueError('Nonfinite source value')
    return value


def improvement(original, optimized):
    if original is None or optimized is None or original <= 0:
        return None, None
    delta = original-optimized
    if abs(delta) <= 1e-10*max(1, original, optimized):
        delta = 0.0
    return delta, delta/original


def atomic_text(path, text):
    temporary = path.with_suffix(path.suffix+'.tmp')
    temporary.write_text(text, encoding='utf-8')
    temporary.replace(path)


def save_csv(path, rows):
    stream = io.StringIO(newline='')
    writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
    writer.writeheader()
    writer.writerows(rows)
    atomic_text(path, '\ufeff'+stream.getvalue())


def build_data(manifest, source, state):
    indexed = {(row['case_id'], int(row['model'])):row for row in source}
    if len(indexed)!=len(source):
        raise ValueError('Duplicate case/model in comparison source')
    cases = sorted(manifest['selected_dataset_cases'], key=lambda r:(r['customer_count'],r['case_id']))
    details = []
    for record in cases:
        for mode in MODES:
            output = dict(case_id=record['case_id'], size=record['size'], customer_count=record['customer_count'],
                          mode=mode, mode_label=LABELS[mode])
            proofs = []
            for model in range(1,5):
                row = indexed.get((record['case_id'],model))
                value = None
                status = '未完成'
                proof = False
                if row:
                    if row.get('consistency_errors'):
                        status = '上下界矛盾，暂不统计'
                    elif mode=='exact':
                        if row['exact_status']=='PENDING':
                            status = '未完成'
                        elif row['exact_valid']=='True' and number(row['exact_objective']) is not None:
                            value = number(row['exact_objective'])
                            proof = row['exact_proven_optimal']=='True'
                            status = '已证最优' if proof else '可行未证最优'
                        else:
                            status = row['exact_status']+'，无审计可行解'
                    else:
                        completed, planned, valid = (int(row[key]) for key in ('alns_completed','alns_planned','alns_valid'))
                        if completed<planned:
                            status = f'未完成（{completed}/{planned}）'
                        elif valid!=planned:
                            status = f'可行种子不足（{valid}/{planned}），暂不统计'
                        else:
                            value = number(row[mode])
                            status = f'{valid}/{planned}种子均可行'
                output[f'M{model}'] = value
                output[f'M{model}_status'] = status
                proofs.append(proof)
            for first,second in ((1,2),(3,4)):
                delta, ratio = improvement(output[f'M{first}'],output[f'M{second}'])
                output[f'M{second}_vs_M{first}_distance_reduction'] = delta
                output[f'M{second}_vs_M{first}_improvement_fraction'] = ratio
            output['all_four_valid'] = int(all(output[f'M{m}'] is not None for m in range(1,5)))
            output['all_four_proven_optimal'] = int(mode=='exact' and all(proofs))
            details.append(output)
    summaries = []
    groups = [('customer_count',n,f'{n}顾客') for n in sorted({r['customer_count'] for r in cases})]
    groups += [('size',s,SIZES[s]) for s in SIZES]
    for group_key, group_value, group_label in groups:
        planned = sum(r[group_key]==group_value for r in cases)
        for mode in MODES:
            matched = [r for r in details if r[group_key]==group_value and r['mode']==mode and r['all_four_valid']]
            summary = dict(group_type=group_key, group_value=group_value, group_label=group_label,
                           mode=mode, mode_label=LABELS[mode], common_cases=len(matched), planned_cases=planned)
            for model in range(1,5):
                summary[f'M{model}_mean_distance'] = statistics.mean(r[f'M{model}'] for r in matched) if matched else None
            for first,second in ((1,2),(3,4)):
                summary[f'M{second}_vs_M{first}_mean_improvement_fraction'] = statistics.mean(
                    r[f'M{second}_vs_M{first}_improvement_fraction'] for r in matched) if matched else None
                delta, weighted = improvement(summary[f'M{first}_mean_distance'],summary[f'M{second}_mean_distance'])
                summary[f'M{second}_vs_M{first}_mean_distance_reduction'] = delta
                summary[f'M{second}_vs_M{first}_distance_weighted_improvement_fraction'] = weighted
            summary['all_four_proven_optimal_cases'] = sum(r['all_four_proven_optimal'] for r in matched)
            summaries.append(summary)
    return dict(generated_at=timestamp(), source_updated_at=state['updated_at'], source_status=state['status'],
                completed_runs=state['completed'], planned_runs=state['total'], seeds=manifest['arguments']['seeds'],
                definitions=dict(improvement='(original_distance-optimized_distance)/original_distance',
                                 primary_summary='unweighted mean of per-case improvement fractions; same four-model cohort',
                                 missing='unavailable; not zero', alns='all registered seeds completed and audited feasible'),
                summaries=summaries, details=details)


def fmt(value, percent=False):
    if value is None:
        return '—'
    return f'{100*value:.2f}%' if percent else f'{value:.3f}'


def markdown(data):
    lines = ['# M1–M4 模型横向对比', '',
             f"更新时间：{data['generated_at']}；实验状态：{data['source_status']}；进度：{data['completed_runs']}/{data['planned_runs']}。", '',
             '**距离减少率 =（原模型距离 − 自由分仓模型距离）÷ 原模型距离。正数表示改善，负数表示退步。**', '',
             'M2 对比 M1、M4 对比 M3。精确解、ALNS 三种子最好和三种子均值分别统计。',
             '汇总中的四个平均距离来自同一组四模型均有完整可行结果的案例；减少率为这些案例逐例减少率的算术平均。',
             '待运行、无可行解或 ALNS 种子尚未全部完成/可行均记为缺失，不填零。共同案例数量明确列出。',
             '精确超时可行解的差距仅表示本次解的比较；只有双方均证最优，才说明两模型最优目标的差距。',
             '自由分仓求解结果可能更差，按实际值保留负减少率；不预设 M2/M4 一定改善。', '',
             '## 分规模汇总', '',
             '| 规模 | 求解口径 | 共同完整案例/计划 | M1平均距离 | M2平均距离 | M2相对M1减少率 | M3平均距离 | M4平均距离 | M4相对M3减少率 | 四模型均证最优例数 |',
             '| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |']
    def summary_line(r):
        return (f"| {r['group_label']} | {r['mode_label']} | {r['common_cases']}/{r['planned_cases']} | "
                f"{fmt(r['M1_mean_distance'])} | {fmt(r['M2_mean_distance'])} | {fmt(r['M2_vs_M1_mean_improvement_fraction'],True)} | "
                f"{fmt(r['M3_mean_distance'])} | {fmt(r['M4_mean_distance'])} | {fmt(r['M4_vs_M3_mean_improvement_fraction'],True)} | "
                f"{r['all_four_proven_optimal_cases'] if r['mode']=='exact' else '—'} |")
    lines += [summary_line(r) for r in data['summaries'] if r['group_type']=='size']
    lines += ['', '## 分顾客数量汇总', '',
              '| 顾客数 | 求解口径 | 共同完整案例/计划 | M1平均距离 | M2平均距离 | M2相对M1减少率 | M3平均距离 | M4平均距离 | M4相对M3减少率 | 四模型均证最优例数 |',
              '| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |']
    lines += [summary_line(r) for r in data['summaries'] if r['group_type']=='customer_count']
    lines += ['', '## 逐案例四模型对照', '',
              '| 案例 | 求解口径 | M1距离 | M2距离 | M2减少量 | M2减少率 | M3距离 | M4距离 | M4减少量 | M4减少率 | 状态 |',
              '| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |']
    for r in data['details']:
        states = '；'.join(f"M{m}：{r[f'M{m}_status']}" for m in range(1,5))
        lines.append(f"| {r['case_id']} | {r['mode_label']} | {fmt(r['M1'])} | {fmt(r['M2'])} | "
                     f"{fmt(r['M2_vs_M1_distance_reduction'])} | {fmt(r['M2_vs_M1_improvement_fraction'],True)} | "
                     f"{fmt(r['M3'])} | {fmt(r['M4'])} | {fmt(r['M4_vs_M3_distance_reduction'])} | "
                     f"{fmt(r['M4_vs_M3_improvement_fraction'],True)} | {states} |")
    return '\n'.join(lines)+'\n'


def generate(out):
    manifest = json.loads((out/'manifest.json').read_text(encoding='utf-8'))
    source_payload = (out/'comparison.csv').read_text(encoding='utf-8-sig')
    state = json.loads((out/'progress.json').read_text(encoding='utf-8'))
    data = build_data(manifest,list(csv.DictReader(io.StringIO(source_payload))),state)
    save_csv(out/'model_comparison_cases.csv',data['details'])
    save_csv(out/'model_comparison_summary.csv',data['summaries'])
    atomic_text(out/'model_comparison_data.json',json.dumps(data,ensure_ascii=False,indent=2)+'\n')
    atomic_text(out/'model_comparison.md',markdown(data))
    return data


def export_workbook(args, data, out):
    if not args.node or not args.builder or not args.xlsx:
        return
    process = subprocess.run([args.node,args.builder,str(out/'model_comparison_data.json'),args.xlsx],
                             capture_output=True,text=True,encoding='utf-8',timeout=120)
    atomic_text(out/'model_comparison_workbook.log',process.stdout+'\n'+process.stderr)
    if process.returncode:
        raise RuntimeError('Workbook export failed; see model_comparison_workbook.log')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',required=True,type=Path)
    parser.add_argument('--watch',action='store_true')
    parser.add_argument('--interval',type=float,default=30)
    parser.add_argument('--xlsx-interval',type=float,default=300)
    parser.add_argument('--node')
    parser.add_argument('--builder')
    parser.add_argument('--xlsx')
    args = parser.parse_args()
    if args.interval<=0 or args.xlsx_interval<=0:
        parser.error('Refresh intervals must be positive')
    out = args.output.resolve()
    previous = None
    last_export = 0.0
    status_path = out/'model_comparison_refresh.json'
    while True:
        try:
            fingerprint = (out/'comparison.csv').stat().st_mtime_ns
            state = json.loads((out/'progress.json').read_text(encoding='utf-8'))
            if fingerprint!=previous or state['status']=='COMPLETED':
                data = generate(out)
                exported = False
                if not args.watch or time.monotonic()-last_export>=args.xlsx_interval or state['status']=='COMPLETED':
                    export_workbook(args,data,out)
                    last_export=time.monotonic()
                    exported=bool(args.xlsx)
                atomic_text(status_path,json.dumps(dict(status='COMPLETED' if state['status']=='COMPLETED' else 'WATCHING' if args.watch else 'SNAPSHOT',
                            updated_at=timestamp(),completed_runs=data['completed_runs'],xlsx_updated=exported,
                            xlsx_path=args.xlsx,error=None),ensure_ascii=False,indent=2)+'\n')
                previous=fingerprint
                print(f"Model comparison: {data['completed_runs']}/{data['planned_runs']}; xlsx={exported}",flush=True)
            if not args.watch or state['status']=='COMPLETED':
                break
        except Exception as exc:
            atomic_text(status_path,json.dumps(dict(status='REFRESH_ERROR',updated_at=timestamp(),error=str(exc)),ensure_ascii=False,indent=2)+'\n')
            if not args.watch:
                raise
            print(f'Refresh error: {exc}',file=sys.stderr,flush=True)
        time.sleep(args.interval)


if __name__=='__main__':
    main()
