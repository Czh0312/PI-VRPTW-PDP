import pytest

from experiments.analysis.thesis_pool_timeline import two_cycle_events, select_witnesses
from models.common.cycle.audit import replay_ledger


def evidence():
    events = [dict(time=0, **{'from': 'clean', 'to': 'dirty'}, quantity=2),
              dict(time=10, **{'from': 'dirty', 'to': 'clean'}, quantity=2)]
    terminal, ledger, errors = replay_ledger(events, 2, [])
    assert not errors
    audit = dict(valid=True, errors=[], container_balance={'pool': 2}, container_ledger=ledger,
                 terminal_state=terminal, periodic_replay=dict(valid=True, cycles=2, terminal_state=terminal))
    result = dict(container_pool_size=2, operating_policy=dict(cycle_start=0, cycle_end=10))
    return audit, result


def test_boundary_clean_release_precedes_second_cycle_issue():
    audit, result = evidence()
    rows = two_cycle_events(audit, result)
    boundary = [r for r in rows if r['time'] == 10]
    assert [r['to'] for r in boundary] == ['clean', 'dirty']
    assert boundary[-1]['states']['clean'] == 0
    assert rows[-1]['states']['clean'] == 2


def test_timeline_rejects_tampered_states_and_unverified_cycle():
    audit, result = evidence()
    audit['container_ledger'][0]['states']['clean'] = 1
    with pytest.raises(ValueError, match='states do not reproduce'):
        two_cycle_events(audit, result)
    audit, result = evidence()
    audit['periodic_replay']['valid'] = False
    with pytest.raises(ValueError, match='independently valid'):
        two_cycle_events(audit, result)


def test_witness_selection_uses_lowest_pool_then_seed_not_best_cost():
    rows = [dict(reference_case='x', valid=True, fixed_pool=n, seed=s, objective=c)
            for n, s, c in ((3, 42, 5), (2, 43, 10), (2, 42, 20))]
    rows += [dict(reference_case='x', valid=False, fixed_pool=1, seed=42, objective=None),
             dict(reference_case='y', valid=False, fixed_pool=1, seed=42, objective=None)]
    selected = select_witnesses(rows)
    assert selected['x']['objective'] == 20
    assert selected['y'] is None
