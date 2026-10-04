"""Diagnosis of prior baseline incumbents, before this candidate's registration.

These geometry/capacity counts omit optimistic windows and do not measure the
new candidate's performance. Run separately from any quality timing.
"""
import itertools, json, sys, time
from pathlib import Path

out = Path(__file__).resolve().parent
sys.path.insert(0,str(out.parents[2]))
from configuration.loader import load_routing_case
from models.routing.common.cycle import load_instance

rows = []
for old, indices in [('alns_single_batch_route_group_20261001',[0,3,6]),
                     ('alns_single_batch_improving_groups_20261001',[0,3,6])]:
    root = out.parent/old
    jobs = json.loads((root/'quality_manifest.json').read_text())['jobs']
    for index in indices:
        job = jobs[index]
        config, fleet = load_routing_case(job['case'])
        p = load_instance({**config,'model':2},fleet)
        best = json.loads((root/'quality'/('%03d_before_source.json'%index)).read_text())['result']
        trips = [(r['satellite_id'],[p.order_map[e['order_id']] for e in r['events']]) for r in best['e2_trips']]
        def cost(site, sequence):
            if not sequence:
                return 0
            nodes = [site]+[o.customer for o in sequence]+[site]
            return sum(p.distance(a,b) for a,b in zip(nodes,nodes[1:]))
        def capacity(sequence):
            return (sum(p.task(o.id,'d')[1] for o in sequence) <= p.fleet.e2_weight_cap and
                    sum(p.task(o.id,'d')[2] for o in sequence) <= p.fleet.e2_volume_cap)
        transfers = exchanges = 0
        lowest = 0
        started = time.perf_counter()
        for (site_a,a),(site_b,b) in itertools.combinations(trips,2):
            if site_a == site_b:
                continue
            original = cost(site_a,a)+cost(site_b,b)
            for cut_a in range(len(a)+1):
                for cut_b in range(len(b)+1):
                    if len(a[cut_a:])+len(b[cut_b:]) < 2:
                        continue
                    x,y = a[:cut_a]+b[cut_b:],b[:cut_b]+a[cut_a:]
                    if not capacity(x) or not capacity(y):
                        continue
                    delta = cost(site_a,x)+cost(site_b,y)-original
                    if delta < -1e-9:
                        if not a[cut_a:] or not b[cut_b:]:
                            transfers += 1
                        else:
                            exchanges += 1
                        lowest = min(lowest,delta)
        rows.append(dict(source=old,family=job['family'],routes=len(trips),
                         improving_tail_transfers=transfers,improving_tail_exchanges=exchanges,
                         lowest_E2_distance_delta=lowest,scan_seconds=time.perf_counter()-started))
# Preserve the actual original diagnostic output when re-running this script.
(out/'exploratory_opportunities_rerun.json').write_text(json.dumps(rows,indent=2))
