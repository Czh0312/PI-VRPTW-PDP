"""Independent vectorized replay of every archived minimum-distance trip."""
import json
from pathlib import Path
import struct
import time

import numpy as np

from study import HERE, load, read, save


def audit_all():
    begun = time.perf_counter()
    p = load()
    count = prefixes = 0
    sites = []
    dtype = np.dtype([('mask', '<i8'), ('distance', '<f8'), ('depth', '<i4'), ('pack', '<i8', (4,))])
    assert dtype.itemsize == 52
    for metadata in sorted((HERE / 'native_routes').glob('*_orders.json')):
        info = read(metadata)
        local, site = info['global_order_indices'], info['satellite']
        orders = [p.orders[i] for i in local]
        m = len(orders)
        nodes = [o.customer for o in orders]+[site]
        travel = np.array([[p.travel(a, b) for b in nodes] for a in nodes], dtype=np.int64)
        distances = np.array([[p.distance(a, b) for b in nodes] for a in nodes])
        dlo, dhi, plo, phi, svc, use = [np.array(values) for values in (
            [o.delivery_window[0] for o in orders], [o.delivery_window[1] for o in orders],
            [o.pickup_window[0] for o in orders], [o.pickup_window[1] for o in orders],
            [o.service_time for o in orders], [o.service_time+o.min_use_time for o in orders])]
        dw, dv, pw, pv = [np.array([p.task(o.id, kind)[k] for o in orders]) for kind, k in [('d',1),('d',2),('p',1),('p',2)]]
        output = metadata.with_name(metadata.name.replace('_orders.json', '_output.bin'))
        with output.open('rb') as stream:
            magic, complete, visited, seconds, expected = struct.unpack('<4siqdi', stream.read(28))
            assert magic == b'M8TO' and complete == 1
            prefixes += visited
            done = 0
            while True:
                rows = np.fromfile(stream, dtype=dtype, count=65536)
                if not len(rows):
                    break
                size = len(rows)
                index = np.arange(size)
                assert np.all((rows['depth'] >= 1) & (rows['depth'] <= 2*m))
                preload = rows['mask'] & ((1 << m)-1)
                weight, volume = np.zeros(size), np.zeros(size)
                for i in range(m):
                    yes = (preload & (1 << i)) != 0
                    weight += np.where(yes, dw[i], 0.)
                    volume += np.where(yes, dv[i], 0.)
                assert np.all(weight <= p.fleet.e2_weight_cap+1e-8)
                assert np.all(volume <= p.fleet.e2_volume_cap+1e-8)
                clock = np.full(size, p.operating_policy.cycle_start, dtype=np.int64)
                at = np.full(size, m, dtype=np.int64)
                length = np.zeros(size)
                visited_mask = np.zeros(size, dtype=np.int64)
                delivered_at = np.full((size, m), -1, dtype=np.int64)
                for position in range(int(rows['depth'].max())):
                    active = rows['depth'] > position
                    task = ((rows['pack'][:, position // 10] >> (6*(position % 10))) & 63)-1
                    assert np.all((task[active] >= 0) & (task[active] < 2*m))
                    task = np.where(active, task, 0)
                    customer = task % m
                    delivery = active & (task < m)
                    pickup = active & ~delivery
                    bit = np.left_shift(np.int64(1), task)
                    assert np.all((visited_mask[active] & bit[active]) == 0)
                    chosen = (preload & np.left_shift(np.int64(1), customer)) != 0
                    actual_delivered = delivered_at[index, customer]
                    assert np.all(actual_delivered[pickup & chosen] >= 0)
                    ready = np.maximum(plo[customer], np.where(chosen, actual_delivered, dlo[customer])+use[customer])
                    arrival = np.maximum(clock+travel[at, customer], np.where(delivery, dlo[customer], ready))
                    assert np.all(arrival[active] <= np.where(delivery, dhi[customer], phi[customer])[active])
                    delivered_at[index[delivery], customer[delivery]] = arrival[delivery]
                    weight += np.where(delivery, -dw[customer], np.where(pickup, pw[customer], 0.))
                    volume += np.where(delivery, -dv[customer], np.where(pickup, pv[customer], 0.))
                    assert np.all((weight >= -1e-8) & (weight <= p.fleet.e2_weight_cap+1e-8))
                    assert np.all((volume >= -1e-8) & (volume <= p.fleet.e2_volume_cap+1e-8))
                    length += np.where(active, distances[at, customer], 0.)
                    clock = np.where(active, arrival+svc[customer], clock)
                    at = np.where(active, customer, at)
                    visited_mask |= np.where(active, bit, 0)
                assert np.array_equal(visited_mask, rows['mask'])
                end = clock+travel[at, m]+p.operating_policy.e2_return_unload_time+p.operating_policy.e2_turnaround_time
                assert np.all(end <= p.operating_policy.cycle_end)
                assert np.allclose(length+distances[at, m], rows['distance'], atol=1e-8, rtol=1e-12)
                done += size
            assert done == expected
            count += done
            sites.append(dict(satellite=site, routes=done, prefixes=visited, native_seconds=seconds))
    return dict(valid=True, route_columns=count, exhaustive_prefixes=prefixes, sites=sites,
                independent_wall_seconds=time.perf_counter()-begun,
                scope='Every archived representative: task uniqueness, initial/full weight and volume, windows, same-trip use-time precedence, return feasibility and distance')


if __name__ == '__main__':
    result = audit_all()
    save(HERE / 'independent_route_audit.json', result)
    print(json.dumps(result), flush=True)
