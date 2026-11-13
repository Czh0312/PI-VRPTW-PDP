"""Two-cycle E4 timelines reconstructed from immutable independently audited events."""
from collections import defaultdict
from pathlib import Path

from experiments.analysis.analyze_thesis_stages import table
from experiments.runs.run_thesis_completion import read, sha, complete_attempt
from models.common.cycle.audit import replay_ledger


def two_cycle_events(audit, result):
    if (not audit.get('valid') or audit.get('errors') or
            not audit.get('periodic_replay', {}).get('valid') or
            audit['periodic_replay'].get('cycles') != 2):
        raise ValueError('An independently valid two-cycle audit is required')
    pool = result['container_pool_size']
    policy = result['operating_policy']
    start, end = policy['cycle_start'], policy['cycle_end']
    if end <= start or audit['container_balance']['pool'] != pool:
        raise ValueError('Invalid period or mismatched audited pool')
    ledger = audit['container_ledger']
    if not ledger or any(r['cycle'] != 1 or not start <= r['time'] <= end for r in ledger):
        raise ValueError('Expected one complete in-cycle ledger')
    satellites = [key[4:] for key in audit['terminal_state'] if key.startswith('sat:')]
    events = [{k: v for k, v in row.items() if k not in ('cycle', 'states')} for row in ledger]
    terminal, replayed, errors = replay_ledger(events, pool, satellites)
    if errors or replayed != ledger or terminal != audit['terminal_state']:
        raise ValueError('Saved one-cycle states do not reproduce from audited events')
    terminal, repeated, errors = replay_ledger(events, pool, satellites, 2, end-start)
    if errors or terminal != audit['periodic_replay']['terminal_state']:
        raise ValueError('Continuous two-cycle replay disagrees with audit')
    if terminal['clean'] != pool or any(v for k, v in terminal.items() if k != 'clean'):
        raise ValueError('Terminal pool did not return fully clean')
    return repeated


def select_witnesses(rows):
    cases = defaultdict(list)
    for row in rows:
        cases[row['reference_case']].append(row)
    return {case: min((r for r in members if r['valid']),
                     key=lambda r: (r['fixed_pool'], r['seed']), default=None)
            for case, members in sorted(cases.items())}


def timelines(study, rows):
    from experiments.analysis.analyze_thesis_mechanisms import plotting
    plt = plotting()
    study = Path(study)
    (study/'figures').mkdir(exist_ok=True, parents=True)
    selections = []
    for index, (case, row) in enumerate(select_witnesses(rows).items()):
        if row is None:
            selections.append(dict(case=case, witness=None, reason='No valid primary attempt'))
            continue
        attempt = row['_path']
        if not complete_attempt(attempt):
            raise ValueError('Incomplete timeline witness')
        result = read(attempt/'result.json')
        record = read(attempt/'audit.json')
        if record['result_sha256'] != sha(attempt/'result.json'):
            raise ValueError('Timeline result and audit hashes differ')
        if row['fixed_pool'] != result['container_pool_size']:
            raise ValueError('Timeline pool differs from selected registered point')
        events = two_cycle_events(record['audit'], result)
        selections.append(dict(case=case, witness=row['id'], pool=row['fixed_pool'],
            seed=row['seed'], method=row['method'], status=row['status'],
            result_sha256=record['result_sha256'], events=len(events), cycles=2))
        table(study/'tables'/f'pool_timeline_{index:02d}.csv',
              [dict(time=e['time'], cycle=e['cycle'], source=e.get('source'),
                    from_state=e['from'], to_state=e['to'], quantity=e['quantity'], **e['states']) for e in events])
        factor = result['time_tick_seconds']/(60*result.get('time_tick_subdivision', 1))
        policy = result['operating_policy']
        start, end = policy['cycle_start'], policy['cycle_end']
        period = end-start
        # Keep the final state at each simultaneous event time for the step plot.
        # The CSV retains every topologically ordered transfer at that time.
        points = {start: dict(clean=row['fixed_pool'], dirty=0)}
        for event in events:
            points[event['time']] = event['states']
        points[end+period] = events[-1]['states']
        times = sorted(points)
        fig, (ax, releases) = plt.subplots(2, 1, figsize=(10, 5), sharex=True,
                                         constrained_layout=True, height_ratios=(2, 1))
        for state in ('clean', 'dirty'):
            ax.step([t*factor for t in times], [points[t][state] for t in times],
                    where='post', label=state)
        ax.axhline(row['fixed_pool'], ls=':', lw=.7, color='gray', label='Pool N')
        for cycle in (0, 1):
            for release in record['audit']['depot_releases']:
                a, b = release['unload_complete']+cycle*period, release['clean_ready']+cycle*period
                releases.plot([a*factor, b*factor], [release['quantity']]*2, color='#c98536', lw=3)
                releases.scatter(b*factor, release['quantity'], color='#3c9977', s=22)
        for axis in (ax, releases):
            axis.axvline(end*factor, ls='--', color='gray', lw=.8)
            axis.set_xlim(start*factor, (end+period)*factor)
        ax.set(ylabel='Containers', title=f'{case} / N={row["fixed_pool"]} / seed={row["seed"]} / {row["method"]} / {row["status"]}')
        ax.legend(fontsize=8)
        releases.set(xlabel='Absolute time (minutes), two consecutive cycles',
                     ylabel='Cleaning lot size', title='Orange: cleaning interval; green dot: clean release')
        for suffix in ('png', 'pdf'):
            fig.savefig(study/'figures'/f'e4_timeline_{index:02d}.{suffix}', dpi=170)
        plt.close(fig)
    table(study/'tables/pool_timeline_selection.csv', selections)
