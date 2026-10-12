# models/routing/variants/model_1/objective.py
# -*- coding: utf-8 -*-
import gurobipy as gp
from gurobipy import GRB


def set_objective(m: gp.Model, problem, vars_dict: dict, ctx: dict):
    x = vars_dict["x"]
    z = vars_dict["z"]
    n1e = ctx["N1E"]
    k_set = ctx["K"]
    get_phys = ctx["get_phys"]

    obj_e1 = gp.quicksum(
        problem.distance(i, j) * x[i, j, k]
        for k in k_set
        for i in n1e
        for j in n1e
        if i != j
    )
    obj_e2 = gp.quicksum(
        problem.distance(get_phys(i), get_phys(j)) * z[i, j, v]
        for (i, j, v) in z.keys()
    )
    m.setObjective(obj_e1 + obj_e2, GRB.MINIMIZE)
