"""Experiment-only union of individually valid, fingerprint-matched planes."""
from copy import deepcopy
import math


def certificate_sets(problem, left, right):
    from models.routing.solvers.exact.single_batch_columns import problem_fingerprint
    fingerprint = problem_fingerprint(problem)
    if problem.model_number not in (1, 2) or problem.reusable or len(problem.operating_policy.batches)!=1:
        raise ValueError('Diagnostic requires B1 M1/M2')
    for cert in (left, right):
        if (cert.get('proof_scope')!='B1_delivery_route_relaxation' or
                cert.get('problem_sha256')!=fingerprint or cert.get('time_cuts') or
                cert.get('proven_optimal') or cert.get('proven_infeasible')):
            raise ValueError('Certificate scope/fingerprint/proof mismatch')
        if not math.isfinite(cert['bound']) or cert['bound']<0:
            raise ValueError('Invalid certified scalar floor')
    floor = max(left['bound'],right['bound'])
    result = {}
    for name, source, cuts in (('baseline',left,left['dual_cuts']),
            ('bounded',right,right['dual_cuts']),
            ('union',left,left['dual_cuts']+right['dual_cuts'])):
        cert = deepcopy(source)
        cert.update(bound=floor,dual_cuts=deepcopy(cuts),
            status='FROZEN_CERTIFICATE_SET_DIAGNOSTIC',relaxation_complete=False,
            proven_optimal=False,proven_infeasible=False,
            diagnostic_scope='Equal valid scalar floor; only retained plane set varies',
            diagnostic_source_bounds=[left['bound'],right['bound']])
        result[name] = cert
    return result
