# Local commands. CI runs the same gates in .github/workflows/ci.yml.
PY := .venv/Scripts/python.exe

.PHONY: install lint fmt types test cov gates check test-live serve

install:
	$(PY) -m pip install -e ".[dev]"

lint:
	$(PY) -m ruff check .
	$(PY) -m ruff format --check .

fmt:
	$(PY) -m ruff check . --fix
	$(PY) -m ruff format .

types:
	$(PY) -m mypy underwriter

# OPS-033: the live suite is excluded here and never runs in CI.
test:
	$(PY) -m pytest -m "not live"

cov:
	$(PY) -m pytest -m "not live" --cov=underwriter --cov-branch --cov-report=term-missing

# OPS-031: the build fails if the Kernel is under 100% or the Actuary under 95%.
gates: cov
	$(PY) -m coverage report --include="underwriter/kernel/*" --fail-under=100
	$(PY) -m coverage report --include="underwriter/actuary/*" --fail-under=95

check: lint types gates

# ROAD-D0-02 and ROAD-D0-03. Needs real paper credentials in .env.
# Add ALPACA_ALLOW_TEST_ORDER=true to also place and cancel the ALP-003 probe order.
test-live:
	$(PY) -m pytest -m live -v -s

serve:
	$(PY) -m uvicorn underwriter.server:app --reload --port 8000
