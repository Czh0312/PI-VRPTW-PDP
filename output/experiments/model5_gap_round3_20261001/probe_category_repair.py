"""Repair delivery-batch prices and shared pickup prices using group bounds."""
from pathlib import Path
import json,math,sys
HERE=Path(__file__).resolve().parent;OLD=HERE.parent/'model5_gap_round2_20261001'

def read(p):return json.loads(Path(p).read_text())
def repair(w, batches):
    pi={tuple(k):v for k,v in w['duals']};nu=w['fleet_dual'];K=w['maximum_trips']
    groups={g['category']:g['lower_bound'] for g in w['pricing']['groups'] if not g['infeasible']}
    candidates={0.0}|{max(0,-r) for r in groups.values()}
    proposals=[]
    for u in candidates:
        shifts={b:max(0,-r-u) for b,r in groups.items()}
        updated={k:v-shifts.get(None if k[1]=='p' else batches[k[0]],0) for k,v in pi.items()}
        value=math.fsum(updated.values())+K*(nu-u)
        proposals.append(dict(bound=value,fleet_shift=u,category_shifts=[[b,v] for b,v in shifts.items()],
            repaired_duals=[[list(k),v] for k,v in updated.items()],repaired_fleet_dual=nu-u))
    return max(proposals,key=lambda r:r['bound'])

if __name__=='__main__':
    sys.path.insert(0,str(HERE/'source_snapshot'))
    from configuration.loader import load_routing_case
    from models.routing.common.cycle import load_instance
    p=load_instance(*load_routing_case(read(OLD/'100_full.json')['job']['case']))
    batches={o.id:o.batch for o in p.orders}
    rows=[]
    for path in sorted((HERE/'batch_cg_180s').glob('S*.json')):
        site=read(path)['site']
        best=max((dict(index=i,**repair(w,batches)) for i,w in enumerate(site['iterations'])
            if w['method']=='integer_pricing' and w['phase']=='distance'),key=lambda r:r['bound'])
        rows.append(dict(source=str(path),site=site['satellite'],best=best))
    for path in sorted((HERE/'tail_pricing_40s').glob('S*.json')):
        data=read(path);w={**data['source_witness'],'pricing':data['pricing']}
        rows.append(dict(source=str(path),site=data['job']['site'],best=repair(w,batches)))
    (HERE/'category_repair_probe.json').write_text(json.dumps(rows,indent=2,allow_nan=False)+'\n')
    for row in rows: print(dict(source=row['source'],site=row['site'],bound=row['best']['bound'],shifts=row['best']['category_shifts']),flush=True)
