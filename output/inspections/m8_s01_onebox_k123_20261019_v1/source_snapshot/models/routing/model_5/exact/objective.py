"""Distance and cost objective definitions for M5."""
from __future__ import annotations


def add_distance_cost(expression, problem, left, right, arc):
    """Count a selected physical arc once in the distance objective."""
    expression += problem.distance(left, right) * arc
    return expression


def add_distance_term(expression, term):
    expression += term
    return expression


def set_distance_objective(model, expression):
    from gurobipy import GRB

    model.setObjective(expression, GRB.MINIMIZE)
