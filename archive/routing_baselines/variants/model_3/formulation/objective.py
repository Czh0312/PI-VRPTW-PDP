# models/routing/variants/model_3/objective.py
# -*- coding: utf-8 -*-
import gurobipy as gp
from gurobipy import GRB


def set_objective(m: gp.Model, vars_dict: dict, ctx: dict):
    x = vars_dict["x"]
    z = vars_dict["z"]
    dist_map = ctx["dist_map"]
    n1e = ctx["N1E"]
    k_set = ctx["K"]

    obj_e1 = gp.quicksum(
        dist_map[i, j] * x[i, j, k]
        for k in k_set
        for i in n1e
        for j in n1e
        if i != j
    )
    obj_e2 = gp.quicksum(dist_map[i, j] * z[i, j, v] for (i, j, v) in z.keys())
    m.setObjective(obj_e1 + obj_e2, GRB.MINIMIZE)
