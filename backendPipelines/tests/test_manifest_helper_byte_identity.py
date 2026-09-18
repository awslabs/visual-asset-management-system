#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Every vendored ``manifestHelper.py`` is byte-identical to the canonical copy.

The helper is vendored per pipeline because a pipeline's Lambda code asset cannot import the backend
package, and `backendPipelines/CLAUDE.md` requires the copies to stay identical: edit one, propagate to
the rest in the same change. The copies are discovered rather than listed, because a pipeline added later
is exactly the case the rule exists for; the count floor keeps a glob that stops matching from reading as
"all copies agree".

Durable (root CLAUDE.md Rule 13): a copy can drift again on any edit to any pipeline, so this is a
guardrail, not a pin on one past change.
"""

import glob
import hashlib
import os

import pytest

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_PIPELINES_DIR = os.path.join(_REPO_ROOT, "backendPipelines")

# The copy edits are made to; every other copy is refreshed from it with `cp`.
CANONICAL_COPY = os.path.normpath(os.path.join(
    _PIPELINES_DIR, "preview", "3dThumbnail", "lambda", "manifestHelper.py"))

# Fewer copies than this means the glob is not finding the tree, not that the tree shrank.
MIN_EXPECTED_COPIES = 10


def _copies():
    pattern = os.path.join(_PIPELINES_DIR, "**", "lambda", "manifestHelper.py")
    return sorted(os.path.normpath(p) for p in glob.glob(pattern, recursive=True)
                  if "__pycache__" not in p)


def _digest(path):
    with open(path, "rb") as handle:
        return hashlib.md5(handle.read(), usedforsecurity=False).hexdigest()


def _rel(path):
    return os.path.relpath(path, _REPO_ROOT).replace("\\", "/")


@pytest.mark.unit
class TestManifestHelperCopiesAreByteIdentical:
    def test_the_glob_finds_the_vendored_copies(self):
        """Control for the identity assertion: an empty or short set would satisfy it trivially."""
        copies = _copies()
        assert len(copies) >= MIN_EXPECTED_COPIES, [_rel(c) for c in copies]
        assert CANONICAL_COPY in copies

    def test_every_copy_matches_the_canonical_copy(self):
        canonical = _digest(CANONICAL_COPY)
        drifted = {_rel(c): _digest(c) for c in _copies() if _digest(c) != canonical}
        assert drifted == {}, (
            "manifestHelper.py copies differ from the canonical copy; refresh them with\n"
            "  for f in $(find backendPipelines -name manifestHelper.py); do "
            f"cp {_rel(CANONICAL_COPY)} \"$f\"; done\n"
            f"drifted: {drifted}")
