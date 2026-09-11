# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""A DISABLED trigger must not block a template change it could never be broken by.

A template used as a trigger's default may not carry a required tag with no default value, because a
headless run has nobody to supply it. That guard is right — but it consulted every trigger row
regardless of `enabled`, and a disabled trigger cannot fire, so it cannot be broken.

Measured, on a live deployment: a `fileUpload` trigger created weeks earlier and left
`enabled: False` held `isaaclab-evaluation-cartpole` as its default, and refused a correct edit to that
template. The edit made `CHECKPOINT_PATH` required, without which a defaults-only evaluation renders
`checkpointPath: ""` and the container exits 1 -- after AWS Batch has provisioned a GPU and pulled a
multi-gigabyte image. So an inert row was preventing a fix to a real failure, and the registration
would have been refused at deploy time with the fix silently not landing.

**The check is relocated, not removed**, which is what makes this safe. Saving a trigger validates its
chosen default templates (`validate_trigger_default_templates`, called from
`workflowTriggerService.set_trigger`), so re-enabling a trigger whose template has since gained a
required tag is refused at that point -- where the operator is acting on the trigger and can act on the
message. Both halves are asserted here, because dropping the guard entirely would look identical to
relocating it.
"""

from unittest.mock import MagicMock

import pytest

from backend.backend.common.workflows.triggerTemplateValidation import (
    triggers_referencing_template,
    validate_template_not_breaking_triggers,
    validate_trigger_default_templates,
)

PIPELINE_DB = "GLOBAL"
PIPELINE_ID = "isaaclab-evaluation"
TEMPLATE_ID = "isaaclab-evaluation-cartpole"
COMPOSITE = f"{PIPELINE_DB}:{PIPELINE_ID}"

# A required tag with no default -- the shape the guard exists to catch.
REQUIRED_NO_DEFAULT = [{"tagKey": "CHECKPOINT_PATH", "type": "string", "required": True}]


def _trigger_row(enabled):
    row = {
        "workflowDatabaseId": "GLOBAL",
        "workflowId": PIPELINE_ID,
        "triggerType": "fileUpload",
        "triggerConfig": {"defaultTemplateIds": {COMPOSITE: TEMPLATE_ID}},
    }
    if enabled is not None:
        row["enabled"] = enabled
    return row


def _table(rows):
    """A triggers table whose GSI query returns `rows` for the first trigger type and nothing after."""
    table = MagicMock()
    calls = {"n": 0}

    def query(**kwargs):
        calls["n"] += 1
        # Only the first trigger type yields rows; every query ends its walk (no LastEvaluatedKey).
        return {"Items": rows if calls["n"] == 1 else []}

    table.query.side_effect = query
    return table


@pytest.mark.unit
class TestDisabledTriggersAreIgnored:
    def test_an_enabled_trigger_still_blocks_the_template_change(self):
        """The guard itself. Asserted FIRST because the fix must not weaken it."""
        errors = validate_template_not_breaking_triggers(
            _table([_trigger_row(enabled=True)]), PIPELINE_DB, PIPELINE_ID, TEMPLATE_ID,
            REQUIRED_NO_DEFAULT)
        assert errors, "an enabled trigger's default template must still refuse a required-no-default tag"
        assert "CHECKPOINT_PATH" in errors[0]

    def test_a_disabled_trigger_does_not_block_it(self):
        """The fix. A trigger that cannot fire cannot be broken."""
        errors = validate_template_not_breaking_triggers(
            _table([_trigger_row(enabled=False)]), PIPELINE_DB, PIPELINE_ID, TEMPLATE_ID,
            REQUIRED_NO_DEFAULT)
        assert errors == [], f"a disabled trigger must not block the change, got: {errors}"

    def test_a_row_with_no_enabled_key_is_treated_as_enabled(self):
        """Absent is not disabled.

        Older rows may predate the field, and the conservative direction for a guard is to keep
        blocking rather than to assume a missing flag means inert.
        """
        errors = validate_template_not_breaking_triggers(
            _table([_trigger_row(enabled=None)]), PIPELINE_DB, PIPELINE_ID, TEMPLATE_ID,
            REQUIRED_NO_DEFAULT)
        assert errors, "a row without an `enabled` key must be treated as enabled"

    def test_an_enabled_sibling_blocks_even_when_a_disabled_one_exists(self):
        """The mixed case, which a naive filter could get wrong by looking at only the first row."""
        errors = validate_template_not_breaking_triggers(
            _table([_trigger_row(enabled=False), _trigger_row(enabled=True)]),
            PIPELINE_DB, PIPELINE_ID, TEMPLATE_ID, REQUIRED_NO_DEFAULT)
        assert errors, "an enabled trigger must still block, even alongside a disabled one"

    def test_the_lookup_itself_omits_disabled_rows(self):
        """Asserted at the lookup as well as through the validator.

        `triggers_referencing_template` is used by more than one caller, so the property belongs to it
        rather than to the validator that happens to call it.
        """
        assert triggers_referencing_template(
            _table([_trigger_row(enabled=False)]), PIPELINE_DB, PIPELINE_ID, TEMPLATE_ID) == []
        assert triggers_referencing_template(
            _table([_trigger_row(enabled=True)]), PIPELINE_DB, PIPELINE_ID, TEMPLATE_ID) != []

    def test_a_template_with_no_required_tag_is_never_blocked(self):
        """Control on the other input: the tag schema, not the trigger, decides whether to look."""
        optional_only = [{"tagKey": "CHECKPOINT_PATH", "type": "string", "required": False}]
        assert validate_template_not_breaking_triggers(
            _table([_trigger_row(enabled=True)]), PIPELINE_DB, PIPELINE_ID, TEMPLATE_ID,
            optional_only) == []


@pytest.mark.unit
class TestTheCheckIsRelocatedNotRemoved:
    def test_saving_a_trigger_still_rejects_a_required_no_default_template(self):
        """The other half. Without this, skipping disabled rows would lose the guarantee entirely.

        This is the path a re-enable takes: `set_trigger` writes the whole trigger row and validates the
        default templates it names, so a trigger cannot be turned back on against a template that has
        since gained a required tag.
        """
        errors = validate_trigger_default_templates(
            {COMPOSITE: TEMPLATE_ID}, lambda db, pid, tid: REQUIRED_NO_DEFAULT)
        assert errors, "re-enabling must be refused while the template has a required-no-default tag"
        assert "CHECKPOINT_PATH" in errors[0]

    def test_saving_a_trigger_is_accepted_once_the_tag_carries_a_default(self):
        """Paired arm, so the arm above is not satisfied by a validator that rejects everything."""
        with_default = [{"tagKey": "CHECKPOINT_PATH", "type": "string", "required": True,
                         "default": "checkpoints/model_final.pt"}]
        assert validate_trigger_default_templates(
            {COMPOSITE: TEMPLATE_ID}, lambda db, pid, tid: with_default) == []
