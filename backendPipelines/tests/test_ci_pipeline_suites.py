#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Every pipeline test module either runs in CI or is listed as excluded with its reason.

CI runs the pipeline suites through ``backendPipelines/ci/pipeline_tests.py``, which finds the test
directories itself. These checks keep that discovery honest: a test module outside a ``tests``
directory is never run, an exclusion whose directory is gone hides nothing but reads as if it did,
and a workflow that runs fewer groups than the runner splits into skips whole suites while green.
CI-only per-directory settings stay in ``backendPipelines/ci/suite-settings.json``: a file in a
pipeline folder would change that pipeline's deployed asset.
"""

import importlib.util
import os
import re
import sys

import pytest

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_RUNNER = os.path.join(_REPO_ROOT, "backendPipelines", "ci", "pipeline_tests.py")
_WORKFLOW = os.path.join(_REPO_ROOT, ".github", "workflows", "ci.yml")


def _load_runner():
    spec = importlib.util.spec_from_file_location("_ci_pipeline_tests_runner", _RUNNER)
    module = importlib.util.module_from_spec(spec)
    # dataclasses resolves the module through sys.modules while building the class.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


runner = _load_runner()


@pytest.fixture(scope="module")
def exclusions():
    return runner.load_exclusions()


@pytest.fixture(scope="module")
def discovered():
    return [path.relative_to(runner.REPO_ROOT).as_posix() for path in runner.discover()]


def test_discovery_finds_the_cross_pipeline_suite_and_a_container_suite(discovered):
    """Control: a discovery that finds nothing would pass every other check here."""
    assert "backendPipelines/tests" in discovered
    assert "backendPipelines/preview/3dThumbnail/container/tests" in discovered
    assert len(discovered) >= 30


def test_every_test_module_sits_in_a_tests_directory():
    stray = [
        path.relative_to(runner.REPO_ROOT).as_posix()
        for path in runner.pipeline_test_modules()
        if runner.tests_dir_of(path) is None
    ]
    assert not stray, f"CI runs only directories named tests; move these into one: {stray}"


def test_every_exclusion_names_a_discovered_directory_and_a_reason(exclusions, discovered):
    for path, reason in exclusions.items():
        assert path in discovered, f"{path} is excluded but holds no test module; remove the entry"
        assert reason.strip(), f"{path} is excluded without a reason"


def test_every_discovered_directory_is_run_or_excluded_exactly_once(exclusions, discovered):
    runnable, _ = runner.runnable_suites()
    grouped = [suite.rel for bucket in runner.groups(runnable) for suite in bucket]
    assert len(grouped) == len(set(grouped)), "a directory is placed in two groups"
    assert sorted(grouped + list(exclusions)) == sorted(discovered)


def test_a_shared_environment_is_built_in_one_group_only():
    runnable, _ = runner.runnable_suites()
    owner = {}
    for index, bucket in enumerate(runner.groups(runnable)):
        for suite in bucket:
            assert owner.setdefault(suite.env_key, index) == index, f"{suite.rel} splits its environment"


def test_every_requirements_include_resolves():
    for suite in runner.suites():
        included = [runner.included_file(entry) for entry in suite.extra_requirements]
        for path in list(suite.requirement_files) + [p for p in included if p is not None]:
            assert path.is_file(), f"{path} is missing"
            # Raises on an include whose target is missing.
            runner.requirement_lines(path)


@pytest.fixture(scope="module")
def settings():
    return runner.load_settings()


def test_every_suite_setting_names_a_discovered_directory_and_a_reason(settings, discovered):
    assert settings, "suite-settings.json holds no suites; the checks below would pass vacuously"
    for path, entry in settings.items():
        assert path in discovered, f"{path} has settings but holds no test module; remove the entry"
        assert entry, f"{path} has an empty settings entry"
        assert set(entry) <= {"extraRequirements", "headlessDisplay"}, f"{path}: unknown setting {sorted(entry)}"
        for name, setting in entry.items():
            assert setting.get("reason", "").strip(), f"{path}: {name} has no reason"
        if "extraRequirements" in entry:
            requirements = entry["extraRequirements"].get("requirements", [])
            assert requirements and all(r.strip() for r in requirements), f"{path}: no extraRequirements"


# The per-directory files the settings replaced. CDK packages the pipeline folders as container
# build contexts and Lambda code, so a CI-only file there changes the deployed asset.
_RETIRED_SETTING_FILES = ("requirements-test.txt", "ci-headless-display.txt")


def test_no_ci_only_setting_file_sits_in_a_pipeline_folder():
    found = [
        path.relative_to(runner.REPO_ROOT).as_posix()
        for name in _RETIRED_SETTING_FILES
        for path in runner.PIPELINES_ROOT.rglob(name)
        if not runner._skipped(path)
    ]
    assert not found, f"put these in backendPipelines/ci/{runner.SETTINGS_FILE.name} instead: {found}"


# Calls that open a render window. A suite whose pipeline code makes one needs a display in CI.
_RENDERING = re.compile(
    r"\b(?:pv|pyvista)\.Plotter\(|vtkRenderWindow\b|pyrender\.OffscreenRenderer\b"
    r"|\b(?:o3d|open3d)\.visualization\b"
)
_THUMBNAIL_TESTS = "backendPipelines/preview/3dThumbnail/container/tests"


def _renders(suite):
    """Whether any Python module beside or below the suite's tests directory opens a render window."""
    # The cross-pipeline suite sits at the root, whose parent is every pipeline: scan only its own files.
    scope = suite.path if suite.path.parent == runner.PIPELINES_ROOT else suite.path.parent
    for path in scope.rglob("*.py"):
        if runner._skipped(path) or path.samefile(__file__):
            continue
        if _RENDERING.search(path.read_text(encoding="utf-8", errors="replace")):
            return True
    return False


def test_the_thumbnail_suite_runs_under_a_headless_display():
    """Control: the scan below passes vacuously if it finds no rendering suite at all."""
    suite = next(s for s in runner.suites() if s.rel == _THUMBNAIL_TESTS)
    assert _renders(suite)
    assert suite.needs_display


def test_every_rendering_suite_declares_a_headless_display_and_why():
    for suite in runner.suites():
        if _renders(suite):
            assert suite.needs_display, (
                f"{suite.rel} renders; give it a headlessDisplay entry with its reason in "
                f"{runner.SETTINGS_FILE.name}, or VTK segfaults in CI"
            )
        if suite.needs_display:
            reason = runner.load_settings()[suite.rel]["headlessDisplay"].get("reason", "")
            assert reason.strip(), f"{suite.rel} declares a headless display without a reason"


def test_the_display_environment_matches_the_thumbnail_container_image():
    dockerfile = os.path.join(_REPO_ROOT, "backendPipelines", "preview", "3dThumbnail", "container", "Dockerfile")
    with open(dockerfile, encoding="utf-8") as handle:
        image_env = dict(re.findall(r"^ENV (\w+)=(\S*)\s*$", handle.read(), re.M))
    for name, value in runner.HEADLESS_DISPLAY_ENV.items():
        assert image_env.get(name) == value, f"{name} differs from the container image"
    # The image starts Xvfb on :99 itself; in CI xvfb-run picks the display, so DISPLAY is not forced.
    assert "DISPLAY" in image_env and "DISPLAY" not in runner.HEADLESS_DISPLAY_ENV


def test_a_rendering_suite_runs_under_xvfb_run_and_others_run_bare():
    suite = next(s for s in runner.suites() if s.rel == _THUMBNAIL_TESTS)
    bare = next(s for s in runner.suites() if not s.needs_display)
    interpreter = runner.REPO_ROOT / "python"
    wrapped = runner.pytest_command(suite, interpreter)
    assert wrapped[:2] == ["xvfb-run", "-a"]
    assert wrapped[-len(runner.pytest_command(bare, interpreter)) :] == runner.pytest_command(bare, interpreter)
    assert runner.pytest_command(bare, interpreter)[0] == str(interpreter)
    for name, value in runner.HEADLESS_DISPLAY_ENV.items():
        assert runner.pytest_env(suite)[name] == value
    assert "xvfb" in runner.DISPLAY_SYSTEM_PACKAGES


def test_only_a_group_with_a_rendering_suite_installs_system_packages():
    runnable, _ = runner.runnable_suites()
    for bucket in runner.groups(runnable):
        expected = list(runner.DISPLAY_SYSTEM_PACKAGES) if any(s.needs_display for s in bucket) else []
        assert runner.system_packages(bucket) == expected
    assert any(runner.system_packages(bucket) for bucket in runner.groups(runnable))


def test_the_workflow_installs_the_packages_the_runner_names_for_its_group():
    with open(_WORKFLOW, encoding="utf-8") as handle:
        workflow = handle.read()
    assert re.search(
        r"python backendPipelines/ci/pipeline_tests\.py system-packages --group \$\{\{ matrix\.group \}\}", workflow
    )
    assert 'sudo apt-get install -y --no-install-recommends "${packages[@]}"' in workflow


def test_the_workflow_runs_every_group():
    with open(_WORKFLOW, encoding="utf-8") as handle:
        workflow = handle.read()
    assert re.search(r"python backendPipelines/ci/pipeline_tests\.py run --group \$\{\{ matrix\.group \}\}", workflow)
    groups = re.search(r"^\s+group: \[([0-9, ]+)\]\s*$", workflow, re.M)
    assert groups, "the pipeline test job has no group matrix"
    assert [int(g) for g in groups.group(1).split(",")] == list(range(1, runner.GROUP_COUNT + 1))
