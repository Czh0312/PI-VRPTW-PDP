"""Explain candidate fallbacks on a frozen sequence; no search tuning."""
from collections import Counter, OrderedDict
import sys
from run import HERE, install, load, read, save

variant, case = sys.argv[1:]
load_instance, audit_solution, solve_alns, ev, load_routing_case = install(variant)
p = load(case, load_instance, load_routing_case)
original = ev.lift_result
failed = Counter()
def observe(*args, **kwargs):
    result = original(*args, **kwargs)
    if not result['audit']['valid']:
        failed.update(e['code'] for e in result['audit']['errors'])
    return result
ev.lift_result = observe
from models.common.cycle import audit
calls = [0]
original_audit = audit._audit
def count(*args, **kwargs):
    calls[0] += 1
    return original_audit(*args, **kwargs)
audit._audit = count
contexts = OrderedDict()
totals = Counter()
for record in read(HERE / 'corpus' / f'{case}.json'):
    for route in record['state']['e2']:
        route['tasks'] = [tuple(t) for t in route['tasks']]
    key = record['K'], tuple(record['boundaries'])
    if key not in contexts:
        contexts[key] = ev.JointEvaluator(p.fixed_calendar(*key))
        if len(contexts) > 24:
            contexts.popitem(last=False)
    contexts.move_to_end(key)
    evaluator = contexts[key]
    before = [getattr(evaluator, name, 0) for name in ['hits', 'misses', 'single_audit_accepts', 'reference_fallbacks']]
    result = evaluator(record['assignment'], record['state'])
    for name, old in zip(['hits', 'misses', 'single_audit_accepts', 'reference_fallbacks'], before):
        totals[name] += getattr(evaluator, name, 0) - old
    totals['feasible_calls'] += int(result[1] is not None)
row = dict(variant=variant, case=case, totals=dict(totals), physical_audit_calls=calls[0], failed_lift_codes=dict(failed))
save(HERE / 'inspection' / f'{case}_{variant}.json', row)
print(row)
