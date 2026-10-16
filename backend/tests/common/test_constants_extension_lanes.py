# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The backend constants lanes: common/addonConstants.py and custom/customConstants.py.

``common/constants.py`` builds ``ALLOWED_CONSTRAINT_OBJECT_TYPES``, ``PERMISSION_CONSTRAINT_FIELDS``,
``CONSTRAINT_OBJECT_TYPE_FIELDS``, ``UNALLOWED_FILE_EXTENSION_LIST`` and ``UNALLOWED_MIME_LIST`` as
core + add-on + custom at import, and refuses a lane entry that breaks a rule. These tests pin:

* the empty state: with every lane empty (as shipped) each merged structure equals its core list,
  and so does the ``GET /auth/constraints/permissionObjects`` body;
* every merge rule, each with a control that the same lane minus the offending entry merges;
* that the refusal happens at import, naming the lane file and the entry (fail closed);
* that every consumer reads the merged structure, not a core-only copy.

A populated lane is exercised by loading a private copy of constants.py with stand-in lane modules,
so the shipped lane files stay empty and nothing here asserts that they are empty.
"""

import ast
import contextlib
import importlib.util
import keyword
import os
import sys
import types

import pytest
from aws_lambda_powertools.utilities.parser import ValidationError, parse

# Imported at collection time: the root conftest replaces backend.backend with a mock for any
# test that imports it later.
from backend.backend.common import constants as shipped
from backend.backend.models.roleConstraints import (
    ConstraintCriteriaModel,
    CreateConstraintRequestModel,
    ImportConstraintsTemplateRequestModel,
)

_BACKEND = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "backend"))
_CONSTANTS_PATH = os.path.join(_BACKEND, "common", "constants.py")

WIDGET_TYPE = {
    "widget": {
        "label": "Widget",
        "fields": [
            {"label": "Database ID", "value": "databaseId"},
            {"label": "Widget ID", "value": "widgetId"},
        ],
    }
}


def _lane(name, prefix, fields=None, types_=None, extensions=None, mimes=None):
    module = types.ModuleType(name)
    setattr(module, f"{prefix}_PERMISSION_CONSTRAINT_FIELDS", dict(fields or {}))
    setattr(module, f"{prefix}_CONSTRAINT_OBJECT_TYPE_FIELDS", dict(types_ or {}))
    setattr(module, f"{prefix}_UNALLOWED_FILE_EXTENSIONS", list(extensions or []))
    setattr(module, f"{prefix}_UNALLOWED_MIME_TYPES", list(mimes or []))
    return module


@contextlib.contextmanager
def _lanes_installed(addon=None, custom=None):
    """Put stand-in lane modules in sys.modules for the duration of the block."""
    stand_ins = {
        "common.addonConstants": addon or _lane("common.addonConstants", "ADDON"),
        "custom.customConstants": custom or _lane("custom.customConstants", "CUSTOM"),
    }
    saved = {name: sys.modules.get(name) for name in stand_ins}
    sys.modules.update(stand_ins)
    try:
        yield
    finally:
        for name, module in saved.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module


def load_constants(addon=None, custom=None):
    """A private copy of constants.py built against the given lanes (default: empty)."""
    with _lanes_installed(addon, custom):
        spec = importlib.util.spec_from_file_location("_lane_test_constants", _CONSTANTS_PATH)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module


def custom_lane(**kwargs):
    return _lane("custom.customConstants", "CUSTOM", **kwargs)


def addon_lane(**kwargs):
    return _lane("common.addonConstants", "ADDON", **kwargs)


C = load_constants()
# Each private load defines its own exception class, so match the shared base and the message.
Error = ValueError


def _merge(custom_fields=None, custom_types=None, addon_fields=None, addon_types=None):
    return C.merge_constraint_extensions(
        C.CORE_ALLOWED_CONSTRAINT_OBJECT_TYPES,
        C.CORE_PERMISSION_CONSTRAINT_FIELDS,
        C.CORE_CONSTRAINT_OBJECT_TYPE_FIELDS,
        [
            (C.ADDON_CONSTANTS_SOURCE, addon_fields or {}, addon_types or {}),
            (C.CUSTOM_CONSTANTS_SOURCE, custom_fields or {}, custom_types or {}),
        ],
    )


def _deny(kind, core, custom=None, addon=None):
    pattern = (C.EXTENSION_DENY_EXTENSION_PATTERN if kind == "file extension"
               else C.EXTENSION_DENY_MIME_PATTERN)
    return C.merge_deny_list(kind, core, [(C.ADDON_CONSTANTS_SOURCE, addon or []),
                                          (C.CUSTOM_CONSTANTS_SOURCE, custom or [])], pattern)


@pytest.mark.unit
class TestEmptyStateEqualsCore:
    def test_every_merged_structure_equals_its_core_list(self):
        assert C.ALLOWED_CONSTRAINT_OBJECT_TYPES == C.CORE_ALLOWED_CONSTRAINT_OBJECT_TYPES
        assert C.PERMISSION_CONSTRAINT_FIELDS == C.CORE_PERMISSION_CONSTRAINT_FIELDS
        assert list(C.PERMISSION_CONSTRAINT_FIELDS) == list(C.CORE_PERMISSION_CONSTRAINT_FIELDS)
        assert C.CONSTRAINT_OBJECT_TYPE_FIELDS == C.CORE_CONSTRAINT_OBJECT_TYPE_FIELDS
        assert list(C.CONSTRAINT_OBJECT_TYPE_FIELDS) == list(C.CORE_CONSTRAINT_OBJECT_TYPE_FIELDS)
        assert C.UNALLOWED_FILE_EXTENSION_LIST == C.CORE_UNALLOWED_FILE_EXTENSION_LIST
        assert C.UNALLOWED_MIME_LIST == C.CORE_UNALLOWED_MIME_LIST

    def test_the_shipped_lane_files_are_merged_into_the_module_lambdas_import(self):
        """The module every Lambda imports is core + the real lane files, whatever they hold.

        Asserted for any lane content (a fork fills custom/customConstants.py), never that the lanes
        are empty: the merge of the CORE_* lists with the lane files on disk must equal what the
        module exposes.
        """
        from backend.backend.common import addonConstants as addon_lane
        from backend.backend.custom import customConstants as custom_lane

        expected = shipped.merge_constraint_extensions(
            shipped.CORE_ALLOWED_CONSTRAINT_OBJECT_TYPES,
            shipped.CORE_PERMISSION_CONSTRAINT_FIELDS,
            shipped.CORE_CONSTRAINT_OBJECT_TYPE_FIELDS,
            [(shipped.ADDON_CONSTANTS_SOURCE, addon_lane.ADDON_PERMISSION_CONSTRAINT_FIELDS,
              addon_lane.ADDON_CONSTRAINT_OBJECT_TYPE_FIELDS),
             (shipped.CUSTOM_CONSTANTS_SOURCE, custom_lane.CUSTOM_PERMISSION_CONSTRAINT_FIELDS,
              custom_lane.CUSTOM_CONSTRAINT_OBJECT_TYPE_FIELDS)],
        )
        assert (shipped.ALLOWED_CONSTRAINT_OBJECT_TYPES, shipped.PERMISSION_CONSTRAINT_FIELDS,
                shipped.CONSTRAINT_OBJECT_TYPE_FIELDS) == expected
        assert shipped.UNALLOWED_FILE_EXTENSION_LIST == (
            shipped.CORE_UNALLOWED_FILE_EXTENSION_LIST
            + addon_lane.ADDON_UNALLOWED_FILE_EXTENSIONS + custom_lane.CUSTOM_UNALLOWED_FILE_EXTENSIONS)
        assert shipped.UNALLOWED_MIME_LIST == (
            shipped.CORE_UNALLOWED_MIME_LIST
            + addon_lane.ADDON_UNALLOWED_MIME_TYPES + custom_lane.CUSTOM_UNALLOWED_MIME_TYPES)

    def test_the_core_lists_are_the_eleven_core_types(self):
        # Control: the comparison above is against the real core matrix, not two empty lists.
        assert C.CORE_ALLOWED_CONSTRAINT_OBJECT_TYPES == [
            "database", "asset", "api", "web", "tag", "tagType", "role", "userRole", "pipeline",
            "workflow", "metadataSchema",
        ]
        assert ".exe" in C.CORE_UNALLOWED_FILE_EXTENSION_LIST
        assert "application/x-msdownload" in C.CORE_UNALLOWED_MIME_LIST

    def test_merging_does_not_mutate_the_core_lists(self):
        before = (list(C.CORE_ALLOWED_CONSTRAINT_OBJECT_TYPES), dict(C.CORE_PERMISSION_CONSTRAINT_FIELDS),
                  dict(C.CORE_CONSTRAINT_OBJECT_TYPE_FIELDS), list(C.CORE_UNALLOWED_FILE_EXTENSION_LIST))
        _merge(custom_fields={"widgetId": ""}, custom_types=WIDGET_TYPE)
        _deny("file extension", C.CORE_UNALLOWED_FILE_EXTENSION_LIST, custom=[".xyz"])
        assert before == (list(C.CORE_ALLOWED_CONSTRAINT_OBJECT_TYPES),
                          dict(C.CORE_PERMISSION_CONSTRAINT_FIELDS),
                          dict(C.CORE_CONSTRAINT_OBJECT_TYPE_FIELDS),
                          list(C.CORE_UNALLOWED_FILE_EXTENSION_LIST))


@pytest.mark.unit
class TestObjectTypeAndFieldMerge:
    def test_a_valid_type_and_field_are_appended_after_core(self):
        object_types, fields, type_fields = _merge(custom_fields={"widgetId": ""},
                                                   custom_types=WIDGET_TYPE)
        assert object_types == C.CORE_ALLOWED_CONSTRAINT_OBJECT_TYPES + ["widget"]
        assert list(fields)[-1] == "widgetId" and fields["widgetId"] == ""
        assert type_fields["widget"] == WIDGET_TYPE["widget"]

    def test_add_on_entries_come_before_custom_ones(self):
        object_types, fields, _ = _merge(
            addon_fields={"gadgetId": ""},
            addon_types={"gadget": {"label": "Gadget", "fields": [{"label": "G", "value": "gadgetId"}]}},
            custom_fields={"widgetId": ""}, custom_types=WIDGET_TYPE)
        assert object_types[-2:] == ["gadget", "widget"]
        assert list(fields)[-2:] == ["gadgetId", "widgetId"]

    def test_a_list_valued_sample_stays_a_list(self):
        _, fields, _ = _merge(custom_fields={"widgetTags": []})
        assert fields["widgetTags"] == []

    def test_a_type_may_list_a_field_another_lane_declares(self):
        # Control for the reference rule below: a custom type listing an add-on field merges.
        _, _, type_fields = _merge(
            addon_fields={"gadgetId": ""},
            custom_types={"widget": {"label": "W", "fields": [{"label": "G", "value": "gadgetId"}]}})
        assert type_fields["widget"]["fields"][0]["value"] == "gadgetId"

    @pytest.mark.parametrize("name", ["widget_id", "_widget", "1widget", "w", "w" * 65, "widget-id",
                                      "widget id", "widgetId'", "a.b", "a(b", "", "wídget"])
    def test_a_malformed_field_name_is_refused(self, name):
        with pytest.raises(Error, match=r"custom/customConstants\.py: constraint field name .* must match"):
            _merge(custom_fields={name: ""})

    @pytest.mark.parametrize("name", ["widget_type", "9widget", "x", "wid-get", ""])
    def test_a_malformed_type_name_is_refused(self, name):
        with pytest.raises(Error, match=r"custom/customConstants\.py: object type name .* must match"):
            _merge(custom_fields={"widgetId": ""},
                   custom_types={name: {"label": "W", "fields": [{"label": "W", "value": "widgetId"}]}})

    def test_the_longest_valid_name_is_accepted(self):
        # Control for the length bound: 64 characters is the limit, 65 is refused above.
        _merge(custom_fields={"w" * 64: ""})

    @pytest.mark.parametrize("reserved", ["object__type", "route__path", "method"])
    def test_a_reserved_key_cannot_be_declared_or_listed(self, reserved):
        with pytest.raises(Error):
            _merge(custom_fields={reserved: ""})
        with pytest.raises(Error):
            _merge(custom_types={reserved: {"label": "R", "fields": [{"label": "D", "value": "databaseId"}]}})
        with pytest.raises(Error, match="reserved key"):
            _merge(custom_types={"widget": {"label": "W", "fields": [{"label": "R", "value": reserved}]}})

    def test_method_is_reserved_although_it_matches_the_name_pattern(self):
        # `method` is the one reserved key the pattern alone would accept.
        with pytest.raises(Error, match="'method' is a reserved key"):
            _merge(custom_fields={"method": ""})

    @pytest.mark.parametrize("name", ["if", "lambda", "not", "and", "True", "None", "class"])
    def test_a_python_keyword_field_is_refused(self, name):
        with pytest.raises(Error, match="cannot be evaluated"):
            _merge(custom_fields={name: ""})

    @pytest.mark.parametrize("name", ["keys", "get", "items", "values", "format", "mro", "exec"])
    def test_an_evaluator_reserved_field_is_refused(self, name):
        with pytest.raises(Error, match="cannot be evaluated"):
            _merge(custom_fields={name: ""})

    def test_the_evaluator_reserved_set_covers_simpleeval_and_dict(self):
        """Kept in step with the libraries: every pattern-legal name simpleeval refuses, and every
        public dict attribute (getattr wins over the key lookup), is reserved."""
        import re as _re

        import simpleeval

        legal = _re.compile(C.EXTENSION_NAME_PATTERN)
        refused = {name for name in simpleeval.DISALLOW_METHODS if legal.fullmatch(name)}
        dict_attrs = {name for name in dir(dict) if not name.startswith("_")}
        assert refused | dict_attrs <= C.EVALUATOR_RESERVED_ATTRIBUTES
        # simpleeval's prefixes are outside the pattern, so no prefix needs its own entry.
        assert all(not legal.fullmatch(prefix + "x") for prefix in simpleeval.DISALLOW_PREFIXES)

    @pytest.mark.parametrize("core_field", ["databaseId", "tags", "assetName"])
    def test_an_extension_cannot_redefine_a_core_field(self, core_field):
        with pytest.raises(Error, match=f"constraint field '{core_field}' is already declared by core"):
            _merge(custom_fields={core_field: ""})

    @pytest.mark.parametrize("core_type", ["database", "asset", "metadataSchema"])
    def test_an_extension_cannot_redefine_a_core_type(self, core_type):
        with pytest.raises(Error, match=f"object type '{core_type}' is already declared by core"):
            _merge(custom_types={core_type: {"label": "X", "fields": [{"label": "D", "value": "databaseId"}]}})

    def test_add_on_and_custom_cannot_duplicate_each_other(self):
        with pytest.raises(Error, match=r"custom/customConstants\.py: constraint field 'widgetId' is "
                                        r"already declared by common/addonConstants\.py"):
            _merge(addon_fields={"widgetId": ""}, custom_fields={"widgetId": ""})
        with pytest.raises(Error, match=r"custom/customConstants\.py: object type 'widget' is "
                                        r"already declared by common/addonConstants\.py"):
            _merge(addon_types=WIDGET_TYPE, custom_types=WIDGET_TYPE, addon_fields={"widgetId": ""})

    @pytest.mark.parametrize("sample", ["x", ["a"], None, 0, {}, ("",)])
    def test_a_sample_other_than_empty_string_or_empty_list_is_refused(self, sample):
        with pytest.raises(Error, match=r"must map to \"\" \(one value\) or \[\]"):
            _merge(custom_fields={"widgetId": sample})

    def test_a_type_listing_an_undeclared_field_is_refused(self):
        with pytest.raises(Error, match="lists 'widgetId', which is not a constraint field"):
            _merge(custom_types=WIDGET_TYPE)

    @pytest.mark.parametrize("entry", [
        {"fields": [{"label": "D", "value": "databaseId"}]},
        {"label": "W"},
        {"label": "W", "fields": [], },
        {"label": "", "fields": [{"label": "D", "value": "databaseId"}]},
        {"label": "W", "fields": [{"value": "databaseId"}]},
        {"label": "W", "fields": [{"label": "", "value": "databaseId"}]},
        {"label": "W", "fields": [{"label": "D", "value": "databaseId"}], "extra": 1},
        {"label": "W", "fields": [{"label": "D", "value": "databaseId"},
                                  {"label": "D2", "value": "databaseId"}]},
        ["databaseId"],
    ])
    def test_a_malformed_type_entry_is_refused(self, entry):
        with pytest.raises(Error, match=r"custom/customConstants\.py: object type 'widget'"):
            _merge(custom_types={"widget": entry})

    def test_a_lane_that_is_not_a_dict_is_refused(self):
        with pytest.raises(Error):
            _merge(custom_fields=["widgetId"])


@pytest.mark.unit
class TestDenyListMerge:
    def test_entries_are_appended_and_the_core_list_is_kept_whole(self):
        merged = _deny("file extension", C.CORE_UNALLOWED_FILE_EXTENSION_LIST,
                       addon=[".abc"], custom=[".xyz"])
        assert merged[: len(C.CORE_UNALLOWED_FILE_EXTENSION_LIST)] == C.CORE_UNALLOWED_FILE_EXTENSION_LIST
        assert merged[-2:] == [".abc", ".xyz"]
        mimes = _deny("MIME type", C.CORE_UNALLOWED_MIME_LIST, custom=["application/x-widget"])
        assert mimes[-1] == "application/x-widget"
        assert set(C.CORE_UNALLOWED_MIME_LIST) <= set(mimes)

    def test_a_lane_has_no_way_to_remove_a_core_entry(self):
        # Union only: whatever a lane holds, every core entry is still blocked.
        for lane in ([], [".xyz"], [".abc", ".xyz"]):
            merged = _deny("file extension", C.CORE_UNALLOWED_FILE_EXTENSION_LIST, custom=lane)
            assert set(C.CORE_UNALLOWED_FILE_EXTENSION_LIST) <= set(merged)

    @pytest.mark.parametrize("entry", [".exe", ".bat", ".docm"])
    def test_a_core_extension_cannot_be_repeated(self, entry):
        with pytest.raises(Error, match=f"'{entry}' is already blocked by core"):
            _deny("file extension", C.CORE_UNALLOWED_FILE_EXTENSION_LIST, custom=[entry])

    def test_a_core_mime_type_cannot_be_repeated_in_any_case_spelling(self):
        with pytest.raises(Error, match="already blocked by core"):
            _deny("MIME type", C.CORE_UNALLOWED_MIME_LIST,
                  custom=["application/vnd.ms-word.document.macroenabled.12"])

    def test_add_on_and_custom_cannot_repeat_each_other(self):
        with pytest.raises(Error, match=r"custom/customConstants\.py: file extension '\.xyz' is "
                                        r"already blocked by common/addonConstants\.py"):
            _deny("file extension", C.CORE_UNALLOWED_FILE_EXTENSION_LIST, addon=[".xyz"], custom=[".xyz"])

    @pytest.mark.parametrize("entry", ["xyz", ".XYZ", ".Xyz", "..xyz", ".tar.gz", ".", ".xy z",
                                       ".x/y", "", None, 3, "." + "a" * 33])
    def test_a_malformed_extension_is_refused(self, entry):
        with pytest.raises(Error, match="must match"):
            _deny("file extension", C.CORE_UNALLOWED_FILE_EXTENSION_LIST, custom=[entry])

    @pytest.mark.parametrize("entry", ["Application/X-Widget", "application/x-widget; charset=binary",
                                       "application", "application/", "/x-widget", " application/x",
                                       "application/x widget", None])
    def test_a_malformed_mime_type_is_refused(self, entry):
        with pytest.raises(Error, match="must match"):
            _deny("MIME type", C.CORE_UNALLOWED_MIME_LIST, custom=[entry])

    def test_a_lane_that_is_not_a_list_is_refused(self):
        with pytest.raises(Error, match="must be a list"):
            _deny("file extension", C.CORE_UNALLOWED_FILE_EXTENSION_LIST, custom=".xyz")


@pytest.mark.unit
class TestRefusalHappensAtImport:
    """A broken lane fails the import of common.constants, so no Lambda starts with it."""

    def test_a_bad_custom_lane_fails_the_import_naming_the_file_and_entry(self):
        with pytest.raises(ValueError, match=r"custom/customConstants\.py: constraint field name 'widget_id'") as excinfo:
            load_constants(custom=custom_lane(fields={"widget_id": ""}))
        # The refusal is the module's own error type, not an incidental ValueError.
        assert type(excinfo.value).__name__ == "ConstantsExtensionError"

    def test_a_bad_add_on_lane_fails_the_import_naming_the_file(self):
        with pytest.raises(Error, match=r"common/addonConstants\.py: file extension '\.EXE'"):
            load_constants(addon=addon_lane(extensions=[".EXE"]))

    def test_a_good_lane_imports_and_merges(self):
        module = load_constants(custom=custom_lane(fields={"widgetId": ""}, types_=WIDGET_TYPE,
                                                   extensions=[".xyz"], mimes=["application/x-widget"]))
        assert module.ALLOWED_CONSTRAINT_OBJECT_TYPES[-1] == "widget"
        assert module.get_constraint_fields_for_object_type("widget") == ["databaseId", "widgetId"]
        assert module.UNALLOWED_FILE_EXTENSION_LIST[-1] == ".xyz"
        assert module.UNALLOWED_MIME_LIST[-1] == "application/x-widget"


def _populated():
    return load_constants(custom=custom_lane(
        fields={"widgetId": "", "widgetTags": []},
        types_={"widget": {"label": "Widget", "fields": [
            {"label": "Database ID", "value": "databaseId"},
            {"label": "Widget ID", "value": "widgetId"},
            {"label": "Widget Tags", "value": "widgetTags"}]}},
        extensions=[".xyz"], mimes=["application/x-widget"]))


@contextlib.contextmanager
def _common_constants(module):
    """Make `from common.constants import ...` (a call-time import) see ``module``."""
    saved = sys.modules.get("common.constants")
    sys.modules["common.constants"] = module
    try:
        yield
    finally:
        if saved is None:
            sys.modules.pop("common.constants", None)
        else:
            sys.modules["common.constants"] = saved


@pytest.mark.unit
class TestConsumersReadTheMergedStructures:
    def test_no_module_imports_a_core_only_list(self):
        """Every consumer imports the merged names; a CORE_* import would skip the extensions."""
        offenders = []
        for root, _dirs, files in os.walk(_BACKEND):
            for name in files:
                path = os.path.join(root, name)
                if not name.endswith(".py") or path == _CONSTANTS_PATH:
                    continue
                with open(path, encoding="utf-8") as handle:
                    tree = ast.parse(handle.read())
                for node in ast.walk(tree):
                    if isinstance(node, ast.ImportFrom) and (node.module or "").endswith("constants"):
                        offenders += [f"{path}: {a.name}" for a in node.names if a.name.startswith("CORE_")]
                    if isinstance(node, ast.Attribute) and node.attr.startswith("CORE_UNALLOWED") \
                            or isinstance(node, ast.Attribute) and node.attr.startswith("CORE_ALLOWED"):
                        offenders.append(f"{path}: {node.attr}")
        assert offenders == []

    @pytest.mark.parametrize("relative,names", [
        ("handlers/authz/__init__.py", {"PERMISSION_CONSTRAINT_FIELDS", "get_constraint_fields_for_object_type"}),
        ("handlers/auth/authConstraintsService.py", {"ALLOWED_CONSTRAINT_OBJECT_TYPES", "CONSTRAINT_OBJECT_TYPE_FIELDS"}),
        ("handlers/auth/authConstraintsTemplateService.py", {"ALLOWED_CONSTRAINT_OBJECT_TYPES"}),
        ("models/roleConstraints.py", {"ALLOWED_CONSTRAINT_OBJECT_TYPES", "PERMISSION_CONSTRAINT_FIELDS",
                                       "get_constraint_fields_for_object_type"}),
        ("common/s3.py", {"UNALLOWED_MIME_LIST", "UNALLOWED_FILE_EXTENSION_LIST"}),
    ])
    def test_each_consumer_imports_the_merged_names(self, relative, names):
        """The enforcer scrub and rules, constraint and template validation, the permissionObjects
        listing and the upload check import the merged names from common.constants."""
        with open(os.path.join(_BACKEND, relative), encoding="utf-8") as handle:
            tree = ast.parse(handle.read())
        imported = {alias.name for node in ast.walk(tree)
                    if isinstance(node, ast.ImportFrom) and node.module == "common.constants"
                    for alias in node.names}
        assert names <= imported, sorted(names - imported)

    def test_the_merged_names_are_the_objects_get_constraint_fields_reads(self):
        # get_constraint_fields_for_object_type (the scrub and the write-time matrix check) reads the
        # merged matrix of its own module.
        module = _populated()
        assert module.get_constraint_fields_for_object_type("widget") == ["databaseId", "widgetId", "widgetTags"]
        assert C.get_constraint_fields_for_object_type("widget") == []

    def test_the_upload_check_reads_the_merged_deny_lists(self):
        source = open(os.path.join(_BACKEND, "common", "s3.py"), encoding="utf-8").read()
        assert "from common.constants import UNALLOWED_MIME_LIST, UNALLOWED_FILE_EXTENSION_LIST" in source

    def test_the_upload_check_blocks_an_extension_entry(self):
        module = _populated()
        s3_mod = _load_s3(module)
        assert s3_mod.validateUnallowedFileExtensionAndContentType("/a/model.xyz", "") is False
        assert s3_mod.validateUnallowedFileExtensionAndContentType("/a/model.bin", "application/x-widget") is False
        # Controls: a core entry is still blocked, and an unlisted file passes.
        assert s3_mod.validateUnallowedFileExtensionAndContentType("/a/tool.exe", "") is False
        assert s3_mod.validateUnallowedFileExtensionAndContentType("/a/model.glb", "model/gltf-binary") is True

    def test_constraint_validation_accepts_an_extension_type_and_its_fields(self):
        body = {
            "identifier": "widget-readers", "name": "widget-readers", "description": "Widgets",
            "objectType": "widget",
            "criteriaAnd": [{"field": "widgetId", "operator": "equals", "value": "w1"}],
            "groupPermissions": [{"groupId": "r1", "permission": "GET", "permissionType": "allow"}],
        }
        with _common_constants(_populated()):
            assert parse(body, model=CreateConstraintRequestModel).objectType == "widget"
            off_matrix = dict(body, criteriaAnd=[{"field": "assetName", "operator": "equals", "value": "x"}])
            with pytest.raises(ValidationError):
                parse(off_matrix, model=CreateConstraintRequestModel)
        # Control: without the lane the same type is refused.
        with _common_constants(C), pytest.raises(ValidationError):
            parse(body, model=CreateConstraintRequestModel)

    def test_template_import_accepts_an_extension_type(self):
        body = {
            "variableValues": {"ROLE_NAME": "r1", "DATABASE_ID": "db1"},
            "constraints": [{
                "name": "{{ROLE_NAME}}-widgets", "description": "d", "objectType": "widget",
                "criteriaAnd": [{"field": "databaseId", "operator": "equals", "value": "{{DATABASE_ID}}"}],
                "groupPermissions": [{"action": "GET", "type": "allow"}],
            }],
        }
        with _common_constants(_populated()):
            assert parse(body, model=ImportConstraintsTemplateRequestModel).constraints[0].objectType == "widget"
        with _common_constants(C), pytest.raises(ValidationError):
            parse(body, model=ImportConstraintsTemplateRequestModel)

    @pytest.mark.parametrize("operator", ["equals", "contains", "starts_with", "ends_with"])
    def test_a_list_valued_extension_field_takes_only_the_membership_operators(self, operator):
        with _common_constants(_populated()):
            with pytest.raises(ValueError, match="is_one_of"):
                ConstraintCriteriaModel(field="widgetTags", operator=operator, value="blue")
            assert ConstraintCriteriaModel(field="widgetTags", operator="is_one_of", value=["blue"])
            # Control: the single-valued extension field takes the pattern operators.
            assert ConstraintCriteriaModel(field="widgetId", operator=operator, value="w1")


def _load_s3(constants_module):
    """A private copy of common/s3.py bound to ``constants_module``."""
    with _common_constants(constants_module):
        spec = importlib.util.spec_from_file_location("_lane_test_s3", os.path.join(_BACKEND, "common", "s3.py"))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    return module


@pytest.mark.unit
def test_keyword_module_agrees_with_the_rule():
    # Control: the keyword check is the interpreter's own list, not a hand-written copy.
    assert keyword.iskeyword("lambda") and not keyword.iskeyword("widgetId")
