# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The backend suite runs on the package versions the deployed handler Lambdas load.

A handler Lambda ships only the `backend/backend` source and loads every third-party package
from the `vams_layer_base` layer, which `lambdaLayersBuilder-nestedStack.ts` bundles from
`backend/lambdaLayers/base` by exporting that directory's `poetry.lock`. The backend suite
installs `backend/requirements-dev.txt`, exported from `backend/poetry.lock`. A package the two
locks carry at different versions therefore runs in production on a version no backend test
exercised -- for `casbin`, the engine behind every Tier-1 and Tier-2 check.

The API Gateway authorizer loads `vams_layer_authorizer` (`backend/lambdaLayers/authorizer`), a
separate dependency set that this module does not compare.
"""

import pathlib
import re
import tomllib

import pytest

BACKEND_DIR = pathlib.Path(__file__).resolve().parents[1]
BACKEND_LOCK = BACKEND_DIR / 'poetry.lock'
BASE_LAYER_LOCK = BACKEND_DIR / 'lambdaLayers' / 'base' / 'poetry.lock'

# `name==version` at the start of a Poetry-exported requirements line; the markers after `;`
# are not compared (the backend is resolved for Python 3.13+, the layer for the 3.12 runtime).
REQUIREMENT_PIN = re.compile(r'^([A-Za-z0-9][A-Za-z0-9._-]*)(?:\[[^\]]*\])?==([^\s;]+)')


def _normalize(name):
    """PEP 503 normalized project name, so `typing_extensions` and `typing-extensions` match."""
    return re.sub(r'[-_.]+', '-', name).lower()


def _locked_versions(lock_path):
    """{normalized name: set of versions} for every package in a poetry.lock."""
    with open(lock_path, 'rb') as handle:
        packages = tomllib.load(handle).get('package', [])
    versions = {}
    for package in packages:
        versions.setdefault(_normalize(package['name']), set()).add(package['version'])
    return versions


def _pinned_versions(requirements_path):
    """{normalized name: set of versions} for every pin in a Poetry-exported requirements file."""
    pins = {}
    for line in requirements_path.read_text(encoding='utf-8').splitlines():
        match = REQUIREMENT_PIN.match(line.strip())
        if match:
            pins.setdefault(_normalize(match.group(1)), set()).add(match.group(2))
    return pins


def _version_drift(backend_versions, layer_versions):
    """(name, backend versions, layer versions) for each package both sides carry at different versions."""
    return sorted(
        (name, sorted(backend_versions[name]), sorted(layer_versions[name]))
        for name in set(backend_versions) & set(layer_versions)
        if backend_versions[name] != layer_versions[name]
    )


@pytest.mark.unit
class TestBaseLayerDependencyParity:
    def test_the_locks_are_read(self):
        """Non-vacuous: both locks parse and carry the authorization, model and SDK packages. A
        missing name means a path or the lock format changed, and the parity checks below would
        then compare nothing."""
        backend = _locked_versions(BACKEND_LOCK)
        layer = _locked_versions(BASE_LAYER_LOCK)
        for name in ('casbin', 'simpleeval', 'pydantic', 'boto3', 'aws-lambda-powertools'):
            assert name in backend, f"{name} is not in {BACKEND_LOCK}"
            assert name in layer, f"{name} is not in {BASE_LAYER_LOCK}"
        assert len(set(backend) & set(layer)) >= 15, (
            f"only {len(set(backend) & set(layer))} packages are shared by the two locks")

    def test_the_comparison_reports_a_drifted_pin(self, tmp_path):
        """Positive control: two locks that disagree on one shared package report exactly that
        package. A name spelled with `_` on one side is the same package, and a package only one
        side carries is not a drift."""
        (tmp_path / 'backend.lock').write_text(
            '[[package]]\nname = "casbin"\nversion = "1.33.0"\n\n'
            '[[package]]\nname = "typing_extensions"\nversion = "4.15.0"\n\n'
            '[[package]]\nname = "moto"\nversion = "5.1.0"\n',
            encoding='utf-8')
        (tmp_path / 'layer.lock').write_text(
            '[[package]]\nname = "casbin"\nversion = "1.36.0"\n\n'
            '[[package]]\nname = "typing-extensions"\nversion = "4.15.0"\n',
            encoding='utf-8')
        drift = _version_drift(_locked_versions(tmp_path / 'backend.lock'),
                               _locked_versions(tmp_path / 'layer.lock'))
        assert drift == [('casbin', ['1.33.0'], ['1.36.0'])]

    def test_shared_packages_are_locked_at_the_same_version(self):
        drift = _version_drift(_locked_versions(BACKEND_LOCK), _locked_versions(BASE_LAYER_LOCK))
        assert not drift, (
            "backend/poetry.lock and backend/lambdaLayers/base/poetry.lock lock these packages at "
            f"different versions, as (name, backend, layer): {drift}. Re-lock both trees to the same "
            "version (backend/CLAUDE.md 'Updating Python Dependencies').")

    @pytest.mark.parametrize('requirements_file, lock_file', [
        ('requirements.txt', 'poetry.lock'),
        ('requirements-dev.txt', 'poetry.lock'),
        ('lambdaLayers/base/requirements.txt', 'lambdaLayers/base/poetry.lock'),
    ])
    def test_exported_requirements_match_their_lock(self, requirements_file, lock_file):
        """The suite installs `requirements-dev.txt`, not the lock, so the lock comparison above
        describes the suite only while each exported file matches its lock."""
        pins = _pinned_versions(BACKEND_DIR / requirements_file)
        assert pins, f"no pins read from {requirements_file}"
        locked = _locked_versions(BACKEND_DIR / lock_file)
        mismatched = sorted(
            (name, sorted(versions), sorted(locked.get(name, set())))
            for name, versions in pins.items()
            if not versions <= locked.get(name, set())
        )
        assert not mismatched, (
            f"{requirements_file} disagrees with {lock_file}, as (name, exported, locked): "
            f"{mismatched}. Re-export it with poetry export.")
