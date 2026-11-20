This package uses [uv](https://docs.astral.sh/uv/) for dependency locking (uv.lock). Use the uv version named by `required-version` in pyproject.toml.

VAMS uses multiple lambda layers to store the dependencies when used on AWS. Each layer has its own pyproject.toml, uv.lock and committed requirements.txt.
The top level pyproject.toml, uv.lock and requirements files are used for local testing only and are a combined list of requirements from all layers.

Use the below for each layer or top-level tree. 

-------------

### How to build these packages
The Dockerfile should do everything you would need. If you included new dependencies make sure you export them into the requirements file.

More Details:
https://github.com/aws/aws-cdk/issues/14201

### Adding New Dependencies or updating package versions
`uv add stepfunctions` (or edit the dependency in pyproject.toml, then run `uv lock`)

### Removing Dependencies
`uv remove stepfunctions`

### Export uv dependencies to requirements.txt
`uv export --frozen --no-hashes --no-emit-project --no-dev --no-annotate -o requirements.txt`

The deploy installs the committed requirements.txt with pip; it never runs uv. See backend/CLAUDE.md, "Updating Python Dependencies".


### Running Tests - top level backend
`make all` should trigger all the code checks we have included so far
We use
1. flake8 for linting
2. mypy for type checks
3. pytest for running Tests
4. coverage to run code coverage

### Why does my PR fail in actions

Likely because of some failure while `make all`.

Try running github actions locally with [act](https://github.com/nektos/act/)

`act` should pickup the current ci.yml and run it locally inside a docker container

-------------

### Local Development

See documentation for instructions on how to deploy local developments for external OAUTH authentication and a basic API. 
