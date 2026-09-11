#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""The checkpoint_db patch in `entrypoint.sh` is explained by what it does, not by a deadlock it cannot fix.

The patch adds `stderr=subprocess.DEVNULL` to the second `uvx hf download --quiet` call inside
`subprocess.check_output`. `check_output` pipes stdout only and drains it through `communicate()`, and
stderr is inherited, so there is no pipe for hf-cli output to fill and no `wait()` for the parent to
block in. `backendPipelines/CLAUDE.md` records the measurement and forbids restating the claim, and
`genAi/nvidia/tests/test_inference_output_capture.py` pins it in both directions.

The claim is still writable -- it was written above this patch and again above the transfer container's
copy -- which is why this is a standing guard rather than a pin of one edit. The comment sits directly
above upstream-source surgery, so whoever next touches that surgery reasons from whatever it says.

The subject is the contiguous comment block immediately above the `CHECKPOINT_DB=` assignment. Reading
that block rather than the whole file keeps the xet `futex_wait` note higher up -- a real deadlock, in a
different component -- out of the assertion.
"""

import re
from pathlib import Path

CONTAINER_DIR = Path(__file__).resolve().parents[1]
ENTRYPOINT = CONTAINER_DIR / "entrypoint.sh"
PATCH_ANCHOR = "CHECKPOINT_DB="

# Each is a fragment of the refuted mechanism as it was once written, not a bare word: "deadlock" on its
# own also occurs in the correct wording ("NOT a deadlock fix") and in the genuine xet note.
REFUTED_MECHANISM = (
    r"pipe buffer",
    r"blocks? in wait\(\)",
    r"so the pipe never fills",
)


def entrypoint_text():
    return ENTRYPOINT.read_text(encoding="utf-8")


def checkpoint_db_comment():
    """The comment lines directly above the patch's `CHECKPOINT_DB=` assignment, joined with spaces."""
    lines = entrypoint_text().splitlines()
    anchor = next(i for i, line in enumerate(lines) if line.startswith(PATCH_ANCHOR))
    block = []
    for line in reversed(lines[:anchor]):
        if not line.startswith("#"):
            break
        block.append(line.lstrip("#").strip())
    assert block, "no comment sits above the checkpoint_db patch; this test has no subject"
    return " ".join(reversed(block))


def test_the_comment_is_about_the_checkpoint_db_patch():
    """Positive control: the block the other tests read names the function and the call it patches, so
    an absence asserted against it is an absence from the right comment."""
    comment = checkpoint_db_comment()
    assert "checkpoint_db._hf_download" in comment and "check_output" in comment, comment


def test_the_comment_does_not_restate_the_pipe_buffer_deadlock():
    comment = checkpoint_db_comment()
    for claim in REFUTED_MECHANISM:
        assert not re.search(claim, comment, re.IGNORECASE), (
            f"the checkpoint_db comment restates the refuted mechanism ({claim!r}); "
            "backendPipelines/CLAUDE.md forbids it")


def test_the_comment_says_what_the_patch_does_and_where_the_rule_lives():
    comment = checkpoint_db_comment()
    assert "progress output" in comment, comment
    assert "NOT a deadlock fix" in comment, comment
    assert "backendPipelines/CLAUDE.md" in comment, comment


def test_the_patch_itself_is_unchanged():
    """Rewording the rationale must not touch the surgery: the redirect and its marker stay."""
    text = entrypoint_text()
    assert "stderr=subprocess.DEVNULL" in text
    assert "# VAMS_PATCHED_CHECKOUTPUT" in text
