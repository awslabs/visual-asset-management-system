# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Several triggers of one type on one workflow.

A workflow may carry more than one trigger of a type, each with its own input-file filters and default
templates, and an upload fires the workflow once per matching trigger.

The keying is what makes this work without touching the rows already deployed:

  - The FIRST trigger of a type keeps the bare type as its sort key — exactly what every row written
    before this existed holds — so those rows stay addressable and keep firing once.
  - An ADDITIONAL trigger suffixes an id: ``fileUpload#<triggerId>``. The sort-key attribute is still
    named ``triggerType`` because DynamoDB cannot rename a key attribute in place.
  - The bare type is carried separately in ``triggerBaseType`` for the dispatcher's by-type index, whose
    query is an exact match. A suffixed value there would put each additional trigger in its own
    partition and it would sit in the table and silently never fire.
"""

import json
from unittest.mock import MagicMock, patch

import pytest

from backend.backend.handlers.workflows.workflowTriggerService import lambda_handler

MOD = "backend.backend.handlers.workflows.workflowTriggerService"

BASE = "/database/db1/workflows/wflow1/triggers"
BARE = {"databaseId": "db1", "workflowId": "wflow1", "triggerType": "fileUpload"}
SUFFIXED = {"databaseId": "db1", "workflowId": "wflow1", "triggerType": "fileUpload#second"}


def _event(method, path_params, body=None):
    return {
        "requestContext": {"http": {"method": method, "path": f"{BASE}/x"}},
        "pathParameters": path_params,
        "queryStringParameters": None,
        "headers": {"authorization": "Bearer test-token"},
        "body": json.dumps(body) if body is not None else None,
    }


def _enforcer():
    inst = MagicMock()
    inst.enforceAPI.return_value = True
    inst.enforce.return_value = True
    return inst


def _sibling(template_ids):
    return {"triggerType": "fileUpload", "triggerBaseType": "fileUpload",
            "triggerConfig": {"defaultTemplateIds": template_ids}}


@pytest.mark.unit
class TestMultipleTriggersOfOneType:
    def _put(self, parent, enforcer, claims, params, body, siblings=(), restriction="none"):
        claims.return_value = {"tokens": ["u"]}
        enforcer.return_value = _enforcer()
        parent.return_value = (True, {"databaseId": "db1", "workflowId": "wflow1",
                                      "systemConfig": {"concurrencyRestriction": restriction}})
        with patch(f"{MOD}._same_type_triggers", return_value=list(siblings)), \
             patch(f"{MOD}.get_trigger", return_value=None), \
             patch(f"{MOD}._triggers_table") as table, \
             patch(f"{MOD}.validate_trigger_default_templates", return_value=[]), \
             patch(f"{MOD}.log_actions"):
            table.return_value = MagicMock()
            resp = lambda_handler(_event("PUT", params, body), MagicMock())
            written = table.return_value.put_item.call_args
        return resp, written

    @staticmethod
    def _item(written):
        return written.kwargs["Item"] if written and written.kwargs else written.args[0]

    @patch(f"{MOD}._enforce_parent_workflow")
    @patch(f"{MOD}.request_to_claims")
    @patch(f"{MOD}.CasbinEnforcer")
    def test_a_second_trigger_of_a_type_is_accepted(self, enf, claims, parent):
        resp, written = self._put(
            parent, enf, claims, SUFFIXED,
            {"inputFileFilters": {"allow": ["*.obj"]}, "defaultTemplateIds": {"GLOBAL:pipe1": "tmpl-b"}},
            siblings=[_sibling({"GLOBAL:pipe1": "tmpl-a"})])
        assert resp["statusCode"] == 200, resp["body"]
        item = self._item(written)
        assert item["triggerType"] == "fileUpload#second"
        # The GSI attribute must stay the BARE type or the dispatcher never finds this row.
        assert item["triggerBaseType"] == "fileUpload"
        assert item["triggerId"] == "second"

    @patch(f"{MOD}._enforce_parent_workflow")
    @patch(f"{MOD}.request_to_claims")
    @patch(f"{MOD}.CasbinEnforcer")
    def test_the_first_trigger_of_a_type_keeps_the_bare_key(self, enf, claims, parent):
        # A row written before multiple triggers existed is addressed and rewritten in place rather
        # than duplicated under a new key.
        resp, written = self._put(parent, enf, claims, BARE,
                                  {"inputFileFilters": {"allow": ["*.glb"]}})
        assert resp["statusCode"] == 200, resp["body"]
        item = self._item(written)
        assert item["triggerType"] == "fileUpload"
        assert item["triggerBaseType"] == "fileUpload"
        assert item["triggerId"] == ""

    @patch(f"{MOD}._enforce_parent_workflow")
    @patch(f"{MOD}.request_to_claims")
    @patch(f"{MOD}.CasbinEnforcer")
    def test_per_asset_concurrency_rejects_a_second_trigger(self, enf, claims, parent):
        # Several triggers fire the workflow once each, so a workflow that serializes runs per asset
        # would have them contend on that asset.
        resp, _ = self._put(parent, enf, claims, SUFFIXED,
                            {"defaultTemplateIds": {"GLOBAL:pipe1": "tmpl-b"}},
                            siblings=[_sibling({"GLOBAL:pipe1": "tmpl-a"})], restriction="perAsset")
        assert resp["statusCode"] == 400
        assert "per asset" in json.loads(resp["body"])["message"]

    @patch(f"{MOD}._enforce_parent_workflow")
    @patch(f"{MOD}.request_to_claims")
    @patch(f"{MOD}.CasbinEnforcer")
    def test_per_input_file_concurrency_still_allows_several(self, enf, claims, parent):
        # Deliberately NOT blocked: overlapping filters under perInputFile are caught by the
        # execution's own per-file check, which fails that trigger's execution rather than the save.
        resp, _ = self._put(parent, enf, claims, SUFFIXED,
                            {"defaultTemplateIds": {"GLOBAL:pipe1": "tmpl-b"}},
                            siblings=[_sibling({"GLOBAL:pipe1": "tmpl-a"})], restriction="perInputFile")
        assert resp["statusCode"] == 200, resp["body"]

    @patch(f"{MOD}._enforce_parent_workflow")
    @patch(f"{MOD}.request_to_claims")
    @patch(f"{MOD}.CasbinEnforcer")
    def test_the_same_default_templates_are_rejected_as_a_duplicate(self, enf, claims, parent):
        # The templates are what distinguish two triggers of one type; the same set twice is the same
        # trigger declared twice, whatever the filters say.
        resp, _ = self._put(parent, enf, claims, SUFFIXED,
                            {"defaultTemplateIds": {"GLOBAL:pipe1": "tmpl-a"}},
                            siblings=[_sibling({"GLOBAL:pipe1": "tmpl-a"})])
        assert resp["statusCode"] == 400
        assert "same default templates" in json.loads(resp["body"])["message"]

    @patch(f"{MOD}._enforce_parent_workflow")
    @patch(f"{MOD}.request_to_claims")
    @patch(f"{MOD}.CasbinEnforcer")
    def test_two_triggers_naming_no_templates_are_duplicates(self, enf, claims, parent):
        # Naming NO default template is a valid choice when no pipeline requires one, which makes "no
        # templates" a comparable value: two triggers of a type that both name none are the same
        # trigger twice, even with different filters.
        resp, _ = self._put(parent, enf, claims, SUFFIXED,
                            {"inputFileFilters": {"allow": ["*.obj"]}},
                            siblings=[_sibling({})])
        assert resp["statusCode"] == 400
        assert "same default templates" in json.loads(resp["body"])["message"]

    @patch(f"{MOD}._enforce_parent_workflow")
    @patch(f"{MOD}.request_to_claims")
    @patch(f"{MOD}.CasbinEnforcer")
    def test_a_malformed_trigger_id_is_rejected(self, enf, claims, parent):
        # The suite's shared `validate` stand-in answers True for everything, so it is replaced here
        # with the real ID rule. Without this the assertion would pass against a handler that never
        # validated the suffix at all.
        import re

        def _real_id_rule(spec):
            for name, entry in spec.items():
                if not re.match(r"^[-_a-zA-Z0-9]{3,63}$", str(entry.get("value") or "")):
                    return False, f"{name} is invalid"
            return True, ""

        claims.return_value = {"tokens": ["u"]}
        enf.return_value = _enforcer()
        parent.return_value = (True, {"databaseId": "db1", "workflowId": "wflow1"})
        with patch(f"{MOD}.validate", side_effect=_real_id_rule):
            bad = dict(SUFFIXED, triggerType="fileUpload#a b")
            assert lambda_handler(_event("GET", bad), MagicMock())["statusCode"] == 400
            # A well-formed suffix passes the check (it then 404s, having no stored row).
            ok = dict(SUFFIXED, triggerType="fileUpload#second")
            with patch(f"{MOD}.get_trigger", return_value=None):
                assert lambda_handler(_event("GET", ok), MagicMock())["statusCode"] == 404

    @patch(f"{MOD}._enforce_parent_workflow")
    @patch(f"{MOD}.request_to_claims")
    @patch(f"{MOD}.CasbinEnforcer")
    def test_a_percent_encoded_key_is_decoded_before_it_is_split(self, enf, claims, parent):
        """API Gateway hands pathParameters through PERCENT-ENCODED.

        A client MUST encode the '#' in a trigger key or the request never routes (a raw '#' is a URL
        fragment delimiter), so "fileUpload%23nightly" is what actually arrives. Caught live on the
        deployed stack: without decoding, the split found no '#', the whole value read as the base type,
        and every additional trigger was rejected with "Unsupported trigger type"."""
        resp, written = self._put(
            parent, enf, claims,
            dict(SUFFIXED, triggerType="fileUpload%23nightly"),
            {"inputFileFilters": {"allow": ["*.obj"]}, "defaultTemplateIds": {"GLOBAL:pipe1": "tmpl-b"}},
            siblings=[_sibling({"GLOBAL:pipe1": "tmpl-a"})])
        assert resp["statusCode"] == 200, resp["body"]
        item = self._item(written)
        assert item["triggerType"] == "fileUpload#nightly"
        assert item["triggerBaseType"] == "fileUpload"
        assert item["triggerId"] == "nightly"

    @patch(f"{MOD}._enforce_parent_workflow")
    @patch(f"{MOD}.request_to_claims")
    @patch(f"{MOD}.CasbinEnforcer")
    def test_an_unknown_base_type_is_still_rejected(self, enf, claims, parent):
        claims.return_value = {"tokens": ["u"]}
        enf.return_value = _enforcer()
        parent.return_value = (True, {"databaseId": "db1", "workflowId": "wflow1"})
        bad = dict(SUFFIXED, triggerType="notAType#x")
        assert lambda_handler(_event("GET", bad), MagicMock())["statusCode"] == 400


@pytest.mark.unit
class TestDispatchFansOutPerTrigger:
    """One upload launches the workflow once per matching trigger."""

    def _rows(self):
        from backend.backend.common.workflows import workflowRecords as wr
        return [
            wr.build_trigger_record("GLOBAL", "wf1", "fileUpload",
                                    {"inputFileFilters": {"allow": ["*.glb"], "exclude": []},
                                     "defaultTemplateIds": {"GLOBAL:pipe1": "tmpl-a"}}),
            wr.build_trigger_record("GLOBAL", "wf1", "fileUpload",
                                    {"inputFileFilters": {"allow": ["*.glb"], "exclude": []},
                                     "defaultTemplateIds": {"GLOBAL:pipe1": "tmpl-b"}},
                                    trigger_id="second"),
        ]

    def test_both_triggers_of_a_type_match_and_fire(self):
        from backend.backend.common.workflows import triggerMatching as tm
        matches = tm.match_fileupload_triggers(self._rows(), "db1", "a1", "/model.glb")
        # A suffixed row must NOT be filtered out by the type comparison.
        assert len(matches) == 2, matches
        templates = [body.get("pipelineParameters") or body for _, _, body in matches]
        assert len(templates) == 2

    def test_a_non_matching_upload_fires_nothing(self):
        from backend.backend.common.workflows import triggerMatching as tm
        assert tm.match_fileupload_triggers(self._rows(), "db1", "a1", "/notes.txt") == []

    def test_a_legacy_row_without_the_base_type_still_matches(self):
        # A row written before triggerBaseType existed carries only the bare type in its sort key; the
        # matcher falls back to splitting it rather than dropping the trigger.
        from backend.backend.common.workflows import triggerMatching as tm
        legacy = {"triggerType": "fileUpload", "workflowDatabaseId": "GLOBAL", "workflowId": "wf1",
                  "enabled": True,
                  "triggerConfig": {"inputFileFilters": {"allow": ["*.glb"], "exclude": []},
                                    "defaultTemplateIds": {}}}
        assert len(tm.match_fileupload_triggers([legacy], "db1", "a1", "/model.glb")) == 1
