"""Collect same-call route proposals without promoting their pool bounds."""


def collect_pricing_columns(problem, reference, conditional=None, leaves=()):
    """Retain routes from weaker subset runs and conditional pricing as well.

Conditional routes are proposals for the physical pool, not global floors.
The pool independently recomputes all route clocks, loads and distances.
"""
    rows = list(reference.get('sites', []))
    rows += [r['certificate'] for r in reference.get('subset_runs', [])]
    rows += [r['certificate'] for r in (conditional or {}).get('conditions', [])]
    rows += [r['certificate'] for r in leaves]
    kept = {site: {} for site in problem.satellites}
    for row in rows:
        site = row.get('satellite')
        if site not in kept:
            continue
        for column in row.get('columns', []):
            key = tuple(tuple(k) for k in column['tasks'])
            kept[site].setdefault(key, column)
    return {site: list(columns.values()) for site, columns in kept.items()}


def near_target_primal_budget(upper, lower, *, target_gap, gap_limit,
                              time_limit, remaining):
    """Keep up to fifteen seconds for bound work and final audits."""
    gap = max(0.0, upper-lower)/max(1e-9, abs(upper))
    if lower <= 0 or gap <= target_gap or gap > gap_limit or remaining <= .05:
        return 0.0
    reserve = min(15.0, remaining*.1)
    return max(0.0, min(time_limit, remaining-reserve))
