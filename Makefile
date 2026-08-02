.DEFAULT_GOAL := test

.PHONY: help test test-module test-quality-check architecture-check verify postgres-contracts
.PHONY: frontend-deps frontend-build frontend-test frontend-e2e
.PHONY: desktop-deps desktop-check desktop-dev desktop-build
.PHONY: mobile-ios-init mobile-ios-build mobile-ios-release
.PHONY: mobile-android-init mobile-android-build mobile-android-release
.PHONY: mutation-canaries diff-check
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
		'make test                 Check changed tests, refresh artifacts, run the Python suite' \
		'make verify               Run the complete local release boundary (PostgreSQL is required)' \
		'make test-module M=...    Refresh generated artifacts, run one Python module, require committed outputs' \
		'make test-quality-check   Reject shallow Python tests changed since TEST_QUALITY_BASE (default HEAD)' \
		'make architecture-check   Reject package-boundary and unowned source growth violations' \
		'make postgres-contracts   Run mandatory PostgreSQL contracts without allowing skips' \
		'make frontend-deps        Install frontend dependencies' \
		'make frontend-build       Build the frontend' \
		'make frontend-test        Run frontend unit tests' \
		'make frontend-e2e         Build and run canonical browser workflows' \
		'make desktop-deps         Install desktop client dependencies' \
		'make desktop-check        Compile and test the desktop shell' \
		'make desktop-dev          Build the local runtime and open the desktop client' \
		'make desktop-build        Build the self-contained native desktop installer' \
		'make mobile-ios-init      Generate the ignored native iOS workspace' \
		'make mobile-ios-build     Build the keyless iOS simulator application' \
		'make mobile-ios-release   Build an App Store Connect IPA using the configured Apple account' \
		'make mobile-android-init  Generate the ignored native Android workspace' \
		'make mobile-android-build Build the keyless Android debug APK' \
		'make mobile-android-release Build a signed Play Store AAB from environment credentials' \
		'make mutation-canaries    Run critical authorization, rollback, room-scope, and response-order canaries' \
		'make codebase-map         Regenerate all three checked-in architecture maps' \
		'make codebase-map-check   Check maps without changing the working tree' \
		'make codebase-map-verify  Regenerate maps and fail if they differ from HEAD' \
		'make room-event-types     Regenerate the checked frontend room-event contract' \
		'make room-event-types-check  Check the room-event contract without changing the working tree' \
		'make generated-artifacts-verify  Regenerate and verify every checked-in generated artifact'

# Refresh generated artifacts, run the Python suite, then reject uncommitted
# generated outputs. This is intentionally not the complete release boundary;
# use `make verify` for that.
test: test-quality-check architecture-check generated-artifacts
	$(PYTHON) -m unittest discover -s tests -t .
	$(MAKE) generated-artifacts-commit-check

# Run one module with the same generated-artifact contract as the Python suite.
test-module: test-quality-check architecture-check generated-artifacts
	$(PYTHON) -m unittest $(M)
	$(MAKE) generated-artifacts-commit-check

# One local command for every executable release boundary. PostgreSQL
# prerequisites are deliberately mandatory: the runner exits nonzero if the
# DSN, driver packages, or any selected contract is missing or skipped.
verify:
	$(MAKE) test-quality-check
	$(MAKE) architecture-check
	$(MAKE) generated-artifacts-check
	$(PYTHON) -m unittest discover -s tests -t .
	$(MAKE) postgres-contracts
	$(MAKE) frontend-test
	$(MAKE) frontend-e2e
	$(MAKE) mutation-canaries
	$(MAKE) diff-check

TEST_QUALITY_BASE ?= HEAD

test-quality-check:
	$(PYTHON) scripts/check_test_quality.py --base $(TEST_QUALITY_BASE)

architecture-check:
	$(PYTHON) scripts/check_package_architecture.py
	$(PYTHON) scripts/check_source_growth.py

frontend-deps:
	npm --prefix frontend ci

frontend-build:
	npm --prefix frontend run build

frontend-test:
	npm --prefix frontend test

frontend-e2e:
	npm --prefix frontend run test:e2e

desktop-deps:
	npm --prefix desktop ci

desktop-check:
	npm --prefix desktop run check

desktop-dev:
	npm --prefix desktop run dev

desktop-build:
	npm --prefix desktop run build

mobile-ios-init:
	npm --prefix desktop run mobile:ios:init

mobile-ios-build:
	npm --prefix desktop run mobile:ios:build

mobile-ios-release:
	npm --prefix desktop run mobile:ios:release

mobile-android-init:
	npm --prefix desktop run mobile:android:init

mobile-android-build:
	npm --prefix desktop run mobile:android:build

mobile-android-release:
	npm --prefix desktop run mobile:android:release

postgres-contracts:
	$(PYTHON) -m tests.run_postgres_contracts

mutation-canaries:
	$(PYTHON) -m unittest \
		tests.test_gui_server_room_routes.GuiServerRoomRouteTests.test_agent_session_http_mutations_require_authorization_without_process_start \
		tests.test_room_invite_repository.JsonInviteSessionRepositoryTests.test_replace_failure_rolls_back_session_mutation \
		tests.test_gui_server_lobby_social.GuiServerLobbySocialTests.test_side_chat_applies_retention_limit_within_the_requested_room \
		tests.test_opencode_runtime.OpenCodeRuntimeTests.test_late_previous_turn_events_do_not_complete_current_turn

diff-check:
	git diff --check

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
