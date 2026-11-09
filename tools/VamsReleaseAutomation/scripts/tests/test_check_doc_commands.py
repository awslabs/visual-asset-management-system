# Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""The doc-command check knows the command groups (ledger, history, home) and their flags."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1]


def _load():
    spec = importlib.util.spec_from_file_location("check_doc_commands", SCRIPTS / "check_doc_commands.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["check_doc_commands"] = mod
    spec.loader.exec_module(mod)
    return mod


docs = _load()


def _problems(tmp_path: Path, text: str) -> list[str]:
    pkg = tmp_path / "tools/VamsReleaseAutomation"
    pkg.mkdir(parents=True)
    (pkg / "page.md").write_text(text)
    return docs.check(pkg)


def test_group_subcommands_and_their_flags_are_known(tmp_path):
    text = ("Run `vra history show --env prod`, `vra history verify --env prod`, `vra home sync`, "
            "`vra home migrate --from a --old-work-dir b --to c --sync git` and `vra ledger graph`.\n")
    assert _problems(tmp_path, text) == []


def test_unknown_group_subcommand_and_flag_are_reported(tmp_path):
    probs = _problems(tmp_path, "Run `vra history erase --env prod` and `vra home show --force`.\n")
    assert any("unknown subcommand" in p and "history erase" in p for p in probs)
    assert any("`vra home show` has no flag --force" in p for p in probs)
