
lint:
	uv run flake8

test:
	uv run ptw --runner 'py.test -vv'

typecheck:
	uv run mypy .

coverage:
	uv run coverage run -m pytest
	uv run coverage report

install:
	uv sync --locked

all: install lint typecheck coverage
