#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Each identifier field is validated by the rule that matches the shape it actually carries.

VAMS has several ID shapes and one `validate()` dispatcher, so the failure mode is a field validated by
a rule meant for a different shape. That is invisible in normal use — a well-formed value passes every
candidate rule — and only shows up as either an over-permissive guard (a non-identifier reaching a
DynamoDB key or an S3 prefix builder) or an over-strict one (a legitimate identifier rejected by one
endpoint while every other endpoint accepts it).

The shapes:
  GUID      undashed 32-hex OR    execution ids. The undashed form comes from
            dashed 8-4-4-4-12     executionRecords.new_guid() (uuid4().hex); the dashed form is the
                                  uuid Step Functions assigns as the execution name when
                                  StartExecution is called without one, which an execution row then
                                  keeps for its whole life.
  UUID      dashed 8-4-4-4-12     externally-issued ids such as an API key id. A .hex value does NOT
                                  match this, which is why GUID is a separate rule rather than reuse.
  ID        `^[-_a-zA-Z0-9]{3,63}$`  authored slugs: databaseId, pipelineId, workflowId, and the
                                  caller-authored executionGroupId label.
  ASSET_ID  filename_pattern      caller-nameable asset ids, up to 256 chars, dots and spaces allowed.

These tests pin the SELECTION, not just the regexes: a rule swapped back on a field fails here.
"""
import pytest

from common.validators import validate, execution_id_pattern


REAL_GUID = "fd79afb2f12d4a10809c78c98007da91"          # a real execution id from a live deployment
DASHED_EXECUTION_ID = "fd79afb2-f12d-4a10-809c-78c98007da91"  # a Step Functions-named execution id
REAL_ASSET_ID = "xddcc84a4-b1c6-46a4-82d3-3568448b3a92"  # a real assetId (37 chars, dashed)


def _ok(field, value, rule, **extra):
    return validate({field: {"value": value, "validator": rule, **extra}})


@pytest.mark.unit
class TestGuidRule:
    """The GUID rule accepts both shapes a stored execution id can carry, and nothing else."""

    def test_a_real_execution_id_is_accepted(self):
        valid, message = _ok("executionId", REAL_GUID, "GUID")
        assert valid, message

    def test_a_dashed_execution_id_is_accepted(self):
        # An execution started without an explicit name is named by Step Functions with a dashed
        # uuid, and the row keeps that id for its whole life — so details, logs, abort, rerun and
        # permanent delete all have to accept it.
        valid, message = _ok("executionId", DASHED_EXECUTION_ID, "GUID")
        assert valid, message

    @pytest.mark.parametrize("value,why", [
        ("execution id with spaces", "spaces are not hex"),
        ("it's,a,test", "punctuation is not hex"),
        ("FD79AFB2F12D4A10809C78C98007DA91", "uppercase undashed: .hex emits lowercase, and the "
                                             "value is compared as an exact DynamoDB key, so an "
                                             "uppercase variant would match nothing"),
        ("fd79afb2f12d4a10809c78c98007da9", "31 chars, one short"),
        ("fd79afb2f12d4a10809c78c98007da911", "33 chars, one long"),
        ("fd79afb2-f12d-4a10-809c-78c98007da9", "dashed form, final group one short"),
        ("fd79afb2-f12d-4a10-809c-78c98007da911", "dashed form, final group one long"),
        ("fd79afb2-f12d-4a10-809c78c98007da91", "dashed form with a dash missing"),
        ("fd79afb2_f12d_4a10_809c_78c98007da91", "underscores in place of the dashes"),
        ("../../etc/passwd", "path traversal"),
        ("a/b", "a path separator"),
        ("", "empty"),
    ])
    def test_a_non_execution_id_is_rejected(self, value, why):
        valid, _ = _ok("executionId", value, "GUID")
        assert not valid, f"GUID rule should reject {value!r} ({why})"

    def test_the_rule_is_anchored_at_both_ends(self):
        # An unanchored or prefix-only rule would accept an id with a suffix appended, which is how a
        # separator could reach an S3 prefix builder.
        for base in (REAL_GUID, DASHED_EXECUTION_ID):
            for value in [base + "/x", "x" + base, base + " "]:
                valid, _ = _ok("executionId", value, "GUID")
                assert not valid, f"{value!r} must not pass an anchored GUID rule"

    def test_the_pattern_constant_holds_both_shapes(self):
        # Pinned literally: a future edit dropping either alternative, or an anchor, fails here as
        # well as in the behavioral tests above.
        assert execution_id_pattern == (
            r'^(?:[0-9a-f]{32}'
            r'|[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})$')


@pytest.mark.unit
class TestExecutionIdUsesTheGuidRule:
    """executionId arrives from the URL path on 6 executionService routes.

    Previously validated with ASSET_ID (filename_pattern), which admits spaces, commas, apostrophes and
    control characters for a value that is always 32 hex. Traversal was blocked either way — ASSET_ID
    rejects '/' and '\\' — so this is input hygiene and defense-in-depth, not a fixed exploit.
    """

    def test_the_old_asset_id_rule_would_have_accepted_a_non_guid(self):
        # The regression this fix removes: documents WHY the rule was wrong, so the change is not
        # silently reverted as cosmetic.
        valid, _ = _ok("executionId", "execution id with spaces", "ASSET_ID")
        assert valid, ("ASSET_ID is expected to accept this non-GUID value — that permissiveness is "
                       "the reason executionId must not use it")

    def test_and_the_guid_rule_does_not(self):
        valid, _ = _ok("executionId", "execution id with spaces", "GUID")
        assert not valid


@pytest.mark.unit
class TestAssetIdRuleBreadth:
    """assetId is caller-nameable up to 256 chars (models/assetsV3 max_length=256), so ASSET_ID — not
    the 3-63 slug rule — is the correct rule for it.

    The comment handlers used the narrow ID rule, which rejects a dotted or long assetId that every
    other endpoint accepts. commentService.py already used ASSET_ID at one of its own call sites, so the
    file disagreed with itself; these pin the resolved direction.
    """

    def test_a_real_asset_id_passes_asset_id(self):
        valid, message = _ok("assetId", REAL_ASSET_ID, "ASSET_ID")
        assert valid, message

    @pytest.mark.parametrize("value", ["my.asset.v2", "cube.obj", "a" * 200])
    def test_asset_ids_the_narrow_id_rule_would_reject_are_accepted(self, value):
        assert _ok("assetId", value, "ASSET_ID")[0], f"{value!r} is a legitimate assetId"
        assert not _ok("assetId", value, "ID")[0], (
            f"{value!r} must FAIL the narrow ID rule — that asymmetry is why the comment handlers "
            "had to be repointed")

    def test_asset_id_still_blocks_path_separators(self):
        # Breadth is not absence of a guard: ASSET_ID must keep rejecting traversal, since assetId is
        # interpolated into S3 keys.
        for value in ["../../etc/passwd", "a/b", "a\\b"]:
            assert not _ok("assetId", value, "ASSET_ID")[0], f"{value!r} must stay rejected"


@pytest.mark.unit
class TestCallSitesDeclareTheRightRule:
    """Source-level guard: the rule each handler DECLARES for each id field.

    The behavioral tests above prove each rule works; they cannot prove a handler still asks for it.
    A repointed call site is exactly how this class of defect appeared, so it is pinned by reading the
    source rather than by exercising a route (these routes need a full AWS event + Casbin to reach).
    """

    @staticmethod
    def _rules_for(path, field):
        import pathlib
        import re
        root = pathlib.Path(__file__).resolve().parents[2] / "backend"
        text = (root / path).read_text(encoding="utf-8")
        pattern = (r"""['"]""" + re.escape(field) + r"""['"]\s*:\s*\{[^{}]*?"""
                   r"""['"]validator['"]\s*:\s*['"](\w+)['"]""")
        return re.findall(pattern, text, re.S)

    def test_every_execution_service_execution_id_site_uses_guid(self):
        rules = self._rules_for("handlers/workflows/executionService.py", "executionId")
        assert rules, "expected executionId validate() sites in executionService.py"
        assert set(rules) == {"GUID"}, f"executionId must be validated as a GUID everywhere; got {rules}"
        assert len(rules) == 6, f"expected 6 executionId sites, found {len(rules)} — audit the new one"

    @pytest.mark.parametrize("path", [
        "handlers/comments/addComment.py",
        "handlers/comments/commentService.py",
        "handlers/comments/editComment.py",
    ])
    def test_comment_handlers_validate_asset_id_as_asset_id(self, path):
        rules = self._rules_for(path, "assetId")
        assert rules, f"expected assetId validate() sites in {path}"
        assert set(rules) == {"ASSET_ID"}, (
            f"{path} must use ASSET_ID for assetId (the ID rule rejects a dotted or >63-char assetId "
            f"that the rest of VAMS accepts); got {rules}")
