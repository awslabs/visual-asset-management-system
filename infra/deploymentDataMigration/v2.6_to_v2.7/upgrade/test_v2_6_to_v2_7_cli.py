# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for the v2.6 -> v2.7 migration's command line: the step choices, the release flags, the
destructive gate, and the exit-code folding of main().

Run: python -m pytest test_v2_6_to_v2_7_cli.py -q
"""

import importlib.util
import json
import os

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))


def _load_migration_module():
    spec = importlib.util.spec_from_file_location(
        "v26_to_v27_migration", os.path.join(_HERE, "v2.6_to_v2.7_migration.py")
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


mig = _load_migration_module()


def _write_config(tmp_path, **extra):
    values = {"resource_names_ssm_param_prefix": "/vams-x/resourceNames"}
    values.update(extra)
    path = tmp_path / "cfg.json"
    path.write_text(json.dumps(values))
    return str(path)


def test_parser_steps_choices_are_the_release_steps():
    parser = mig.build_parser()
    steps = next(action for action in parser._actions if action.dest == "steps")
    assert tuple(steps.choices) == (
        "orphanedTriggers",
        "vectorBackfill",
        "systemPipelineRetirement",
        "all",
    )
    assert steps.default == "all"
    assert mig.STEP_CHOICES == tuple(steps.choices)


def test_parser_accepts_the_release_flags():
    namespace = mig.build_parser().parse_args(
        [
            "--config", "c.json", "--steps", "vectorBackfill", "--clear-vectors", "--async",
            "--dry-run", "--limit", "5", "--profile", "p", "--region", "us-west-2",
            "--log-level", "DEBUG", "--confirm-account", "123456789012", "--yes",
        ]
    )
    assert namespace.config == "c.json"
    assert namespace.steps == "vectorBackfill"
    assert namespace.clear_vectors is True
    assert namespace.async_invoke is True
    assert namespace.dry_run is True
    assert namespace.limit == 5
    assert namespace.profile == "p"
    assert namespace.region == "us-west-2"
    assert namespace.log_level == "DEBUG"
    assert namespace.confirm_account == "123456789012"
    assert namespace.yes is True


def test_parser_defaults_leave_every_switch_off():
    namespace = mig.build_parser().parse_args(["--config", "c.json"])
    assert namespace.steps == "all"
    assert namespace.clear_vectors is False
    assert namespace.async_invoke is False
    assert namespace.dry_run is False
    assert namespace.limit is None
    assert namespace.log_level == "INFO"


@pytest.mark.parametrize("flag", ["--clear-indexes", "--operation"])
def test_parser_rejects_the_v26_only_flags(flag):
    with pytest.raises(SystemExit):
        mig.build_parser().parse_args(["--config", "c.json", flag, "both"])


@pytest.mark.parametrize(
    "argv,expected",
    [
        (["--steps", "orphanedTriggers"], True),
        (["--steps", "systemPipelineRetirement"], False),
        (["--steps", "vectorBackfill"], False),
        (["--steps", "vectorBackfill", "--clear-vectors"], True),
        (["--steps", "all"], True),
    ],
)
def test_main_marks_the_run_destructive_only_when_it_deletes(monkeypatch, tmp_path, argv, expected):
    seen = {}

    def fake_confirm(**kwargs):
        seen.update(kwargs)
        return False

    monkeypatch.setattr(mig, "confirm_migration_target", fake_confirm)

    rc = mig.main(["--config", _write_config(tmp_path)] + argv)

    # The refused confirmation stops the run before any step; that is the only thing that ran.
    assert rc == 1
    assert seen["destructive"] is expected
    assert seen["base_param_prefix"] == "/vams-x/resourceNames"
    assert seen["dry_run"] is False


def test_main_destructive_summary_names_what_would_be_deleted(monkeypatch, tmp_path):
    seen = {}

    def fake_confirm(**kwargs):
        seen.update(kwargs)
        return False

    monkeypatch.setattr(mig, "confirm_migration_target", fake_confirm)

    mig.main(["--config", _write_config(tmp_path), "--clear-vectors"])

    assert "orphanedTriggers" in seen["destructive_summary"]
    assert "--clear-vectors" in seen["destructive_summary"]
    assert mig._destructive_summary(False, False) == "nothing"


def test_main_treats_a_placeholder_prefix_as_unset(monkeypatch, tmp_path):
    seen = {}

    def fake_confirm(**kwargs):
        seen.update(kwargs)
        return False

    monkeypatch.setattr(mig, "confirm_migration_target", fake_confirm)
    config = _write_config(
        tmp_path, resource_names_ssm_param_prefix="<RESOURCE_NAMES_SSM_PARAM_PREFIX>"
    )

    mig.main(["--config", config])

    assert seen["base_param_prefix"] is None


def test_main_config_flags_apply_when_the_cli_flags_are_absent(monkeypatch, tmp_path):
    seen = {}

    def fake_confirm(**kwargs):
        seen.update(kwargs)
        return False

    monkeypatch.setattr(mig, "confirm_migration_target", fake_confirm)
    config = _write_config(tmp_path, dry_run=True, clear_vectors=True)

    mig.main(["--config", config, "--steps", "vectorBackfill"])

    assert seen["dry_run"] is True
    # clear_vectors from the config makes the vectorBackfill step destructive too.
    assert seen["destructive"] is True


def test_main_runs_the_steps_in_order_and_folds_exit_codes(monkeypatch, tmp_path):
    order = []
    monkeypatch.setattr(mig, "confirm_migration_target", lambda **kwargs: True)
    monkeypatch.setattr(
        mig, "run_orphaned_triggers_step", lambda *a, **k: order.append("orphanedTriggers") or 0
    )

    def vector(*args, **kwargs):
        order.append("vectorBackfill")
        return 1

    monkeypatch.setattr(mig, "run_vector_backfill_step", vector)
    monkeypatch.setattr(
        mig,
        "run_system_pipeline_retirement_step",
        lambda *a, **k: order.append("systemPipelineRetirement") or 0,
    )

    rc = mig.main(["--config", _write_config(tmp_path)])

    assert rc == 1
    assert order == ["orphanedTriggers", "vectorBackfill", "systemPipelineRetirement"]


def test_main_runs_only_the_named_step(monkeypatch, tmp_path):
    order = []
    monkeypatch.setattr(mig, "confirm_migration_target", lambda **kwargs: True)
    monkeypatch.setattr(
        mig, "run_orphaned_triggers_step", lambda *a, **k: order.append("orphanedTriggers") or 0
    )
    monkeypatch.setattr(
        mig, "run_vector_backfill_step", lambda *a, **k: order.append("vectorBackfill") or 0
    )
    monkeypatch.setattr(
        mig,
        "run_system_pipeline_retirement_step",
        lambda *a, **k: order.append("systemPipelineRetirement") or 0,
    )

    rc = mig.main(["--config", _write_config(tmp_path), "--steps", "systemPipelineRetirement"])

    assert rc == 0
    assert order == ["systemPipelineRetirement"]


def test_main_passes_explicit_only_when_vector_backfill_is_named(monkeypatch, tmp_path):
    seen = []
    monkeypatch.setattr(mig, "confirm_migration_target", lambda **kwargs: True)
    monkeypatch.setattr(mig, "run_orphaned_triggers_step", lambda *a, **k: 0)
    monkeypatch.setattr(mig, "run_system_pipeline_retirement_step", lambda *a, **k: 0)
    monkeypatch.setattr(mig, "run_vector_backfill_step", lambda *a, **k: seen.append(k) or 0)
    config = _write_config(tmp_path)

    mig.main(["--config", config, "--steps", "vectorBackfill", "--clear-vectors"])
    mig.main(["--config", config])

    assert seen == [
        {"clear_vectors": True, "explicit": True},
        {"clear_vectors": False, "explicit": False},
    ]
