"""Optional compiled exhaustive oracle; compiler/executable are explicit inputs.

No compiler path or global runtime setting is installed or modified. The
binary protocol preserves all reference task/capacity/time checks. Callers
can use the Python Pareto-label implementation on unsupported hosts.
"""
from pathlib import Path
import json
import os
import shutil
import struct
import subprocess
import tempfile
import time


def build_native(compiler, executable):
    source = Path(__file__).with_suffix('.cs')
    executable = Path(executable).resolve()
    executable.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run([str(compiler), '/nologo', '/optimize+', '/platform:x64',
                             '/out:'+str(executable), str(source)], capture_output=True)
    if result.returncode:
        encoding = 'mbcs' if os.name == 'nt' else 'utf-8'
        raise RuntimeError((result.stdout+result.stderr).decode(encoding, errors='replace'))
    return executable


def enumerate_trip_library(problem, *, native_command, deadline=None, on_progress=None, stats=None,
                           include_paths=True, archive_directory=None):
    orders = list(problem.orders)
    n = len(orders)
    if n > 20:
        raise ValueError('Packed native route oracle supports at most twenty orders')
    policy, fleet = problem.operating_policy, problem.fleet
    begun = time.perf_counter()
    routes, completed = [], []
    total_prefixes = 0
    current_site = None
    native_seconds = 0.

    def report(complete=False, partial_columns=0, partial_prefixes=0):
        info = dict(prefixes=total_prefixes+partial_prefixes, columns=len(routes)+partial_columns,
            satellite=current_site, completed_satellites=list(completed), complete=complete,
            seconds=time.perf_counter()-begun, native_seconds=native_seconds,
            algorithm='compiled_exhaustive_reference', native_parallel=False)
        if stats is not None:
            stats.update(info)
        if on_progress is not None:
            on_progress(info)

    with tempfile.TemporaryDirectory(prefix='m8-trip-') as temporary:
        folder = Path(temporary)
        for site in problem.satellites:
            current_site = site
            if deadline is not None and time.perf_counter() >= deadline:
                report()
                raise TimeoutError('Incomplete trip enumeration; no route-master bound is valid')
            local = [i for i, o in enumerate(orders) if o.satellite == site]
            size = len(local)
            if not size:
                completed.append(site)
                continue
            os_ = [orders[i] for i in local]
            locations = [o.customer for o in os_]+[site]
            input_path, output_path, progress_path = [folder/name for name in ('input.bin', 'output.bin', 'progress.json')]
            with input_path.open('wb') as stream:
                stream.write(struct.pack('<4si4q2d', b'M8TI', size, policy.cycle_start, policy.cycle_end,
                    policy.e2_return_unload_time, policy.e2_turnaround_time,
                    fleet.e2_weight_cap, fleet.e2_volume_cap))
                for a in locations:
                    for b in locations:
                        stream.write(struct.pack('<qd', problem.travel(a, b), problem.distance(a, b)))
                for o in os_:
                    stream.write(struct.pack('<6q4d', *o.delivery_window, *o.pickup_window,
                        o.service_time, o.service_time+o.min_use_time,
                        problem.task(o.id, 'd')[1], problem.task(o.id, 'd')[2],
                        problem.task(o.id, 'p')[1], problem.task(o.id, 'p')[2]))
            remaining = deadline-time.perf_counter() if deadline is not None else 86400.
            if remaining <= 0:
                report()
                raise TimeoutError('Incomplete trip enumeration; no route-master bound is valid')
            process = subprocess.Popen([str(native_command), str(input_path), str(output_path),
                                        str(progress_path), format(remaining, '.17g')],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
            previous = None
            while process.poll() is None:
                try:
                    process.wait(timeout=.2)
                except subprocess.TimeoutExpired:
                    if deadline is not None and time.perf_counter() > deadline+2:
                        process.kill()
                        process.wait()
                        report()
                        raise TimeoutError('Incomplete trip enumeration; no route-master bound is valid')
                if progress_path.exists():
                    try:
                        content = progress_path.read_text(encoding='utf-8-sig')
                        if content != previous:
                            item = json.loads(content)
                            report(partial_columns=item['columns'], partial_prefixes=item['prefixes'])
                            previous = content
                    except (OSError, ValueError):
                        pass
            _, errors = process.communicate()
            if process.returncode:
                raise RuntimeError('Native trip oracle failed: '+errors.decode('utf-8', errors='replace')[:2000])
            if archive_directory is not None:
                archive = Path(archive_directory)
                archive.mkdir(parents=True, exist_ok=True)
                key = f'site_{len(completed):02d}'
                shutil.copyfile(input_path, archive / (key+'_input.bin'))
                shutil.copyfile(output_path, archive / (key+'_output.bin'))
                (archive / (key+'_orders.json')).write_text(json.dumps(dict(satellite=site, global_order_indices=local)), encoding='utf-8')
            with output_path.open('rb') as stream:
                header = struct.Struct('<4siqdi')
                magic, complete, prefixes, seconds, count = header.unpack(stream.read(header.size))
                assert magic == b'M8TO'
                native_seconds += seconds
                if not complete:
                    report(partial_columns=count, partial_prefixes=prefixes)
                    raise TimeoutError('Incomplete trip enumeration; no route-master bound is valid')
                total_prefixes += prefixes
                all_bits = (1 << size)-1
                lookup = [0]*(1 << size)
                for mask in range(1, len(lookup)):
                    bit = mask & -mask
                    lookup[mask] = lookup[mask ^ bit] | (1 << local[bit.bit_length()-1])
                row_format = struct.Struct('<qdi4q')
                data = stream.read()
                assert len(data) == count*row_format.size
                for mask, distance, depth, a, b, c, d in row_format.iter_unpack(data):
                    global_mask = lookup[mask & all_bits] | (lookup[mask >> size] << n)
                    row = dict(mask=global_mask, deliveries=lookup[mask & all_bits], satellite=site, distance=distance)
                    if include_paths:
                        packed = (a, b, c, d)
                        sequence = []
                        for j in range(depth):
                            task = ((packed[j // 10] >> (6*(j % 10))) & 63)-1
                            sequence.append((local[task % size], 'd' if task < size else 'p'))
                        row['path'] = sequence
                    routes.append(row)
            completed.append(site)
            report()
        report(complete=True)
        return sorted(routes, key=lambda r: r['mask']), total_prefixes
