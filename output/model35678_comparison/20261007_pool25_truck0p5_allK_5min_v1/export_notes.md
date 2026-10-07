# Repository export

The original local experiment and its frozen hash manifest are retained unchanged.
The local verification checked 312 source files and 236 input files.

The repository exports 311 frozen source files. The remaining file,
`source_snapshot/tools/project_runtime.py`, is a machine-local environment
preflight helper and stays local under the project's runtime policy. It is not
imported by the frozen solver. Its original hash remains in `experiment.json`
so the local archive still records the exact source snapshot taken at run time.

An exported checkout therefore does not contain every file required by the
current strict archive hash verifier. Reproduction on another machine also
requires localizing the archived absolute input paths and selecting a compatible
Python runtime. No solver, input, route, cost, audit or search result has been
removed or changed for this export.
