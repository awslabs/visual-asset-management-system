#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Every pipeline test module either runs in CI or is listed as excluded with its reason.

CI runs the pipeline suites through ``backendPipelines/ci/pipeline_tests.py``, which finds the test
directories itself. These checks keep that discovery honest: a test module outside a ``tests``
directory is never run, an exclusion whose directory is gone hides nothing but reads as if it did,
and a workflow that runs fewer groups than the runner splits into skips whole suites while green.
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
        for path in suite.requirement_files:
            assert path.is_file(), f"{path} is missing"
            # Raises on an include whose target is missing.
            runner.requirement_lines(path)


def test_the_workflow_runs_every_group():
    with open(_WORKFLOW, encoding="utf-8") as handle:
        workflow = handle.read()
    assert re.search(r"python backendPipelines/ci/pipeline_tests\.py run --group \$\{\{ matrix\.group \}\}", workflow)
    groups = re.search(r"^\s+group: \[([0-9, ]+)\]\s*$", workflow, re.M)
    assert groups, "the pipeline test job has no group matrix"
    assert [int(g) for g in groups.group(1).split(",")] == list(range(1, runner.GROUP_COUNT + 1))
