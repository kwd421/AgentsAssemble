.PHONY: test test-module frontend-deps frontend-build

PYTHON ?= python3

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
