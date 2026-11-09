# Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Ledger core patches: unified diffs the engine applies to the fork source of every build and IAM plan.

A core patch is normally a commit in the fork, registered by path in ``core-patches.md``. Some fixes are better
kept out of the fork's history until upstream ships them (one example: the IamRoleTransform boundary fix of
GitHub issue #431, ``ledger-template/core-patches/iam-role-transform-boundary.patch``). For those, the ledger
holds the diff itself under ``core-patches/<name>.patch`` and ``core-patches.md`` lists it on a bullet line::

    -   `core-patches/iam-role-transform-boundary.patch` (CP-431, until upstream fixes GitHub issue #431)

Lines inside fenced code blocks are never read, so a template entry can be shown without activating it.

``vra build`` (step ``build.source``) and ``vra iam plan`` apply every listed patch to the scratch copy of the
ref, never to the fork clone. Per patch:

- it applies cleanly: applied, recorded in the build manifest (``corePatches``: file, sha256, status);
- its reverse applies cleanly: the tree already has the change (the fork committed it, or upstream shipped the
  same fix): ``present``, recorded, nothing changed;
- neither: the run stops with exit 20 (refresh the patch for this VAMS version, or retire it).

Whether a fix really took effect is checked on the synthesized templates, never on a version string (for the
boundary patch: ``vra.iam.check_assembly`` requires every ``AWS::IAM::Role`` to carry the boundary).
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

from vra import util
from vra.errors import NeedsHuman, VraError

PATCH_DIR = "core-patches"
_ENTRY = re.compile(r"`(" + PATCH_DIR + r"/[^`\s]+\.patch)`")
_DIFF_PATH = re.compile(r"^(?:---|\+\+\+) (?:[ab]/)?(\S+)", re.M)


def _register_lines(text: str):
    """Bullet and table lines of core-patches.md outside fenced code blocks."""
    fenced = False
    for line in text.splitlines():
        s = line.lstrip()
        if s.startswith(("```", "~~~")):
            fenced = not fenced
            continue
        if not fenced and s.startswith(("-", "*", "|")):
            yield line


def register_lines(ledger_root: Path) -> list[str]:
    p = Path(ledger_root) / "core-patches.md"
    return list(_register_lines(p.read_text(encoding="utf-8"))) if p.exists() else []


def listed(ledger_root: Path) -> list[str]:
    """Ledger-relative paths of the patch files core-patches.md lists (``core-patches/<name>.patch``), in order."""
    out: list[str] = []
    for line in register_lines(ledger_root):
        for rel in _ENTRY.findall(line):
            if rel not in out:
                out.append(rel)
    return out


def touched_paths(text: str) -> list[str]:
    """Repository paths a unified diff changes (from its ---/+++ headers; /dev/null skipped)."""
    out = []
    for p in _DIFF_PATH.findall(text):
        if p != "/dev/null" and p not in out:
            out.append(p)
    return out


def validate(ledger_root: Path) -> list[str]:
    """Errors for ``vra ledger validate``: every listed patch exists inside core-patches/ and is a unified diff of
    relative repository paths."""
    root = Path(ledger_root)
    errors = []
    for rel in listed(root):
        p = (root / rel).resolve()
        if root.resolve() / PATCH_DIR not in p.parents:
            errors.append(f"core-patches.md: {rel} is outside {PATCH_DIR}/")
            continue
        if not p.is_file():
            errors.append(f"core-patches.md lists {rel}, which does not exist in the ledger")
            continue
        text = p.read_text(encoding="utf-8", errors="replace")
        paths = touched_paths(text)
        if not paths or "\n@@ " not in "\n" + text:
            errors.append(f"{rel}: not a unified diff (no ---/+++ headers or @@ hunks)")
        for t in paths:
            if t.startswith("/") or ".." in Path(t).parts or Path(t).parts[:1] == (".git",):
                errors.append(f"{rel}: changes {t!r}; a core patch may only change relative paths of the fork")
    return errors


def _git_apply(src: Path, patch: Path, *extra: str) -> subprocess.CompletedProcess:
    # never let git find an enclosing repository: inside one, `git apply` silently skips paths outside the cwd
    env = {**os.environ, "GIT_CEILING_DIRECTORIES": str(Path(src).resolve().parent)}
    env.pop("GIT_DIR", None)
    env.pop("GIT_WORK_TREE", None)
    return subprocess.run(["git", "apply", "--whitespace=nowarn", *extra, str(patch)], cwd=str(src), env=env,
                          capture_output=True, text=True)


def apply_all(ledger_root: Path, src: Path, log=None, where: str = "build") -> list[dict]:
    """Apply every listed patch to ``src`` (a scratch copy of the fork at the built commit). Returns one record per
    patch: ``{file, sha256, status: applied|present, paths}``. Raises NeedsHuman when a patch neither applies nor
    is already present."""
    root = Path(ledger_root)
    errors = validate(root)
    if errors:
        raise NeedsHuman("core patches in the ledger are invalid: " + "; ".join(errors), {"errors": errors})
    out = []
    for rel in listed(root):
        p = root / rel
        rec = {"file": rel, "sha256": util.sha256_file(p),
               "paths": touched_paths(p.read_text(encoding="utf-8", errors="replace"))}
        check = _git_apply(src, p, "--check")
        if check.returncode == 0:
            done = _git_apply(src, p)
            if done.returncode != 0:
                raise VraError(f"core patch {rel} passed --check but did not apply: {done.stderr.strip()[:300]}")
            rec["status"] = "applied"
        elif _git_apply(src, p, "--reverse", "--check").returncode == 0:
            rec["status"] = "present"
        else:
            raise NeedsHuman(
                f"core patch {rel} does not apply to the {where} source and is not already in it "
                f"({check.stderr.strip()[:300]}). Refresh the patch for this VAMS version (core-patches.md), or "
                f"remove its entry when upstream has fixed the problem", {"patch": rel, "paths": rec["paths"]})
        if log:
            log(f"core patch {rel}: {rec['status']} ({', '.join(rec['paths'])})")
        out.append(rec)
    return out


def digest(records: list[dict]) -> str:
    """One value for the listed patches (file + sha256), for bundle reuse checks."""
    return util.sha256_bytes("\n".join(f"{r['file']} {r['sha256']}" for r in records).encode())


def current(ledger_root: Path) -> list[dict]:
    """``{file, sha256}`` of the listed patches as the ledger holds them now (missing files skipped)."""
    root = Path(ledger_root)
    return [{"file": rel, "sha256": util.sha256_file(root / rel)} for rel in listed(root) if (root / rel).is_file()]


__all__ = ["PATCH_DIR", "apply_all", "current", "digest", "listed", "register_lines", "touched_paths", "validate"]
