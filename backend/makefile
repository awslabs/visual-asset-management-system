
lint:
	poetry run flake8

typecheck:
	poetry run mypy .

coverage:
	poetry run coverage run -m pytest
	poetry run coverage report

install:
	poetry install

all: install lint typecheck coverage
