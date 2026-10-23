# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The definition document: fetched from its S3 pointer (or a local file), validated against the shipped
schemas (read through the package's stem-keyed `load_schema`)."""

import json

import jsonschema

from . import load_schema
from .errors import INPUT_REJECTED, PIPELINE_ERROR, PipelineRejection
from .profileRules import model_key_errors, profile_errors

DEFINITION_SCHEMA = "definition_schema"
CONFIG_SCHEMA = "config_schema"


def parse_s3_uri(uri):
    if not uri.startswith("s3://"):
        raise ValueError(f"not an S3 URI: {uri}")
    bucket, _, key = uri[len("s3://"):].partition("/")
    if not bucket or not key:
        raise ValueError(f"S3 URI needs a bucket and a key: {uri}")
    return bucket, key


def _validate(instance, schema_name, label):
    try:
        jsonschema.validate(instance, load_schema(schema_name))
    except jsonschema.ValidationError as exc:
        where = "/".join(str(part) for part in exc.absolute_path) or "<root>"
        raise PipelineRejection(PIPELINE_ERROR, f"{label} invalid at {where}: {exc.message}") from exc


def validate_definition(definition):
    _validate(definition, DEFINITION_SCHEMA, "definition document")
    config = definition.get("config", {})
    # The rules constructPipeline applied, in its order: a model key on its own, then the schema, then
    # what the schema cannot express (profileRules.py is the same file in both places).
    model_errors = model_key_errors(config)
    if model_errors:
        raise PipelineRejection(INPUT_REJECTED, " ".join(model_errors))
    _validate(config, CONFIG_SCHEMA, "definition document config block")
    errors = profile_errors(config)
    if errors:
        raise PipelineRejection(INPUT_REJECTED, " ".join(errors))
    return definition


def load_definition(uri_or_path, clients):
    if uri_or_path.startswith("s3://"):
        bucket, key = parse_s3_uri(uri_or_path)
        text = clients.s3.get_object(Bucket=bucket, Key=key)["Body"].read().decode("utf-8")
    else:
        with open(uri_or_path, "r", encoding="utf-8") as handle:
            text = handle.read()
    try:
        definition = json.loads(text)
    except json.JSONDecodeError as exc:
        raise PipelineRejection(PIPELINE_ERROR, f"definition document is not valid JSON: {exc}") from exc
    return validate_definition(definition)
