.DEFAULT_GOAL := test

.PHONY: help test test-module frontend-deps frontend-build
.PHONY: codebase-map codebase-map-check codebase-map-commit-check codebase-map-verify
.PHONY: room-event-types room-event-types-check room-event-types-commit-check room-event-types-verify
.PHONY: generated-artifacts generated-artifacts-check generated-artifacts-commit-check generated-artifacts-verify

PYTHON ?= python3
CODEBASE_MAP_OUTPUTS := \
	docs/product/PACKAGE_MAP.md \
	docs/product/CODEBASE_MAP.json \
	docs/product/CODEBASE_MAP.html
ROOM_EVENT_TYPE_OUTPUT := frontend/src/types/generatedRoomEvent.ts

help:
	@printf '%s\n' \
		'make test                 Refresh generated artifacts, run the full suite, require committed outputs' \
		'make test-module M=...    Refresh generated artifacts, run one module, require committed outputs' \
		'make frontend-deps        Install frontend dependencies' \
		'make frontend-build       Build the frontend' \
		'make codebase-map         Regenerate all three checked-in architecture maps' \
		'make codebase-map-check   Check maps without changing the working tree' \
		'make codebase-map-verify  Regenerate maps and fail if they differ from HEAD' \
		'make room-event-types     Regenerate the checked frontend room-event contract' \
		'make room-event-types-check  Check the room-event contract without changing the working tree' \
		'make generated-artifacts-verify  Regenerate and verify every checked-in generated artifact'

# Refresh generated artifacts, run the full suite, then reject uncommitted changes.
test: generated-artifacts
	$(PYTHON) -m unittest discover -s tests -t .
	$(MAKE) generated-artifacts-commit-check

# Run one module with the same generated-artifact contract as the full suite.
test-module: generated-artifacts
	$(PYTHON) -m unittest $(M)
	$(MAKE) generated-artifacts-commit-check

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

room-event-types:
	$(PYTHON) scripts/generate_room_event_types.py

room-event-types-check:
	$(PYTHON) scripts/generate_room_event_types.py --check

room-event-types-commit-check:
	@if ! git diff --quiet --no-ext-diff -- $(ROOM_EVENT_TYPE_OUTPUT) \
		|| ! git diff --cached --quiet --no-ext-diff HEAD -- $(ROOM_EVENT_TYPE_OUTPUT); then \
		printf '%s\n' \
			'Generated room-event types differ from HEAD.' \
			'Review and commit the generated file before treating verification as complete.'; \
		git --no-pager diff --stat --no-ext-diff -- $(ROOM_EVENT_TYPE_OUTPUT); \
		git --no-pager diff --cached --stat --no-ext-diff HEAD -- $(ROOM_EVENT_TYPE_OUTPUT); \
		exit 1; \
	fi

room-event-types-verify: room-event-types
	$(MAKE) room-event-types-commit-check

generated-artifacts: codebase-map room-event-types

generated-artifacts-check: codebase-map-check room-event-types-check

generated-artifacts-commit-check: codebase-map-commit-check room-event-types-commit-check

generated-artifacts-verify: generated-artifacts
	$(MAKE) generated-artifacts-commit-check
