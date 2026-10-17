# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Every `validate_asset_file_path` call site is either a created key or a declared read path.

`models.assetsV3.validate_asset_file_path(field_name, value, new_object=False, file_only=False)` is
the one path rule behind every file-operation request model. Its default is the READ rule: it accepts
a key that may already exist in a bucket, including one whose final segment ends in a dot or
whitespace. A validator for a key the request CREATES must pass `new_object=True`, or a key Windows
rewrites on download (`payload.exe.` lands as `payload.exe`) can be created past the extension
blocklist. Nothing in the function can tell the two apart, so the partition is enforced here, as a
RULE rather than as an inventory of survivors: a newly added validator that calls the function for a
key it creates without the flag fails this test until it either passes the flag or is named in
READ_PATHS with the reason the key already exists.

The scan is on the source (`ast`), not on the parsed models, because the fact under test is the
keyword each call site passes; pydantic keeps no record of that. `tests/models/
test_assetsV3_trailing_dot_write_paths.py` holds the hand-written behaviour cases for the same models.
"""

import ast
import os

import pytest

_MODELS_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "backend", "models")
_FUNCTION = "validate_asset_file_path"

# (class, field) pairs that call the function for a key that ALREADY EXISTS, with the reason. Each
# entry must still be a read site (the test fails on a stale one), and every read site must be here.
READ_PATHS = {
    "models/assetsV3.py::ExternalFileModel.tempKey": "temp object the completion copies from",
    "models/assetsV3.py::FileInfoRequestModel.filePath": "describes a stored object",
    "models/assetsV3.py::MoveFileRequestModel.sourcePath": "object being moved; destinationPath is created",
    "models/assetsV3.py::CopyFileRequestModel.sourcePath": "object being copied; destinationPath is created",
    "models/assetsV3.py::ArchiveFileRequestModel.filePath": "stored object being archived",
    "models/assetsV3.py::UnarchiveFileRequestModel.filePath": "archived object being restored",
    "models/assetsV3.py::DeleteFileRequestModel.filePath": "stored object being deleted",
    "models/assetsV3.py::RevertFileVersionRequestModel.filePath": "stored object whose version is restored",
    "models/assetsV3.py::SetPrimaryFileRequestModel.filePath": "stored object being marked primary",
    "models/assetsV3.py::DeleteAuxiliaryPreviewAssetFilesRequestModel.filePath": "stored object whose previews are deleted",
    "models/assetsV3.py::AssetFileVersionItemModel.relativeKey": "stored object pinned to a version by S3 version id",
    "models/assetsV3.py::DownloadAssetRequestModel.key": "stored object or prefix being downloaded",
}


def _is_target_call(node):
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    return (isinstance(func, ast.Name) and func.id == _FUNCTION) or (
        isinstance(func, ast.Attribute) and func.attr == _FUNCTION)


def _validator_fields(function_node):
    """The field names a `@validator(...)` decorator covers, or None when the function has none."""
    for decorator in function_node.decorator_list:
        if isinstance(decorator, ast.Call) and getattr(decorator.func, "id", None) == "validator":
            fields = [a.value for a in decorator.args if isinstance(a, ast.Constant)]
            if fields and len(fields) == len(decorator.args):
                return fields
    return None


def _new_object_for(call, field_name):
    """Whether this call passes `new_object=True` for `field_name`.

    Two spellings are recognised: a literal `True`/`False` (or omission, which is False), and
    `field.name == '<created field>'`, the form a validator that covers a created and a read field
    uses. Anything else is reported as unclassified rather than guessed at.
    """
    expression = None
    for keyword in call.keywords:
        if keyword.arg == "new_object":
            expression = keyword.value
    if expression is None and len(call.args) >= 3:
        expression = call.args[2]
    if expression is None:
        return False
    if isinstance(expression, ast.Constant) and isinstance(expression.value, bool):
        return expression.value
    if (isinstance(expression, ast.Compare)
            and isinstance(expression.left, ast.Attribute)
            and isinstance(expression.left.value, ast.Name)
            and expression.left.value.id == "field"
            and expression.left.attr == "name"
            and len(expression.ops) == 1 and isinstance(expression.ops[0], ast.Eq)
            and len(expression.comparators) == 1
            and isinstance(expression.comparators[0], ast.Constant)
            and isinstance(expression.comparators[0].value, str)):
        return field_name == expression.comparators[0].value
    return None


def _call_sites():
    """Every call of the function under `backend/models/`, as (key, new_object) with key
    `models/<module>.py::<Class>.<field>`; new_object is None where the call cannot be classified."""
    sites = []
    for file_name in sorted(os.listdir(_MODELS_DIR)):
        if not file_name.endswith(".py"):
            continue
        with open(os.path.join(_MODELS_DIR, file_name), encoding="utf-8") as f:
            tree = ast.parse(f.read(), filename=file_name)
        classified = set()
        for class_node in [n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]:
            for function_node in [n for n in class_node.body if isinstance(n, ast.FunctionDef)]:
                fields = _validator_fields(function_node)
                for call in [n for n in ast.walk(function_node) if _is_target_call(n)]:
                    classified.add(id(call))
                    if fields is None:
                        sites.append((f"models/{file_name}::{class_node.name}.{function_node.name}"
                                      f" (line {call.lineno}, not a @validator)", None))
                        continue
                    for field_name in fields:
                        key = f"models/{file_name}::{class_node.name}.{field_name}"
                        sites.append((key, _new_object_for(call, field_name)))
        # A call outside a model class (module level, a free function) has no field to classify.
        for call in [n for n in ast.walk(tree) if _is_target_call(n) and id(n) not in classified]:
            sites.append((f"models/{file_name}::<module> (line {call.lineno})", None))
    return sites


@pytest.mark.unit
class TestEveryCallSiteIsClassified:
    """The RULE: a (class, field) either creates the key (`new_object=True`) or is declared a read path."""

    def test_the_scan_finds_the_function(self):
        """Non-vacuity: the function is defined where the scan looks and is called from there."""
        with open(os.path.join(_MODELS_DIR, "assetsV3.py"), encoding="utf-8") as f:
            tree = ast.parse(f.read())
        defined = [n for n in ast.walk(tree)
                   if isinstance(n, ast.FunctionDef) and n.name == _FUNCTION]
        assert len(defined) == 1, "validate_asset_file_path moved; point _MODELS_DIR at it"
        argument_names = [a.arg for a in defined[0].args.args]
        assert "new_object" in argument_names and "file_only" in argument_names

    def test_every_call_site_is_a_created_key_or_a_declared_read_path(self):
        sites = _call_sites()
        unclassified = sorted(key for key, new_object in sites if new_object is None)
        created = sorted(key for key, new_object in sites if new_object is True)
        read = sorted(key for key, new_object in sites if new_object is False)

        assert unclassified == [], (
            "these validate_asset_file_path calls cannot be classified: pass new_object= as a literal "
            "True/False or as field.name == '<created field>', from a @validator method:\n  "
            + "\n  ".join(unclassified))
        undeclared = sorted(set(read) - set(READ_PATHS))
        assert undeclared == [], (
            "these fields call validate_asset_file_path without new_object=True and are not declared "
            "read paths. A key this request CREATES must pass new_object=True (and file_only=True when "
            "it names a file); a key that already exists goes in READ_PATHS with the reason:\n  "
            + "\n  ".join(undeclared))
        stale = sorted(set(READ_PATHS) & set(created))
        assert stale == [], (
            "these fields are declared read paths but now pass new_object=True; remove them from "
            "READ_PATHS:\n  " + "\n  ".join(stale))
        gone = sorted(set(READ_PATHS) - set(read) - set(created))
        assert gone == [], (
            "these declared read paths no longer call validate_asset_file_path; remove them from "
            "READ_PATHS:\n  " + "\n  ".join(gone))
        # Non-vacuity: the walk found both sides at all (six created and twelve read sites today).
        assert len(created) >= 3, f"only {len(created)} created-key site(s) found; the walk found too few"
        assert len(read) >= 6, f"only {len(read)} read-path site(s) found; the walk found too few"

    def test_the_classifier_reads_both_recognised_spellings(self):
        """The classifier itself, on the two spellings the rule accepts and one it refuses."""
        def classify(source, field_name):
            call = next(n for n in ast.walk(ast.parse(source)) if isinstance(n, ast.Call))
            return _new_object_for(call, field_name)

        assert classify("validate_asset_file_path('k', v)", "k") is False
        assert classify("validate_asset_file_path('k', v, new_object=True)", "k") is True
        assert classify("validate_asset_file_path('k', v, True)", "k") is True
        assert classify("validate_asset_file_path(field.name, v, new_object=(field.name == 'a'))", "a") is True
        assert classify("validate_asset_file_path(field.name, v, new_object=(field.name == 'a'))", "b") is False
        assert classify("validate_asset_file_path('k', v, new_object=created)", "k") is None
