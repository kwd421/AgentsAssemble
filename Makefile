.DEFAULT_GOAL := test

.PHONY: help test test-module frontend-deps frontend-build
.PHONY: codebase-map codebase-map-check codebase-map-commit-check codebase-map-verify

PYTHON ?= python3
CODEBASE_MAP_OUTPUTS := \
	docs/product/PACKAGE_MAP.md \
	docs/product/CODEBASE_MAP.json \
	docs/product/CODEBASE_MAP.html

help:
	@printf '%s\n' \
		'make test                 Refresh maps, run the full suite, require committed outputs' \
		'make test-module M=...    Refresh maps, run one module, require committed outputs' \
		'make frontend-deps        Install frontend dependencies' \
		'make frontend-build       Build the frontend' \
		'make codebase-map         Regenerate all three checked-in architecture maps' \
		'make codebase-map-check   Check maps without changing the working tree' \
		'make codebase-map-verify  Regenerate maps and fail if they differ from HEAD'

# Refresh maps, run the full suite, then reject uncommitted generated changes.
test: codebase-map
	$(PYTHON) -m unittest discover -s tests -t .
	$(MAKE) codebase-map-commit-check

# Run one module with the same generated-map contract as the full suite.
test-module: codebase-map
	$(PYTHON) -m unittest $(M)
	$(MAKE) codebase-map-commit-check

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

codebase-map-commit-check:
	@if ! git diff --quiet --no-ext-diff -- $(CODEBASE_MAP_OUTPUTS) \
		|| ! git diff --cached --quiet --no-ext-diff HEAD -- $(CODEBASE_MAP_OUTPUTS); then \
		printf '%s\n' \
			'Generated codebase maps differ from HEAD.' \
			'Review and commit the generated files before treating verification as complete.'; \
		git --no-pager diff --stat --no-ext-diff -- $(CODEBASE_MAP_OUTPUTS); \
		git --no-pager diff --cached --stat --no-ext-diff HEAD -- $(CODEBASE_MAP_OUTPUTS); \
		exit 1; \
	fi

codebase-map-verify: codebase-map
	$(MAKE) codebase-map-commit-check
