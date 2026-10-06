"""Explicit label migration for the former M3 multi-batch cycle outputs.

This never accepts the retired transshipment M5 or a single-batch schedule.
"""
from copy import deepcopy

from .audit import audit_solution
from .policy import VERSION
from .scope import finish_result


def migrate_former_m3_result(problem, result):
    if (problem.model_number != 5 or len(problem.operating_policy.batches) <= 1
            or result.get('model') not in (None, 3)
            or result.get('schema_version') != 2 or result.get('policy_version') != VERSION
            or len(result.get('batches', [])) <= 1):
        raise ValueError('Migration requires a former M3 schema-2 multi-batch cycle and a current M5 instance')
    migrated = deepcopy(result)
    migrated['model'] = 5
    migrated['label_migration'] = dict(from_model=result.get('model', 3), to_model=5,
        reason='Current fixed multi-batch cycle is M5; routing rules and schedule unchanged')
    migrated.pop('audit', None)
    migrated['audit'] = audit_solution(problem, migrated)
    if not migrated['audit']['valid']:
        raise ValueError(f'Migrated schedule failed independent audit: {migrated["audit"]["errors"]}')
    return finish_result(problem, migrated)
