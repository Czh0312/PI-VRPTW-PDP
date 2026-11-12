# -*- coding: utf-8 -*-
"""Load labelled square distance/time matrices used by real-road cases."""
from __future__ import annotations

import csv
from pathlib import Path
from typing import Dict


Matrix = Dict[str, Dict[str, float]]


def load_square_matrix(path: str | Path) -> Matrix:
    matrix_path = Path(path)
    with matrix_path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.reader(handle)
        header = next(reader, None)
        if not header or len(header) < 2 or header[0] != "node_id":
            raise ValueError(f"Invalid matrix header in {matrix_path}")
        columns = [value.strip() for value in header[1:]]
        matrix: Matrix = {}
        for row in reader:
            if not row:
                continue
            node_id = row[0].strip()
            if len(row) != len(columns) + 1:
                raise ValueError(f"Matrix row {node_id!r} has wrong length in {matrix_path}")
            matrix[node_id] = {
                column: float(value) for column, value in zip(columns, row[1:])
            }
    if set(matrix) != set(columns):
        raise ValueError(f"Matrix row/column labels differ in {matrix_path}")
    return matrix
