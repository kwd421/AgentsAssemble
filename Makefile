.PHONY: help test test-module frontend-deps frontend-build codebase-map codebase-map-check

PYTHON ?= python3

help:
	@printf '%s\n' \
		'make test                 Run the full unittest suite' \
		'make test-module M=...    Run one unittest module' \
		'make frontend-deps        Install frontend dependencies' \
		'make frontend-build       Build the frontend' \
		'make codebase-map         Regenerate all three checked-in architecture maps' \
		'make codebase-map-check   Fail when any checked-in architecture map is stale'

# Run the full unittest suite (Python + node-backed UI smoke tests).
test:
	$(PYTHON) -m unittest discover -s tests -t .

# Run a single module, e.g. `make test-module M=tests.test_gui_server`.
test-module:
	$(PYTHON) -m unittest $(M)

frontend-deps:
	npm --prefix frontend ci

frontend-build:
	npm --prefix frontend run build

codebase-map:
	$(PYTHON) scripts/generate_package_map.py
	$(PYTHON) scripts/generate_codebase_map.py

codebase-map-check:
	@status=0; \
	$(PYTHON) scripts/generate_package_map.py --check || status=1; \
	$(PYTHON) scripts/generate_codebase_map.py --check || status=1; \
	exit $$status
