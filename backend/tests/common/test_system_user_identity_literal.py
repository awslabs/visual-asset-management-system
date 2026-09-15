# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""SYSTEM_USER is the only system identity spelled anywhere in the backend.

Handlers compare a user id against the literal string "SYSTEM_USER" (the metadata
schema-validation bypass, the pipeline-execution authorization bypass), and the id is
seeded into the user and user-roles tables at deploy time and assigned to the admin role.
A second spelling therefore names an identity that exists in neither table: it is
attributed to nothing in the permissions editor and in every audit record, and it fails
closed with no obvious cause the moment the value reaches a comparison or a Casbin check.

The behavioural tests beside each handler prove the identity that handler uses; they
cannot prove that a NEW system-originated path does not invent its own spelling, which is
what this walk covers. It looks at STRING LITERALS only -- an identifier such as
SYNC_SYSTEM_TYPE or SYSTEM_CONFIG_KEYS is an ordinary constant name and is not an
identity.
"""

import pathlib
import re

import pytest

BACKEND_ROOT = pathlib.Path(__file__).resolve().parents[2] / 'backend'

# A quoted SYSTEM_-prefixed value, which is the shape a system identity takes.
SYSTEM_LITERAL = re.compile(r"""['"](SYSTEM_[A-Za-z0-9_]+)['"]""")


@pytest.mark.unit
class TestSystemIdentityLiteral:
    @staticmethod
    def _system_literals(root=None):
        """Every quoted SYSTEM_* value under root, as (file:line, value) pairs.

        A pair per occurrence rather than per line, so a line carrying two literals reports
        both: under a line key the survivor can be the compliant spelling, which would hide
        the other one."""
        root = root or BACKEND_ROOT
        found = []
        for path in sorted(root.rglob('*.py')):
            for lineno, line in enumerate(
                    path.read_text(encoding='utf-8').splitlines(), start=1):
                site = f"{path.relative_to(root).as_posix()}:{lineno}"
                found.extend((site, value) for value in SYSTEM_LITERAL.findall(line))
        return found

    def test_the_walk_finds_the_sites(self):
        """Non-vacuous: SYSTEM_USER is written across the asset, metadata, workflow and
        auth modules. A count this far below the real one means the walk found the wrong
        tree or the pattern stopped matching."""
        literals = self._system_literals()
        assert len(literals) >= 40, f"expected the SYSTEM_USER sites, found {len(literals)}"

    def test_the_walk_reports_a_second_spelling(self, tmp_path):
        """Positive control: the walk over a tree that DOES carry another spelling reports it,
        so a clean result over the real tree means something. Both literals on the shared line
        are reported -- a per-line key would drop one, and the survivor can be the compliant
        spelling."""
        (tmp_path / 'handler.py').write_text(
            "actor = 'SYSTEM_ASYNC' if queued else 'SYSTEM_USER'\n"
            "identifier = SYSTEM_CONFIG_KEYS\n",
            encoding='utf-8')
        literals = self._system_literals(tmp_path)
        assert literals == [('handler.py:1', 'SYSTEM_ASYNC'),
                            ('handler.py:1', 'SYSTEM_USER')]

    def test_no_other_system_identity_is_spelled(self):
        others = [(site, value) for site, value in self._system_literals()
                  if value != 'SYSTEM_USER']
        assert not others, (
            "SYSTEM_USER is the only valid system identity; these sites spell another: "
            f"{others}")
