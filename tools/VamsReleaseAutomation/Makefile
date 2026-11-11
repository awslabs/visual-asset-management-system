PYTHON ?= python3

.PHONY: check install-dev test index

# Full offline self-check (tests, parse, ledger validate, doc/CLI match, FILES.md, links, neutrality scan)
check:
	PYTHON=$(PYTHON) bash scripts/check.sh

install-dev:
	$(PYTHON) -m pip install -e '.[dev]'

# pytest over the testpaths in pyproject.toml (engine/tests, adapters/shared, scripts/tests)
test:
	$(PYTHON) -m pytest

# Regenerate FILES.md after adding, removing or renaming a file
index:
	$(PYTHON) scripts/gen_index.py .
