
lint:
	flake8

typecheck:
	mypy .

coverage:
	coverage run -m pytest
	coverage report

install:
	poetry install

all: install lint typecheck coverage