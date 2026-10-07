"""Read heartbeats and update the fixed-K progress table."""
import json
from pathlib import Path
from datetime import datetime
OUT=Path(__file__).resolve().parent
read=lambda p:json.loads(p.read_text(encoding='utf-8'))
plan=read(OUT/'experiment.json')
rows=read(OUT/'rows.json') if (OUT/'rows.json').exists() else []
done={r['id'] for r in rows}
active=[]
for f in OUT.glob('results/*/live_status.json'):
    try: v=read(f)
    except (FileNotFoundError,json.JSONDecodeError): continue
    if v['stage']=='solving' and v['job_id'] not in done: active.append(v)
print('completed',len(rows),'/9 valid',sum(r['valid'] for r in rows))
print('active',[(v['job_id'],round(v['elapsed_seconds']),round(v.get('memory_MiB',0))) for v in active])
print('results',[(r['id'],r.get('pool'),r.get('delivery_batches'),round(r['cost'],2) if r['cost'] else None) for r in rows])
lines=['# 箱价25、一级里程1固定K进度','',str(datetime.now()),'',f'已完成{len(rows)}/9；审计有效{sum(r["valid"] for r in rows)}。每个固定K独享300秒，seed42。','',
       '| 模型 | 固定日历K | 实际配送批次 | 箱池N | 总费用 |','|---|---:|---:|---:|---:|']
for job in sorted(plan['jobs'],key=lambda j:(j['model'],j['fixed_k'])):
    r=next((r for r in rows if r['id']==job['id']),None)
    cells=['待完成']*3 if r is None else [str(r.get('delivery_batches','—')),str(r.get('pool','—')),f'{r["cost"]:.2f}' if r['valid'] else r['status']]
    lines.append(f'| M{job["model"]} | {job["fixed_k"]} | '+ ' | '.join(cells)+' |')
(OUT/'progress.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
