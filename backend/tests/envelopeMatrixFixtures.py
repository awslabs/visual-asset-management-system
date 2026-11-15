# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Fixtures and builders shared by the workflow-execution input envelope combination tests
(tests/handlers/workflows/test_envelope_combination_matrix.py).

Everything here runs the execute handler's real envelope assembly (`_build_grouped_metadata`) and per
step delivery (`narrow_metadata_envelope`) with the metadata service, the S3 listing and the schema
reads stubbed by fixed data. It only uses names that also exist in the execute path that predates the
opt-in metadataInputs keys, so the same builders produced the golden digests the byte-identity tests
compare against (golden/envelope_matrix_without_opt_in_keys.json): a test whose golden no longer
matches has changed what a pipeline receives."""

import copy
import hashlib
import itertools
import json
import os
from unittest.mock import MagicMock, patch

# The execute handler reads these at import (mirrors test_executeWorkflow.py).
for _name, _value in (
        ("ASSET_STORAGE_TABLE_NAME", "t-assets"),
        ("WORKFLOW_STORAGE_TABLE_V2_NAME", "t-wf-v2"),
        ("PIPELINE_STORAGE_TABLE_V2_NAME", "t-pipe-v2"),
        ("PIPELINE_TEMPLATES_STORAGE_TABLE_NAME", "t-templates"),
        ("PIPELINE_TEMPLATE_TAG_SCHEMA_STORAGE_TABLE_NAME", "t-tagschema"),
        ("S3_ASSET_BUCKETS_STORAGE_TABLE_NAME", "t-buckets"),
        ("S3_ASSETAUXILIARY_STORAGE_BUCKET", "t-aux"),
        ("METADATA_SERVICE_LAMBDA_FUNCTION_NAME", "t-md-svc"),
        ("WORKFLOW_EXECUTION_STORAGE_TABLE_V2_NAME", "t-exec-v2"),
        ("PIPELINE_EXECUTIONS_STORAGE_TABLE_NAME", "t-pexec"),
        ("PIPELINE_EXECUTION_INPUT_METADATA_STORAGE_TABLE_NAME", "t-pin-md"),
        ("PIPELINE_EXECUTION_INPUT_CONFIGURATION_STORAGE_TABLE_NAME", "t-pin-cfg"),
        ("WORKFLOW_EXECUTION_INPUTS_STORAGE_TABLE_NAME", "t-wf-inputs"),
        ("WORKFLOW_EXECUTION_CONFIGURATION_STORAGE_TABLE_NAME", "t-wf-cfg")):
    os.environ.setdefault(_name, _value)

from backend.backend.handlers.workflows import executeWorkflow as ew  # noqa: E402
from backend.backend.common.workflows import executionRecords as er  # noqa: E402

MOD = "backend.backend.handlers.workflows.executeWorkflow"

FOUR_KEYS = ("assetMetadata", "fileMetadata", "fileAttributes", "databaseMetadata")
OPT_IN_KEYS = ("assetFileMetadata", "metadataSchema")
SIX_KEYS = FOUR_KEYS + OPT_IN_KEYS
# What an omitted key means: the four metadata types are on, the two opt-in sections off.
DEFAULTS = {**{k: True for k in FOUR_KEYS}, **{k: False for k in OPT_IN_KEYS}}

ABSENT = "absent"
TRI_STATE = (True, False, ABSENT)
GOLDEN_DIGEST_LENGTH = 16


#######################
# Gate maps
#######################

def gate_map(values):
    """A metadataInputs map from {key: True|False|ABSENT}: an ABSENT key is omitted."""
    return {k: v for k, v in values.items() if v is not ABSENT}


def combos(keys, values=TRI_STATE):
    """Every metadataInputs map over `keys`, each key taking each of `values`, in a fixed order."""
    return [gate_map(dict(zip(keys, choice))) for choice in itertools.product(values, repeat=len(keys))]


def combo_id(metadata_inputs, keys):
    """'T' / 'F' / '-' per key, so a case names the map it ran with."""
    def mark(key):
        if key not in metadata_inputs:
            return "-"
        return "T" if metadata_inputs[key] else "F"
    return "".join(mark(k) for k in keys)


def normalized(metadata_inputs):
    """The six keys as booleans, an omitted key at its default (what the gate means)."""
    return {k: bool(metadata_inputs[k]) if k in (metadata_inputs or {}) else DEFAULTS[k]
            for k in SIX_KEYS}


#######################
# The fixed world the envelope is read from
#######################

def _entries(**pairs):
    return [{"metadataKey": k, "metadataValue": v} for k, v in pairs.items()]


READS = {
    ("asset", "db1", "a1"): _entries(title="A1"),
    ("asset", "db1", "a2"): _entries(title="A2"),
    ("asset", "db2", "a3"): _entries(title="A3"),
    ("asset", "db3", "a4"): _entries(title="A4"),
    ("asset", "db2", "a9"): _entries(title="A9"),
    ("metadata", "db1", "a1", "/x.glb"): _entries(material="steel"),
    ("attribute", "db1", "a1", "/x.glb"): _entries(units="mm"),
    ("metadata", "db1", "a1", "/y.glb"): _entries(material="iron"),
    ("metadata", "db1", "a2", "/b.glb"): _entries(material="wood"),
    ("attribute", "db1", "a2", "/b.glb"): _entries(units="cm"),
    ("metadata", "db1", "a2", "/sub/c.usd"): _entries(material="glass"),
    ("metadata", "db1", "a2", "/README"): _entries(language="en"),
    ("metadata", "db2", "a3", "/folder/m.obj"): _entries(material="clay"),
    ("attribute", "db2", "a3", "/folder/m.mtl"): _entries(shader="pbr"),
    ("metadata", "db3", "a4", "/sub/p.e57"): _entries(sensor="faro"),
    ("database", "db1"): _entries(region="north"),
    ("database", "db2"): _entries(region="south"),
    ("database", "db3"): _entries(region="east"),
}

# Objects under each asset's location 'assets/<assetId>/'. README and the two LICENSE files have no
# extension (one under a folder whose name has a dot), in databases with an extension-restricted schema.
LISTINGS = {
    "assets/a1/": ["assets/a1/x.glb", "assets/a1/y.glb"],
    "assets/a2/": ["assets/a2/", "assets/a2/README", "assets/a2/a.b/LICENSE", "assets/a2/b.glb",
                   "assets/a2/b.glb.previewFile.png", "assets/a2/sub/", "assets/a2/sub/c.usd",
                   "assets/a2/z.txt"],
    "assets/a3/": ["assets/a3/folder/", "assets/a3/folder/m.obj", "assets/a3/folder/m.mtl",
                   "assets/a3/other.txt"],
    "assets/a4/": ["assets/a4/LICENSE", "assets/a4/top.las", "assets/a4/sub/p.e57",
                   "assets/a4/sub/q.e57"],
}


def _input(database_id, asset_id, relative_key):
    return {"databaseId": database_id, "assetId": asset_id, "relativeFileKey": relative_key,
            "versionId": ""}


# Each selection: (inputs, metadata-source assets, metadata-source databases). 'mixed' carries every
# record kind at once (a file, a whole asset and a folder in two databases, plus a source asset).
SELECTIONS = {
    "mixed": ([_input("db1", "a1", "/x.glb"), _input("db1", "a2", "/"),
               _input("db2", "a3", "/folder/")],
              [{"databaseId": "db2", "assetId": "a9"}], ["db1", "db2"]),
    "file": ([_input("db1", "a1", "/x.glb")], [], ["db1"]),
    "folder": ([_input("db2", "a3", "/folder/")], [], ["db2"]),
    "wholeAsset": ([_input("db1", "a2", "/")], [], ["db1"]),
}

# The run-shaped selections the launch tests use (the metadata sources follow from the inputs).
LAUNCH_SELECTIONS = {
    "oneFile": [_input("db1", "a1", "/x.glb")],
    "severalFiles": [_input("db1", "a1", "/x.glb"), _input("db1", "a1", "/y.glb")],
    "folder": [_input("db2", "a3", "/folder/")],
    "wholeAsset": [_input("db1", "a2", "/")],
    "folderAndAFileInIt": [_input("db2", "a3", "/folder/"), _input("db2", "a3", "/folder/m.obj")],
    "twoAssets": [_input("db1", "a1", "/x.glb"), _input("db1", "a2", "/")],
    "twoDatabases": [_input("db2", "a3", "/folder/"), _input("db3", "a4", "/")],
}


def asset_record(database_id, asset_id):
    return {"databaseId": database_id, "assetId": asset_id, "assetName": asset_id,
            "description": "", "tags": [], "bucketId": "bkt-id",
            "assetLocation": {"Key": f"assets/{asset_id}/"}}


def asset_records(inputs, sources=()):
    return {(i["databaseId"], i["assetId"]): asset_record(i["databaseId"], i["assetId"])
            for i in list(inputs) + list(sources)}


def list_objects(**kwargs):
    """ListObjectsV2 over LISTINGS: the keys under the requested Prefix in key order, one page."""
    prefix = kwargs["Prefix"]
    keys = sorted({k for keys in LISTINGS.values() for k in keys if k.startswith(prefix)})
    return {"Contents": [{"Key": k} for k in keys], "IsTruncated": False}


#######################
# Metadata schemas: db1 has schemas, db2 none, db3 schemas and restrictMetadataOutsideSchemas
#######################

def _field(name, value_type="string", required=False):
    return {"metadataFieldKeyName": name, "metadataFieldValueType": value_type, "required": required,
            "controlledListKeys": None, "defaultMetadataFieldValue": None, "sequence": None,
            "dependsOnFieldKeyName": None}


def _schema(database_id, name, entity_type, fields, restriction=""):
    keyed = {f["metadataFieldKeyName"]: f for f in fields}
    return {"databaseId": database_id, "schemaName": name, "metadataSchemaEntityType": entity_type,
            "fileKeyTypeRestriction": restriction, "fields": keyed, "metadataSchemaFields": keyed}


SCHEMAS = {
    ("db1", "assetMetadata"): [_schema("db1", "Asset", "assetMetadata", [_field("title", required=True)])],
    ("db1", "fileMetadata"): [_schema("db1", "Mesh", "fileMetadata", [_field("material")], ".glb,.usd")],
    ("db3", "fileMetadata"): [_schema("db3", "Scan", "fileMetadata", [_field("sensor", required=True)],
                                      ".e57")],
    ("GLOBAL", "fileAttribute"): [_schema("GLOBAL", "Units", "fileAttribute", [_field("units")])],
}
RESTRICTING_DATABASES = {"db3"}


def schema_patches(dynamodb=None):
    """Patches that let the schema section read SCHEMAS for a caller allowed everything. `create=True`
    because the execute path that predates the section has none of these names; there they are inert."""
    casbin = MagicMock()
    casbin.return_value.enforce.return_value = True
    casbin.return_value.enforceAPI.return_value = True
    database_table = MagicMock()
    database_table.get_item.side_effect = lambda Key, **_k: {"Item": {
        "restrictMetadataOutsideSchemas": Key["databaseId"] in RESTRICTING_DATABASES}}
    if dynamodb is None:
        dynamodb = MagicMock()
        dynamodb.Table.return_value = database_table
    return [
        patch(f"{MOD}.CasbinEnforcer", casbin, create=True),
        patch(f"{MOD}.claims_and_roles", {"tokens": ["user1"]}, create=True),
        patch(f"{MOD}.load_enabled_schemas", create=True,
              side_effect=lambda d, t, _c, _t: [copy.deepcopy(s) for s in SCHEMAS.get((d, t), [])]),
        patch(f"{MOD}.dynamodb", dynamodb),
    ], database_table


#######################
# Builders
#######################

def build_envelope(metadata_inputs, selection="mixed", notices=None, s3=None):
    """The shared envelope the execute path gathers for `selection` under the gate `metadata_inputs`
    (the intake gate, already narrowed to what some step asks for)."""
    inputs, sources, databases = SELECTIONS[selection]
    s3_mock = s3 or MagicMock()
    if s3 is None:
        s3_mock.list_objects_v2.side_effect = list_objects
    patches = [
        patch(f"{MOD}._fetch_metadata", side_effect=lambda d, a, _q, _e: READS.get(("asset", d, a), [])),
        patch(f"{MOD}._fetch_file_metadata",
              side_effect=lambda d, a, r, kind, _e: READS.get((kind, d, a, r), [])),
        patch(f"{MOD}._fetch_database_metadata", side_effect=lambda d, _e: READS.get(("database", d), [])),
        patch(f"{MOD}._asset_bucket_details", return_value={"bucketName": "bkt"}),
        patch(f"{MOD}.s3c", s3_mock),
    ] + schema_patches()[0]
    for p in patches:
        p.start()
    try:
        return ew._build_grouped_metadata(
            list(inputs), asset_records(inputs, sources), dict(metadata_inputs), {},
            metadata_source_assets=list(sources), metadata_source_databases=list(databases),
            notices=notices)
    finally:
        for p in reversed(patches):
            p.stop()


def delivered(envelope, step_metadata_inputs):
    """What one step receives out of the shared envelope under its own effective gate."""
    return er.narrow_metadata_envelope(envelope, dict(step_metadata_inputs))


def envelope_bytes(envelope):
    """The exact bytes the execute path writes to S3 for an envelope."""
    return json.dumps(envelope).encode("utf-8")


def digest(data):
    return hashlib.sha256(data).hexdigest()[:GOLDEN_DIGEST_LENGTH]


def row_digest(chunks):
    return digest(b"\n".join(chunks))


def golden_for(selection, build=build_envelope, deliver=delivered):
    """The byte-identity record of one selection, over every map of the four metadata types (each on,
    off or omitted) at the workflow and at the step:
      shared[W]   the shared envelope's digest for workflow map W;
      delivered[W] one digest over the 81 delivered envelopes of W, the step maps in combos() order;
      pairs[W|S]  per boolean pair, the delivered envelope's digest (what a single pipeline is
                  compared against)."""
    four = combos(FOUR_KEYS)
    record = {"shared": {}, "delivered": {}, "pairs": {}}
    for workflow_map in four:
        envelope = build(workflow_map, selection)
        wid = combo_id(workflow_map, FOUR_KEYS)
        record["shared"][wid] = digest(envelope_bytes(envelope))
        record["delivered"][wid] = row_digest(
            [envelope_bytes(deliver(envelope, step_map)) for step_map in four])
    for workflow_map in combos(FOUR_KEYS, (True, False)):
        envelope = build(workflow_map, selection)
        for step_map in combos(FOUR_KEYS, (True, False)):
            key = f"{combo_id(workflow_map, FOUR_KEYS)}|{combo_id(step_map, FOUR_KEYS)}"
            record["pairs"][key] = digest(envelope_bytes(deliver(envelope, step_map)))
    return record
