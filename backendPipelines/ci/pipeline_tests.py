#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Find and run the pipeline test suites, one pytest process per test directory.

Pipeline test modules share basenames across pipelines and no tests directory is a package, so one
pytest process over ``backendPipelines/`` fails at collection (``import file mismatch``). Each test
directory therefore runs in its own process, from the directory that holds it
(``python -m pytest tests -q``), with that directory's own requirements.

A test directory is any directory named ``tests`` under ``backendPipelines/`` that holds a
``test_*.py`` file. A new one is found without editing the CI workflow. Its environment is built
from, in install order:

1. ``requirements-common.txt`` beside this file: pytest plus the Lambda base layer packages the
   pipeline Lambdas import at runtime.
2. The nearest ``requirements.txt`` above the tests directory, stopping below the pipeline's use-case
   directory (``lambda/requirements.txt``, ``container/requirements.txt``).
3. The directory's ``extraRequirements`` in ``suite-settings.json`` beside this file, when it has
   any, for packages only the tests import: requirement lines, or ``-r <repo-relative path>``.

Directories with the same requirements share one virtual environment. A directory that cannot run on
a CPU-only runner is listed in ``excluded-test-dirs.json`` beside this file with its reason.

Every CI-only setting for a test directory lives in ``suite-settings.json``, never in the pipeline's
own folder: CDK packages those folders as container build contexts and Lambda code, so a file there
changes the deployed asset.

A directory whose tests render (PyVista/VTK and the like) has a ``headlessDisplay`` entry in
``suite-settings.json`` stating why. Its suite runs under ``xvfb-run -a`` with the environment the pipeline's container
image renders with (``HEADLESS_DISPLAY_ENV``), and its group needs ``DISPLAY_SYSTEM_PACKAGES``,
which ``system-packages`` prints for the CI job to install. Without a display, VTK segfaults on the
first render.

Usage::

    python backendPipelines/ci/pipeline_tests.py list
    python backendPipelines/ci/pipeline_tests.py system-packages --group 1
    python backendPipelines/ci/pipeline_tests.py run --group 1 --work-dir /tmp/pipeline-tests

CI adds ``--low-disk``: no pip cache, and each environment is removed after its last suite.

Standard library only: it runs before any dependency is installed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

CI_DIR = Path(__file__).resolve().parent
PIPELINES_ROOT = CI_DIR.parent
REPO_ROOT = PIPELINES_ROOT.parent
COMMON_REQUIREMENTS = CI_DIR / "requirements-common.txt"
EXCLUSIONS_FILE = CI_DIR / "excluded-test-dirs.json"
# Per test directory: extraRequirements and headlessDisplay, each with its reason.
SETTINGS_FILE = CI_DIR / "suite-settings.json"

# The rendering environment of the 3D thumbnail container image (its Dockerfile ENV lines). DISPLAY
# is not here: xvfb-run picks a free display number and sets it.
HEADLESS_DISPLAY_ENV = {
    "PYVISTA_OFF_SCREEN": "true",
    "MESA_GL_VERSION_OVERRIDE": "4.5",
    "VTK_DEFAULT_RENDER_WINDOW_OFFSCREEN": "1",
}
# The Ubuntu packages a rendering suite needs on a GitHub-hosted runner: the X virtual framebuffer
# (xvfb-run) and Mesa's software OpenGL that VTK draws through.
DISPLAY_SYSTEM_PACKAGES = ("xvfb", "libgl1", "libglx-mesa0", "libgl1-mesa-dri", "libglu1-mesa")
XVFB_SCREEN = "-screen 0 1024x768x24"

# The number of CI jobs the suites are split across. ci.yml runs one job per group; the guard test
# backendPipelines/tests/test_ci_pipeline_suites.py holds the two in step.
GROUP_COUNT = 3

SKIPPED_PARTS = {"node_modules", ".venv", "venv", "__pycache__", ".pytest_cache", "build", "cdk.out"}


@dataclass
class Suite:
    """One test directory and the requirements its environment is built from."""

    path: Path
    requirement_files: Tuple[Path, ...] = field(default_factory=tuple)
    # From suite-settings.json: installed after requirement_files, in one pip call.
    extra_requirements: Tuple[str, ...] = field(default_factory=tuple)
    needs_display: bool = False

    @property
    def rel(self) -> str:
        return self.path.relative_to(REPO_ROOT).as_posix()

    @property
    def env_key(self) -> str:
        names = [p.relative_to(REPO_ROOT).as_posix() for p in self.requirement_files]
        names += [f"extra:{entry}" for entry in self.extra_requirements]
        return hashlib.sha256("\n".join(names).encode()).hexdigest()[:12]


def _skipped(path: Path) -> bool:
    return any(part in SKIPPED_PARTS for part in path.relative_to(PIPELINES_ROOT).parts)


def pipeline_test_modules() -> List[Path]:
    """Every ``test_*.py`` module under backendPipelines/, wherever it sits."""
    return sorted(p for p in PIPELINES_ROOT.rglob("test_*.py") if not _skipped(p))


def tests_dir_of(module: Path) -> Optional[Path]:
    """The nearest directory named ``tests`` holding ``module``, or None when there is none."""
    for parent in module.parents:
        if parent == PIPELINES_ROOT.parent:
            return None
        if parent.name == "tests":
            return parent
    return None


def discover() -> List[Path]:
    """Every directory named ``tests`` that holds a test module, directly or below it."""
    found = {tests_dir_of(p) for p in pipeline_test_modules()}
    found.discard(None)
    return sorted(found)  # type: ignore[arg-type]


def load_exclusions() -> Dict[str, str]:
    """Repo-relative test directory -> the reason it does not run in CI."""
    data = json.loads(EXCLUSIONS_FILE.read_text(encoding="utf-8"))
    return {entry["path"]: entry["reason"] for entry in data["excluded"]}


def load_settings() -> Dict[str, dict]:
    """Repo-relative test directory -> its CI-only settings (extraRequirements, headlessDisplay)."""
    return json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))["suites"]


def included_file(entry: str) -> Optional[Path]:
    """The requirements file an ``-r <repo-relative path>`` entry names, or None for a requirement line."""
    if entry.startswith("-r "):
        return REPO_ROOT / entry[3:].strip()
    return None


def own_requirements(tests_dir: Path) -> Optional[Path]:
    """The nearest requirements.txt above ``tests_dir``, below the use-case directory."""
    # backendPipelines/<use case>/<pipeline>/... : stop before reaching the use-case directory.
    stop = len(PIPELINES_ROOT.parts) + 1
    current = tests_dir.parent
    while len(current.parts) > stop:
        candidate = current / "requirements.txt"
        if candidate.is_file():
            return candidate
        current = current.parent
    return None


def suites() -> List[Suite]:
    settings = load_settings()
    result = []
    for tests_dir in discover():
        files = [COMMON_REQUIREMENTS]
        own = own_requirements(tests_dir)
        if own is not None:
            files.append(own)
        entry = settings.get(tests_dir.relative_to(REPO_ROOT).as_posix(), {})
        extra = tuple(entry.get("extraRequirements", {}).get("requirements", []))
        result.append(Suite(tests_dir, tuple(files), extra, "headlessDisplay" in entry))
    return result


def requirement_lines(path: Path) -> List[str]:
    """The package lines of a requirements file, following its ``-r`` includes."""
    lines: List[str] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        if line.startswith("-r "):
            lines.extend(requirement_lines((path.parent / line[3:].strip()).resolve()))
        elif not line.startswith("-"):
            lines.append(line)
    return lines


def install_weight(suite: Suite) -> int:
    """A relative cost for building a suite's environment: the packages its requirements name."""
    weight = sum(len(requirement_lines(path)) for path in suite.requirement_files)
    for entry in suite.extra_requirements:
        included = included_file(entry)
        weight += len(requirement_lines(included)) if included is not None else 1
    return weight


def groups(runnable: Sequence[Suite], count: int = GROUP_COUNT) -> List[List[Suite]]:
    """Split the suites into ``count`` groups, keeping each shared environment in one group.

    Environments are placed heaviest first onto the lightest group, so each environment is built
    once and the groups take similar time. The split depends only on the checked-in files.
    """
    by_env: Dict[str, List[Suite]] = {}
    for suite in runnable:
        by_env.setdefault(suite.env_key, []).append(suite)
    envs = sorted(
        by_env.values(),
        key=lambda members: (-(install_weight(members[0]) + len(members)), members[0].rel),
    )
    buckets: List[List[Suite]] = [[] for _ in range(count)]
    loads = [0] * count
    for members in envs:
        target = min(range(count), key=lambda i: (loads[i], i))
        buckets[target].extend(members)
        loads[target] += install_weight(members[0]) + len(members)
    return [sorted(bucket, key=lambda s: s.rel) for bucket in buckets]


def runnable_suites() -> Tuple[List[Suite], Dict[str, str]]:
    exclusions = load_exclusions()
    return [s for s in suites() if s.rel not in exclusions], exclusions


def cmd_list(_: argparse.Namespace) -> int:
    runnable, exclusions = runnable_suites()
    for index, bucket in enumerate(groups(runnable), start=1):
        for suite in bucket:
            names = [p.relative_to(REPO_ROOT).as_posix() for p in suite.requirement_files[1:]]
            files = ", ".join(names + list(suite.extra_requirements))
            display = "  (headless display)" if suite.needs_display else ""
            print(f"group {index}  {suite.rel}  [{files or 'common only'}]{display}")
    for path, reason in sorted(exclusions.items()):
        print(f"excluded  {path}  ({reason})")
    return 0


def system_packages(bucket: Sequence[Suite]) -> List[str]:
    """The Ubuntu packages a group's suites need beyond Python: none unless one of them renders."""
    return list(DISPLAY_SYSTEM_PACKAGES) if any(suite.needs_display for suite in bucket) else []


def cmd_system_packages(args: argparse.Namespace) -> int:
    runnable, _ = runnable_suites()
    print(" ".join(system_packages(groups(runnable)[args.group - 1])))
    return 0


def pytest_command(suite: Suite, interpreter: Path) -> List[str]:
    command = [str(interpreter), "-m", "pytest", "tests", "-q", "--tb=short", "-p", "no:cacheprovider"]
    if suite.needs_display:
        # -a: a free display number, so suites never collide on one.
        command = ["xvfb-run", "-a", "-s", XVFB_SCREEN] + command
    return command


def pytest_env(suite: Suite) -> Dict[str, str]:
    env = dict(os.environ)
    if suite.needs_display:
        env.update(HEADLESS_DISPLAY_ENV)
    return env


def _run(command: Sequence[str], cwd: Path, log, env: Optional[Dict[str, str]] = None) -> int:
    log.write(f"$ {' '.join(command)}  (in {cwd})\n")
    log.flush()
    return subprocess.run(command, cwd=cwd, env=env, stdout=log, stderr=subprocess.STDOUT, check=False).returncode


def env_dir_of(suite: Suite, work_dir: Path) -> Path:
    return work_dir / f"venv-{suite.env_key}"


def build_env(suite: Suite, work_dir: Path, python: str, log, low_disk: bool) -> Tuple[Optional[Path], float]:
    """Create (or reuse) the virtual environment for ``suite``; return its interpreter."""
    env_dir = env_dir_of(suite, work_dir)
    interpreter = env_dir / "bin" / "python"
    marker = env_dir / ".installed"
    if marker.is_file():
        return interpreter, 0.0
    start = time.monotonic()
    if _run([python, "-m", "venv", str(env_dir)], REPO_ROOT, log) != 0:
        return None, time.monotonic() - start
    pip = [str(interpreter), "-m", "pip", "install", "--disable-pip-version-check", "--no-input", "-q"]
    if low_disk:
        pip.append("--no-cache-dir")
    # One install per file, in order, so a pipeline's own pin replaces a common one instead of
    # conflicting with it.
    for requirements in suite.requirement_files:
        if _run(pip + ["-r", str(requirements)], REPO_ROOT, log) != 0:
            return None, time.monotonic() - start
    if suite.extra_requirements:
        extra: List[str] = []
        for entry in suite.extra_requirements:
            included = included_file(entry)
            extra += ["-r", str(included)] if included is not None else [entry]
        if _run(pip + extra, REPO_ROOT, log) != 0:
            return None, time.monotonic() - start
    lines = [str(p) for p in suite.requirement_files] + list(suite.extra_requirements)
    marker.write_text("\n".join(lines), encoding="utf-8")
    return interpreter, time.monotonic() - start


def _summary_line(log_path: Path) -> str:
    lines = [line.strip() for line in log_path.read_text(encoding="utf-8", errors="replace").splitlines()]
    lines = [line for line in lines if line]
    return lines[-1] if lines else "(no output)"


def cmd_run(args: argparse.Namespace) -> int:
    runnable, _ = runnable_suites()
    bucket = groups(runnable)[args.group - 1]
    # Suites sharing an environment run back to back, so with --low-disk each environment can be
    # removed once its last suite has run and the job holds one environment at a time.
    first_seen: Dict[str, int] = {}
    for index, suite in enumerate(bucket):
        first_seen.setdefault(suite.env_key, index)
    bucket = sorted(bucket, key=lambda s: (first_seen[s.env_key], s.rel))
    last_use = {suite.env_key: index for index, suite in enumerate(bucket)}
    work_dir = Path(args.work_dir).resolve()
    work_dir.mkdir(parents=True, exist_ok=True)
    logs = work_dir / "logs"
    logs.mkdir(exist_ok=True)
    rows = []
    failed = 0
    for index, suite in enumerate(bucket):
        log_path = logs / (suite.rel.replace("/", "__") + ".log")
        with log_path.open("w", encoding="utf-8") as log:
            interpreter, install_s = build_env(suite, work_dir, args.python, log, args.low_disk)
            test_s = 0.0
            rc = 1
            no_display = suite.needs_display and shutil.which("xvfb-run") is None
            if no_display:
                log.write(
                    f"{suite.rel} renders (headlessDisplay in {SETTINGS_FILE.name}) and xvfb-run is not installed; install: "
                    f"{' '.join(DISPLAY_SYSTEM_PACKAGES)}\n"
                )
            elif interpreter is not None:
                start = time.monotonic()
                rc = _run(pytest_command(suite, interpreter), suite.path.parent, log, pytest_env(suite))
                test_s = time.monotonic() - start
        if no_display:
            result = "xvfb-run not installed"
        elif interpreter is None:
            result = "environment install failed"
        else:
            result = _summary_line(log_path)
        if rc != 0:
            failed += 1
            print(f"::group::FAILED {suite.rel}")
            print(log_path.read_text(encoding="utf-8", errors="replace"))
            print("::endgroup::")
        rows.append((suite.rel, rc, install_s, test_s, result))
        print(f"{'ok  ' if rc == 0 else 'FAIL'} {suite.rel}  install {install_s:.0f}s  tests {test_s:.1f}s  {result}", flush=True)
        if args.low_disk and last_use[suite.env_key] == index:
            shutil.rmtree(env_dir_of(suite, work_dir), ignore_errors=True)

    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as out:
            out.write(f"## Pipeline tests, group {args.group} of {GROUP_COUNT}\n\n")
            out.write("| Directory | Result | Install | Tests | Summary |\n| --- | --- | --- | --- | --- |\n")
            for rel, rc, install_s, test_s, result in rows:
                status = "pass" if rc == 0 else "FAIL"
                clean = result.replace("|", "\\|")
                out.write(f"| {rel} | {status} | {install_s:.0f}s | {test_s:.1f}s | {clean} |\n")
            out.write("\n")
    print(f"{len(rows) - failed} of {len(rows)} pipeline test directories passed in group {args.group}")
    return 1 if failed else 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list", help="print every test directory with its group or exclusion")
    packages = sub.add_parser("system-packages", help="print the Ubuntu packages one group's suites need")
    packages.add_argument("--group", type=int, required=True, choices=range(1, GROUP_COUNT + 1))
    run = sub.add_parser("run", help="build the environments for one group and run its suites")
    run.add_argument("--group", type=int, required=True, choices=range(1, GROUP_COUNT + 1))
    run.add_argument("--work-dir", required=True, help="where the virtual environments and logs go")
    run.add_argument("--python", default=sys.executable, help="interpreter the environments are made from")
    run.add_argument(
        "--low-disk",
        action="store_true",
        help="skip the pip cache and remove each environment after its last suite (CI runners)",
    )
    args = parser.parse_args(argv)
    return {"list": cmd_list, "system-packages": cmd_system_packages, "run": cmd_run}[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
