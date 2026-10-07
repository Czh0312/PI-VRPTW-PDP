"""Read worker heartbeats and update a human-readable local progress file."""
from pathlib import Path
from collections import Counter
from datetime import datetime, timezone, timedelta
import json

OUT=Path(__file__).resolve().parent


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


plan=read(OUT/'experiment.json')
rows=read(OUT/'rows.json') if (OUT/'rows.json').exists() else []
finished={row['id'] for row in rows}
active=[]
for path in OUT.glob('results/*/live_status.json'):
    try:
        state=read(path)
    except (FileNotFoundError,json.JSONDecodeError):
        continue
    if state['stage']=='solving' and state['job_id'] not in finished:
        active.append(state)
print('completed',len(rows),'/',len(plan['jobs']),'valid',sum(row['valid'] for row in rows))
print('active',[(r['job_id'],round(r['elapsed_seconds']),round(r.get('memory_MiB',0))) for r in active])
joint_rows=[row for row in rows if row['valid'] and row['model'] in (6,7,8)]
demands={case['family']:case['demand'] for case in plan['cases']}
print('joint actual-delivery counts',dict(Counter(row['delivery_batches'] for row in joint_rows)),
      'pool below Q',sum(row['pool']<demands[row['family']] for row in joint_rows),'/',len(joint_rows))
lines=['# 自动选择批次：六例五分钟实验进度','',
       '状态：'+('已完成' if len(rows)==len(plan['jobs']) else '运行中')+'。',
       f'更新时间：{datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M:%S")}。',
       f'已完成 {len(rows)}/{len(plan["jobs"])} 次；独立审计通过 {sum(row["valid"] for row in rows)} 次。',
       '每模型每例总预算300秒，M6–M8自行在K1/2/3中选最低成本，seed=42；一级1、二级0.056470588、箱池25、清洗0.2；工时及固定费为零，尚无最优证明。','',
       '| 算例 | M3 | M5 | M6 | M7 | M8 |','|---|---:|---:|---:|---:|---:|']
for case in plan['cases']:
    values=[]
    for model in (3,5,6,7,8):
        row=next((r for r in rows if r['family']==case['family'] and r['model']==model),None)
        values.append('待完成' if row is None else f'{row["cost"]:,.2f}' if row['valid'] else row['status'])
    lines.append(f'| {case["family"]} | '+' | '.join(values)+' |')
lines+=['','正在求解：','']
lines += [f'- {r["job_id"]}：已用 {r["elapsed_seconds"]:.0f} 秒。' for r in active]
lines += ['', '已完成方案的箱池和批次：', '',
          '| 算例 | 模型 | 箱池N | 时间表最低需箱 | 日历K / 实际配送批次 |',
          '|---|---|---:|---:|---|']
for row in sorted(rows, key=lambda item:item['id']):
    if row['valid']:
        lines.append(f'| {row["family"]} | M{row["model"]} | {row["pool"]} | {row["minimum_pool"]} | {row["k"]}/{row["delivery_batches"]} |')
(OUT/'progress.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
