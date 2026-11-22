#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""The Cosmos Transfer job runs with the NCCL wheel the image locks.

The image build syncs upstream's environment from upstream's uv.lock at the pinned commit, then installs
nvidia-nccl-cu12 from the hashed requirements-nccl.lock over it. Upstream's lock pins an older NCCL, so a
command that syncs or installs into that environment afterwards puts upstream's version back. That holds
for a later build step, for entrypoint.sh at job start, and for a subprocess the job's Python code starts.
`uv run` syncs first too, unless it is given `--no-sync`. The entrypoint used to run `uv sync --locked`
at every start, with its errors discarded.
"""

import ast
import re
from pathlib import Path

import pytest

CONTAINER_DIR = Path(__file__).resolve().parent.parent
# The job's own Python modules (`__main__.py` imports the other two).
_JOB_MODULES = ("__main__.py", "inference.py", "model_manager.py")

# uv's global options before a subcommand, such as `--quiet` or `--directory /opt/x`.
_UV_OPTIONS = r"(?:\s+-{1,2}[\w-]+(?:[ =][^\s;&|]+)?)*"
# Commands that change an installed Python environment. The uv binary may be named directly (`uv`,
# `/usr/local/bin/uv`) or held in a shell variable (`"$UV"`, `${UV}`).
_ENV_CHANGE = re.compile(
    r"(?:\buv\b|\"?\$\{?\w+\}?\"?)" + _UV_OPTIONS
    + r"\s+(?:sync|add|remove|run(?![^;&|\n]*--no-sync)|pip" + _UV_OPTIONS + r"\s+(?:install|sync|uninstall))\b"
    r"|\bpip3?\s+(?:install|uninstall)\b"
    r"|\bpython3?\s+-m\s+pip\s+(?:install|uninstall)\b"
)


def _commands(text):
    """The script's lines without comment lines, with backslash continuations joined."""
    joined = re.sub(r"\\\n", " ", text)
    return [line for line in joined.splitlines() if line.strip() and not line.lstrip().startswith("#")]


def _env_changes(text):
    return [line.strip() for line in _commands(text) if _ENV_CHANGE.search(line)]


def _dockerfile_runs(text):
    return [line for line in _commands(text) if line.lstrip().startswith("RUN ")]


def _nccl_install_index(runs):
    installs = [i for i, run in enumerate(runs) if "requirements-nccl.lock" in run]
    assert len(installs) == 1, f"expected one RUN that installs requirements-nccl.lock, found {len(installs)}"
    return installs[0]


def _env_changes_after_nccl_install(dockerfile):
    runs = _dockerfile_runs(dockerfile)
    return [run.strip() for run in runs[_nccl_install_index(runs) + 1:] if _ENV_CHANGE.search(run)]


def _uv_subprocess_strings(source):
    """String literals, docstrings excepted, that name the uv binary or hold an environment-changing command."""
    tree = ast.parse(source)
    docstrings = {id(node.value) for node in ast.walk(tree)
                  if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant)}
    return [node.value for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstrings
            and (node.value == "uv" or node.value.endswith("/uv") or _ENV_CHANGE.search(node.value))]


def test_entrypoint_does_not_change_the_environment():
    changes = _env_changes((CONTAINER_DIR / "entrypoint.sh").read_text())
    assert changes == [], (
        "entrypoint.sh must not sync or install into the image's environment: upstream's lock would "
        f"replace the locked NCCL wheel at every job start. Found: {changes}")


def test_nccl_install_is_hashed_and_follows_upstream_sync():
    runs = _dockerfile_runs((CONTAINER_DIR / "Dockerfile").read_text())
    index = _nccl_install_index(runs)
    install = runs[index]
    assert re.search(r"\buv\s+pip\s+install\b", install)
    assert "--require-hashes" in install and "--only-binary :all:" in install
    assert any(re.search(r"\buv\s+sync\b.*--locked", run) for run in runs[:index]), (
        "the NCCL install must come after upstream's `uv sync --locked`")


def test_no_build_step_changes_the_environment_after_the_nccl_install():
    later = _env_changes_after_nccl_install((CONTAINER_DIR / "Dockerfile").read_text())
    assert later == [], f"a build step after the NCCL install changes the environment: {later}"


@pytest.mark.parametrize("module", _JOB_MODULES)
def test_job_code_starts_no_uv_subprocess(module):
    found = _uv_subprocess_strings((CONTAINER_DIR / module).read_text())
    assert found == [], f"{module} must not run uv or install packages in a subprocess. Found: {found}"


@pytest.mark.parametrize("line", [
    "uv sync --locked",
    "/usr/local/bin/uv sync",
    "uv sync --locked || true # trailing",
    "uv --quiet sync --locked",
    "uv -q --directory /opt/cosmos-transfer2.5 sync",
    'UV=uv; "$UV" sync',
    "${UV} sync --locked",
    "uv run --locked python -c pass",
    "uv run python -c pass && uv run --no-sync python -c pass",
    "uv add nvidia-nccl-cu12==2.26.2",
    "uv pip install nvidia-nccl-cu12==2.26.2",
    "uv pip --quiet install foo",
    "python -m pip install foo",
    ".venv/bin/pip install foo",
])
def test_guard_catches_an_environment_change_in_the_entrypoint(line):
    assert _env_changes(line) == [line]


@pytest.mark.parametrize("line", [
    "uv run --no-sync python -c pass",
    "uvx hf download nvidia/model --quiet",
    "uv --version",
    "uv lock --check",
    'exec "$@"',
    "apt-get install -y --no-install-recommends python3-dev",
])
def test_guard_allows_commands_that_leave_the_environment_alone(line):
    assert _env_changes(line) == []


@pytest.mark.parametrize("step", [
    "RUN uv sync --locked --extra=cu128",
    "RUN .venv/bin/pip install foo",
    "RUN apt-get update && uv run --locked python -c pass",
])
def test_guard_catches_an_environment_change_after_the_nccl_install(step):
    dockerfile = (CONTAINER_DIR / "Dockerfile").read_text() + "\n" + step + "\n"
    assert _env_changes_after_nccl_install(dockerfile) == [step]


@pytest.mark.parametrize("source", [
    'subprocess.run(["uv", "sync", "--locked"], check=True)',
    'subprocess.run(["/usr/local/bin/uv", "pip", "install", "foo"])',
    'os.system("uv sync --locked")',
    'cmd = ["uv", "run", "python", "inference.py"]',
])
def test_guard_catches_a_uv_subprocess_in_job_code(source):
    assert _uv_subprocess_strings(source) != []
