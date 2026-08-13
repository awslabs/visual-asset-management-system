# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Bounds on the metadata request fields that become S3 keys and DynamoDB sort keys.

filePath is concatenated with the asset's S3 prefix to form an object key, and each
metadataKeys element becomes a DynamoDB sort key. Both need a length ceiling in addition
to the format and list-count checks the models already apply.
"""

import pytest
from aws_lambda_powertools.utilities.parser import parse, ValidationError

FILE_MODELS_WITH_PATH = [
    "GetFileMetadataRequestModel",
    "CreateFileMetadataRequestModel",
    "UpdateFileMetadataRequestModel",
    "DeleteFileMetadataRequestModel",
]

MODELS_WITH_KEYS = [
    "DeleteAssetLinkMetadataRequestModel",
    "DeleteAssetMetadataRequestModel",
    "DeleteDatabaseMetadataRequestModel",
]


def _model(name):
    import models.metadata as m
    return getattr(m, name)


def _body_for(name, **extra):
    """Minimal valid body for each file-metadata request model."""
    body = {"filePath": "/folder/file.glb", "type": "metadata"}
    if name in ("CreateFileMetadataRequestModel", "UpdateFileMetadataRequestModel"):
        body["metadata"] = [{"metadataKey": "colour", "metadataValue": "red"}]
    if name == "DeleteFileMetadataRequestModel":
        body["metadataKeys"] = ["colour"]
    body.update(extra)
    return body


@pytest.mark.unit
class TestFilePathBounds:
    @pytest.mark.parametrize("model_name", FILE_MODELS_WITH_PATH)
    def test_rejects_oversized_file_path(self, model_name):
        from models.metadata import MAX_FILE_PATH_LENGTH
        long_path = "/" + "d" * MAX_FILE_PATH_LENGTH
        with pytest.raises(ValidationError):
            parse(_body_for(model_name, filePath=long_path), model=_model(model_name))

    @pytest.mark.parametrize("model_name", FILE_MODELS_WITH_PATH)
    def test_accepts_file_path_at_ceiling(self, model_name):
        from models.metadata import MAX_FILE_PATH_LENGTH
        path = "/" + "d" * (MAX_FILE_PATH_LENGTH - 1)
        model = parse(_body_for(model_name, filePath=path), model=_model(model_name))
        assert len(model.filePath) == MAX_FILE_PATH_LENGTH

    @pytest.mark.parametrize("model_name", FILE_MODELS_WITH_PATH)
    def test_accepts_path_with_spaces_and_unicode(self, model_name):
        """Real asset file paths legitimately contain spaces and non-ASCII names."""
        model = parse(
            _body_for(model_name, filePath="/scans/façade détail 01.glb"),
            model=_model(model_name),
        )
        assert model.filePath == "/scans/façade détail 01.glb"

    @pytest.mark.parametrize("model_name", FILE_MODELS_WITH_PATH)
    def test_rejects_path_traversal(self, model_name):
        with pytest.raises(ValidationError):
            parse(_body_for(model_name, filePath="/folder/../../etc/passwd"),
                  model=_model(model_name))

    @pytest.mark.parametrize("model_name", FILE_MODELS_WITH_PATH)
    def test_normalizes_missing_leading_slash(self, model_name):
        model = parse(_body_for(model_name, filePath="folder/file.glb"),
                      model=_model(model_name))
        assert model.filePath == "/folder/file.glb"


@pytest.mark.unit
class TestMetadataKeyElementBounds:
    @pytest.mark.parametrize("model_name", MODELS_WITH_KEYS)
    def test_rejects_oversized_metadata_key_element(self, model_name):
        """A per-request count ceiling alone leaves each element unbounded."""
        with pytest.raises(ValidationError):
            parse({"metadataKeys": ["k" * 257]}, model=_model(model_name))

    @pytest.mark.parametrize("model_name", MODELS_WITH_KEYS)
    def test_accepts_metadata_key_element_at_ceiling(self, model_name):
        model = parse({"metadataKeys": ["k" * 256]}, model=_model(model_name))
        assert len(model.metadataKeys[0]) == 256

    @pytest.mark.parametrize("model_name", MODELS_WITH_KEYS)
    def test_accepts_realistic_metadata_keys(self, model_name):
        model = parse({"metadataKeys": ["colour", "material", "façade-type"]},
                      model=_model(model_name))
        assert len(model.metadataKeys) == 3

    def test_rejects_oversized_key_on_file_delete(self):
        from models.metadata import DeleteFileMetadataRequestModel
        body = _body_for("DeleteFileMetadataRequestModel", metadataKeys=["k" * 257])
        with pytest.raises(ValidationError):
            parse(body, model=DeleteFileMetadataRequestModel)

    def test_accepts_realistic_keys_on_file_delete(self):
        from models.metadata import DeleteFileMetadataRequestModel
        body = _body_for("DeleteFileMetadataRequestModel", metadataKeys=["colour", "size"])
        model = parse(body, model=DeleteFileMetadataRequestModel)
        assert model.metadataKeys == ["colour", "size"]

    def test_oversized_key_only_flagged_when_present(self):
        """The element bound must not turn a short-key request into a rejection."""
        from models.metadata import DeleteAssetMetadataRequestModel
        model = parse({"metadataKeys": ["a"]}, model=DeleteAssetMetadataRequestModel)
        assert model.metadataKeys == ["a"]
