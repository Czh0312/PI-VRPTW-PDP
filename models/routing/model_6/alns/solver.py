"""No-batch ALNS policy and independent validation of the selected plan."""
from itertools import chain
import math
import random
import time

from models.common.no_batch.audit import audit_solution
from models.common.no_batch.instance import VERSION
from models.routing.alns.engine import Evaluation, OperatorWeights, Proposal, run_search
from models.routing.alns.search_control import ALNSParameters, SearchControl

from .construction import initial_states
from .evaluator import evaluate, improve_pool
from .neighborhoods import DESTROY, REPAIR, destroy, repair, mutate


def solve_alns(problem, max_time=120, max_iterations=5000, seed=42, *, initial_result=None):
    if problem.model_number != 6:
        raise ValueError('M6 entry requires its own no-batch instance')
    if not math.isfinite(max_time) or max_time <= 0 or max_iterations < 0:
        raise ValueError('Invalid ALNS budget')
    begun = time.perf_counter()
    deadline = begun + max_time
    rng = random.Random(seed)
    parameters = ALNSParameters(max_time, max_iterations, seed, segment_period=40)
    control = SearchControl(parameters, begun, time.perf_counter)
    weights = OperatorWeights(
        control, DESTROY + REPAIR, segmented=True, keep_unused=True,
        accepted_reward=2.0, downhill_reward=2.0, credit_rejected_best=False,
    )
    current = best = best_state = None
    history = []
    evaluations = 0

    def key(value):
        tie = value['cost'] if problem.objective_mode == 'min_pool' else value['pool']
        return round(value['objective'], 10), round(tie, 10)

    def consider(state, value):
        nonlocal best, best_state
        if value and value['result']['feasible'] and (best is None or key(value) < key(best)):
            best, best_state = value, state.copy()
            history.append(dict(seconds=time.perf_counter() - begun,
                                pool=value['pool'], transport_cost=value['cost']))
            return True
        return False

    def score(value):
        penalty = max(0, value['pool'] - problem.pool_max) * max(100., value['cost'] * 10)
        if problem.transport_budget is not None:
            penalty += max(0, value['cost'] - problem.transport_budget) * 100
        objective = value['objective']
        if problem.objective_mode == 'min_pool':
            objective = objective * 10000 + value['cost']
        return objective + penalty

    def evaluate_state(state):
        nonlocal evaluations
        evaluations += 1
        return evaluate(problem, state) if state is not None else None

    seeds = []
    if initial_result is not None:
        from .warm_start import from_result

        seeds.append(from_result(problem, initial_result))
    for state in chain(seeds, initial_states(problem)):
        if time.perf_counter() >= deadline:
            break
        candidate = evaluate_state(state)
        if candidate:
            state, candidate = improve_pool(problem, state, candidate, deadline, attempts=20)
            consider(state, candidate)
            value = score(candidate)
            if current is None or value < current.score:
                current = Evaluation(value, state, candidate)

    def propose(trial, iteration):
        if iteration and iteration % 150 == 0 and best_state is not None:
            trial = Evaluation(score(best), best_state.copy(), best)
        if trial is None:
            # Randomized reconstruction can escape poor initial vehicle ordering.
            state = next(initial_states(problem))
            for routes in state.routes.values():
                rng.shuffle(routes)
            value = evaluate_state(state)
            if value is None:
                return Proposal(None)
            trial = Evaluation(score(value), state, value)
        operators = ()
        if rng.random() < .65:
            remove = weights.choose(rng, DESTROY)
            insert = weights.choose(rng, REPAIR)
            proposed, removed = destroy(problem, trial.state, trial.result, remove, rng)
            proposed = repair(problem, proposed, removed, insert, rng, deadline)
            operators = (remove, insert)
        else:
            proposed, _ = mutate(problem, trial.state, trial.result, rng)
        tested = evaluate_state(proposed)
        if not tested:
            return Proposal(trial, operators=operators)
        if rng.random() < .2:
            proposed, tested = improve_pool(problem, proposed, tested, deadline, attempts=8)
        return Proposal(trial, Evaluation(score(tested), proposed, tested), operators)

    def remember(proposal, _iterations):
        return consider(proposal.candidate.state, proposal.candidate.result)

    def accept(proposal, _iteration, _improved):
        score = proposal.current.score
        temperature = max(.001, max(1., score * .03) * (.01 ** control.time_fraction()))
        return control.accept(rng, score, proposal.candidate.score, temperature, joint=True)

    _, iterations, accepted = run_search(
        current, control, weights, propose, remember, accept, deadline=deadline,
    )
    info = dict(method='alns', status='FEASIBLE' if best else 'NO_SOLUTION_FOUND', seed=seed,
                runtime=time.perf_counter() - begun, iterations=iterations,
                evaluations=evaluations, accepted=accepted, operator_weights=weights.weights,
                incumbent_history=history, objective_mode=problem.objective_mode,
                pool_max=problem.pool_max, proven_optimal=False, proven_infeasible=False,
                restricted_trip_domain=problem.restricted_trip_domain)
    result = best['result'] if best else dict(schema_version=3, model=6, policy_version=VERSION,
                                             batch_mode='no_batch', e1_trips=[], e2_trips=[], feasible=False)
    result.update(solver='alns', model_module='models.routing.model_6.alns', solver_module=__name__, info=info)
    if best:
        audit = audit_solution(problem, result)
        if not audit['valid']:
            raise RuntimeError('M6 ALNS incumbent failed final independent audit')
        result.update(audit=audit, cost=audit['cost'], cost_components=audit['cost']['components'],
                      economic_profile_hash=problem.cost_profile.hash,
                      total_distance=sum(audit['distances'].values()))
        info.update(objective=audit['objective'], transport_cost=audit['transport_cost'])
    return result
