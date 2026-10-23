# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Metadata rule scopes: the model's scope-bound fields, the entity type each scope binds the
referenced metadata schema to, and the engine's per-file and per-link evaluation -- which files and
links are in scope (the schema's fileKeyTypeRestriction, then includePaths, then excludePaths; link
direction and relationship type), "every unit must pass", the message / `measured` shapes that never
carry a pattern, and the tooling errors (missing or mismatched schema, missing input, the link
bound)."""

import pytest
from pydantic import ValidationError

from common.compliance import evaluationEngine as engine
from models.compliance import MetadataRule, MetadataScope, VamsRulesV1Schema
from models.metadataSchema import MetadataSchemaEntityType

REF = {"databaseId": "db1", "schemaName": "Model Schema"}
REQUIRE_UNITS = {"name": "units", "validateRequired": True}

FILES = [
    engine.CurrentFile("/models/part.glb", "v1"),
    engine.CurrentFile("/models/part.glb.previewFile.png", "v2"),
    engine.CurrentFile("/models/part.stl", "v3"),
    engine.CurrentFile("/drafts/old.glb", "v4"),
    engine.CurrentFile("/LICENSE", "v5"),
]


def _rule(scope="asset", checks=(REQUIRE_UNITS,), **extra):
    return MetadataRule(ruleType="metadata", enforcement="warn", metadataSchemaRef=REF,
                        scope=scope, checks=list(checks), **extra)


def _schema(scope, required=("units",), restriction=None):
    return {"fields": [{"field": name, "required": True, "type": "STRING"} for name in required],
            "entityType": engine.SCOPE_ENTITY_TYPE[MetadataScope(scope)],
            "fileKeyTypeRestriction": restriction}


def _evaluate(rule, **inputs):
    inputs.setdefault("schema", _schema(rule.scope))
    return engine.evaluate_metadata_rule("r", rule, engine.MetadataRuleInputs(**inputs))


def _files(rule, by_path=None, files=FILES, restriction=None):
    """A file-scope evaluation over `files`, the per-file rows `by_path`."""
    source = "file_metadata_by_path" if rule.scope == MetadataScope.files else \
        "file_attributes_by_path"
    return _evaluate(rule, schema=_schema(rule.scope, restriction=restriction),
                     current_files=files, **{source: by_path or {}})


@pytest.mark.unit
class TestTheModel:

    def test_a_rule_without_a_scope_is_an_asset_rule(self):
        rule = MetadataRule(ruleType="metadata", enforcement="warn", metadataSchemaRef=REF,
                            checks=[REQUIRE_UNITS])
        assert rule.scope == MetadataScope.asset
        assert rule.includePaths == [] and rule.excludePaths == []
        assert rule.linkDirection is None and rule.relationshipType is None

    def test_a_stored_body_without_a_scope_still_parses(self):
        body = VamsRulesV1Schema(schemaFormat="vams-rules-v1", rules={"m": {
            "ruleType": "metadata", "enforcement": "warn", "metadataSchemaRef": REF,
            "checks": [REQUIRE_UNITS]}})
        assert body.parse_rules()["m"].scope == MetadataScope.asset

    @pytest.mark.parametrize("scope", ["asset", "links"])
    def test_path_filters_are_refused_outside_the_file_scopes(self, scope):
        with pytest.raises(ValidationError) as raised:
            _rule(scope, includePaths=["^models/"])
        assert "apply to the files and fileAttributes scopes only" in str(raised.value)

    @pytest.mark.parametrize("scope", ["asset", "files", "fileAttributes"])
    @pytest.mark.parametrize("field", [{"linkDirection": "parents"},
                                       {"relationshipType": "related"}])
    def test_link_filters_are_refused_outside_the_links_scope(self, scope, field):
        with pytest.raises(ValidationError) as raised:
            _rule(scope, **field)
        assert "apply to the links scope only" in str(raised.value)

    def test_an_unknown_scope_or_direction_is_refused(self):
        with pytest.raises(ValidationError):
            _rule("everything")
        with pytest.raises(ValidationError):
            _rule("links", linkDirection="sideways")

    def test_path_patterns_go_through_the_regex_validator_and_the_message_names_the_field(self):
        with pytest.raises(ValidationError) as raised:
            _rule("files", includePaths=["(a+)+$"])
        message = str(raised.value)
        assert "includePaths" in message
        assert "(a+)+" not in message

    def test_patterns_are_trimmed_and_bounded(self):
        assert _rule("files", excludePaths=["  \\.tmp$ "]).excludePaths == ["\\.tmp$"]
        with pytest.raises(ValidationError):
            _rule("files", includePaths=["x"] * 26)

    def test_a_rule_carries_at_most_25_checks(self):
        _rule(checks=[dict(REQUIRE_UNITS, name=f"c{i}") for i in range(25)])
        with pytest.raises(ValidationError):
            _rule(checks=[dict(REQUIRE_UNITS, name=f"c{i}") for i in range(26)])

    def test_the_scope_entity_types_are_the_metadata_schema_entity_types(self):
        assert engine.SCOPE_ENTITY_TYPE == {
            MetadataScope.asset: MetadataSchemaEntityType.ASSET_METADATA.value,
            MetadataScope.files: MetadataSchemaEntityType.FILE_METADATA.value,
            MetadataScope.fileAttributes: MetadataSchemaEntityType.FILE_ATTRIBUTE.value,
            MetadataScope.links: MetadataSchemaEntityType.ASSET_LINK_METADATA.value,
        }


@pytest.mark.unit
class TestFileScopes:

    @pytest.mark.parametrize("scope", ["files", "fileAttributes"])
    def test_every_current_file_is_evaluated_and_one_without_rows_fails(self, scope):
        rule = _rule(scope)
        files = FILES[:1] + FILES[2:3]
        result = _files(rule, {"/models/part.glb": {"units": "mm"}}, files=files)
        assert result.passed is False
        assert result.measured == {
            "scope": scope, "evaluatedCount": 2, "failingCount": 1,
            "failing": [{"path": "/models/part.stl",
                         "message": "units: missing required schema fields: ['units']"}]}
        assert result.message == (
            "1 of 2 files fail: /models/part.stl: units: missing required schema fields: ['units']")
        assert result.expected == {"schemaRef": "db1/Model Schema", "scope": scope}

    def test_rows_for_a_file_no_longer_current_are_not_evaluated(self):
        rule = _rule("files")
        result = _files(rule, {"/models/part.glb": {"units": "mm"},
                               "/archived.glb": {}}, files=FILES[:1])
        assert result.passed is True
        assert result.measured["evaluatedCount"] == 1

    def test_the_restriction_then_include_then_exclude_select_the_files(self):
        rule = _rule("files", includePaths=["^models/", "^drafts/"], excludePaths=["^drafts/"])
        result = _files(rule, restriction=".glb, .STL")
        # .png preview and LICENSE: the png extension is not listed; LICENSE has no extension and
        # the write path applies a restricted schema to it, but includePaths leaves it out.
        assert [f["path"] for f in result.measured["failing"]] == [
            "/models/part.glb", "/models/part.stl"]
        assert result.measured["evaluatedCount"] == 2

    def test_an_extension_less_file_is_in_scope_of_a_restricted_schema(self):
        result = _files(_rule("files"), files=[engine.CurrentFile("/LICENSE", None)],
                        restriction=".glb")
        assert result.measured["evaluatedCount"] == 1

    @pytest.mark.parametrize("restriction", [None, "", "  ", ".all", ".glb,.ALL"])
    def test_no_restriction_or_all_admits_every_file(self, restriction):
        result = _files(_rule("files"), restriction=restriction)
        assert result.measured["evaluatedCount"] == len(FILES)

    def test_patterns_are_searched_in_the_path_without_its_leading_slash(self):
        result = _files(_rule("files", includePaths=["^LICENSE$"]))
        assert result.measured["evaluatedCount"] == 1

    def test_no_file_in_scope_is_a_pass(self):
        result = _files(_rule("files", includePaths=["^nothing/"]))
        assert result.passed is True
        assert result.message is None
        assert result.measured == {"scope": "files", "evaluatedCount": 0, "failingCount": 0,
                                   "failing": []}

    def test_at_most_ten_failing_files_are_named_and_the_rest_counted(self):
        files = [engine.CurrentFile(f"/f{i:02d}.glb", None) for i in range(12)]
        result = _files(_rule("files"), files=files)
        assert len(result.measured["failing"]) == 10
        assert result.measured["failingCount"] == 12
        assert result.message.startswith("12 of 12 files fail: /f00.glb:")
        assert result.message.endswith("; and 2 more")
        assert "/f10.glb" not in result.message

    def test_no_pattern_appears_in_the_result(self):
        rule = _rule("files", includePaths=["^models/"], excludePaths=["secret-pattern"])
        result = _files(rule)
        rendered = repr(result.dict())
        assert "^models/" not in rendered and "secret-pattern" not in rendered

    def test_file_attributes_read_the_attribute_rows_not_the_metadata_rows(self):
        rule = _rule("fileAttributes")
        result = _evaluate(rule, current_files=FILES[:1],
                           file_metadata_by_path={"/models/part.glb": {"units": "mm"}},
                           file_attributes_by_path={})
        assert result.passed is False

    def test_a_listing_that_could_not_be_made_is_a_tooling_error(self):
        result = _evaluate(_rule("files"), current_files=None, file_metadata_by_path={})
        assert result.status == "error"
        assert result.message == engine.FILES_UNAVAILABLE_MESSAGE

    def test_rows_that_were_not_read_are_a_tooling_error(self):
        result = _evaluate(_rule("files"), current_files=FILES, file_metadata_by_path=None)
        assert result.status == "error"
        assert result.message == engine.METADATA_UNAVAILABLE_MESSAGE

    def test_a_stored_pattern_that_no_longer_compiles_is_a_tooling_error(self):
        rule = _rule("files")
        rule.includePaths = ["(unclosed"]
        result = _files(rule)
        assert result.status == "error"
        assert result.message == engine.INVALID_PATTERN_MESSAGE


LINKS = {
    "parents": [{"assetLinkId": "p-1", "relationshipType": "parentChild"},
                {"assetLinkId": "r-1", "relationshipType": "related"}],
    "children": [{"assetLinkId": "c-1", "relationshipType": "parentChild"},
                 {"assetLinkId": "r-2", "relationshipType": "related"}],
}


@pytest.mark.unit
class TestLinksScope:

    @pytest.mark.parametrize("direction,relationship,expected", [
        (None, None, ["c-1", "p-1", "r-1", "r-2"]),
        ("parents", None, ["p-1", "r-1"]),
        ("children", "parentChild", ["c-1"]),
        ("related", "related", ["r-1", "r-2"]),
    ])
    def test_links_in_scope(self, direction, relationship, expected):
        rows = engine.links_in_scope(LINKS, direction, relationship)
        assert [row["assetLinkId"] for row in rows] == expected

    def test_every_link_in_scope_must_carry_the_metadata(self):
        rule = _rule("links", checks=[{"name": "placed", "additionalRequiredFields": ["Matrix"]}],
                     linkDirection="parents", relationshipType="parentChild")
        passing = _evaluate(rule, asset_links=LINKS,
                            link_metadata_by_id={"p-1": {"Matrix": "1,0,0"}})
        assert passing.passed is True
        failing = _evaluate(rule, asset_links=LINKS, link_metadata_by_id={})
        assert failing.passed is False
        assert failing.measured["failing"] == [
            {"assetLinkId": "p-1",
             "message": "placed: missing additional required fields: ['Matrix']"}]
        assert failing.message.startswith("1 of 1 links fail: p-1:")

    def test_no_link_in_scope_is_a_pass(self):
        rule = _rule("links", linkDirection="children", relationshipType="related")
        result = _evaluate(rule, asset_links={"parents": [], "children": []},
                           link_metadata_by_id={})
        assert result.passed is True
        assert result.measured["evaluatedCount"] == 0

    def test_more_links_than_the_bound_is_a_tooling_error_not_a_partial_pass(self):
        many = {"parents": [], "children": [
            {"assetLinkId": f"l-{i:04d}", "relationshipType": "parentChild"}
            for i in range(engine.MAX_LINKS_PER_METADATA_RULE + 1)]}
        result = _evaluate(_rule("links"), asset_links=many, link_metadata_by_id={})
        assert result.status == "error"
        assert result.message == engine.TOO_MANY_LINKS_MESSAGE

    def test_only_the_links_of_rules_within_the_bound_are_read(self):
        many = {"parents": [{"assetLinkId": "p-1", "relationshipType": "parentChild"}],
                "children": [{"assetLinkId": f"l-{i:04d}", "relationshipType": "related"}
                             for i in range(engine.MAX_LINKS_PER_METADATA_RULE + 1)]}
        rules = {"bounded": _rule("links", linkDirection="parents"),
                 "unbounded": _rule("links", linkDirection="children"),
                 "asset": _rule("asset")}
        assert engine.link_ids_to_read(rules, many) == ["p-1"]


@pytest.mark.unit
class TestTheSchemaBinding:

    @pytest.mark.parametrize("scope", ["asset", "files", "fileAttributes", "links"])
    def test_a_schema_of_another_entity_type_is_a_tooling_error(self, scope):
        other = next(s for s in ("asset", "files", "fileAttributes", "links") if s != scope)
        result = _evaluate(_rule(scope), schema=_schema(other), asset_metadata={},
                           current_files=FILES, file_metadata_by_path={},
                           file_attributes_by_path={}, asset_links=LINKS, link_metadata_by_id={})
        assert result.status == "error"
        assert result.message == engine.METADATA_SCHEMA_SCOPE_MISMATCH_MESSAGE

    @pytest.mark.parametrize("scope", ["asset", "files", "fileAttributes", "links"])
    def test_a_missing_schema_is_a_tooling_error_for_every_scope(self, scope):
        result = engine.evaluate_metadata_rule("r", _rule(scope), engine.MetadataRuleInputs())
        assert result.status == "error"
        assert result.message == engine.METADATA_SCHEMA_NOT_FOUND_MESSAGE

    def test_the_schema_key_carries_the_scope(self):
        rule_asset, rule_files = _rule("asset"), _rule("files")
        keys = {engine.metadata_schema_ref_key(r.metadataSchemaRef, r.scope)
                for r in (rule_asset, rule_files)}
        assert keys == {"db1/Model Schema/asset", "db1/Model Schema/files"}


@pytest.mark.unit
class TestConditionalInputs:

    def test_each_scope_asks_for_its_own_source(self):
        def groups(scope):
            return engine.split_rules({"m": _rule(scope)})
        asset, files, attributes, links = (groups(s) for s in
                                           ("asset", "files", "fileAttributes", "links"))
        assert not any(f(asset) for f in (engine.needs_file_metadata, engine.needs_file_attributes,
                                          engine.needs_link_metadata, engine.needs_current_files,
                                          engine.needs_asset_row))
        assert engine.needs_file_metadata(files) and engine.needs_current_files(files)
        assert engine.needs_asset_row(files) and not engine.needs_file_attributes(files)
        assert engine.needs_file_attributes(attributes) and engine.needs_current_files(attributes)
        assert engine.needs_link_metadata(links) and not engine.needs_current_files(links)
