PYTHON ?= .venv/bin/python
PYTEST_ARGS ?=

# Daily smoke checks. Override TESTS to run the files affected by a change.
TESTS ?= tests/routing/test_numbered_model_entries.py \
	tests/routing/test_model_scope.py \
	tests/routing/test_solver_isolation.py \
	tests/routing/test_multibatch_cycle.py \
	tests/routing/test_batch_count_domain.py \
	tests/routing/test_model78_calendar.py::test_public_entry_identity_dispatch_and_cost \
	tests/cost/test_lifecycle.py \
	tests/data/test_real_case.py

.PHONY: test test-full

test:
	$(PYTHON) -m pytest -q $(TESTS) $(PYTEST_ARGS)

test-full:
	$(PYTHON) -m pytest -q $(PYTEST_ARGS)
