# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The DynamoDB tables and indexes as the CDK storage stack defines them, read from its source.

`storageBuilder-nestedStack.ts` declares every table as `const <var> = new dynamodb.Table(scope,
"<id>", { partitionKey, sortKey, ... })` and every index as `<var>.addGlobalSecondaryIndex({
indexName, partitionKey, sortKey, ... })` (or `addLocalSecondaryIndex`), and publishes each table's
name under `[RESOURCE_PARAM_KEYS.dynamoTables.<key>]: storageResources.dynamo.<field>.tableName`,
which is the `param_key` (`dynamoTables/<key>`) the backend's `ResourceKeys` resolve. So a test can
build a table double with the deployed key schema, and find the keys of whatever table or index a
handler queries, without a hand-copied definition that can drift from the CDK.
"""

import os
import re
from functools import lru_cache
from typing import Dict, List, Optional, Tuple

STORAGE_STACK = os.path.abspath(os.path.join(
    os.path.dirname(__file__), "..", "..", "..", "..", "infra", "lib", "nestedStacks", "storage",
    "storageBuilder-nestedStack.ts"))

_TABLE = re.compile(r'const (\w+) = new dynamodb\.Table\(\s*scope,\s*"([^"]+)",\s*\{')
_INDEX = re.compile(r'(\w+)\.add(Global|Local)SecondaryIndex\(\s*\{')
_KEY = r'{0}:\s*\{{\s*name:\s*"([^"]+)",\s*type:\s*dynamodb\.AttributeType\.(\w+)'
_INDEX_NAME = re.compile(r'indexName:\s*"([^"]+)"')
_PARAM = re.compile(
    r'\[RESOURCE_PARAM_KEYS\.dynamoTables\.(\w+)\]:\s*storageResources\.dynamo\.(\w+)\.tableName')
_DYNAMO_FIELD = re.compile(r'^\s*(\w+):\s*(\w+),?\s*$', re.MULTILINE)
_TYPES = {"STRING": "S", "NUMBER": "N", "BINARY": "B"}

# The environment as it was when the test modules, and the handler modules they import, were
# collected. Session fixtures of other test directories (assetLinks, assets) set some table-name
# variables to other values later and do not restore them, while a handler module keeps the name it
# read at import, so names are resolved from this snapshot as well as from the current environment.
_ENVIRONMENT_AT_COLLECTION = dict(os.environ)


def _block(text: str, opening: int) -> str:
    """The text of the `{...}` block whose opening brace is at `opening`."""
    depth = 0
    for position in range(opening, len(text)):
        if text[position] == "{":
            depth += 1
        elif text[position] == "}":
            depth -= 1
            if depth == 0:
                return text[opening:position + 1]
    raise ValueError("unbalanced block in the storage stack source")


def _key(block: str, which: str) -> Optional[Tuple[str, str]]:
    match = re.search(_KEY.format(which), block)
    return (match.group(1), _TYPES[match.group(2)]) if match else None


class CdkTable:
    """One table: its key, and the key of each of its indexes."""

    def __init__(self, var: str, logical_id: str, partition: Tuple[str, str],
                 sort: Optional[Tuple[str, str]]):
        self.var = var
        self.logical_id = logical_id
        self.partition = partition
        self.sort = sort
        # index name -> (kind, partition, sort)
        self.indexes: Dict[str, Tuple[str, Tuple[str, str], Optional[Tuple[str, str]]]] = {}

    def key_attributes(self, index_name: Optional[str] = None) -> Tuple[str, ...]:
        """The key attribute names of the table (`index_name` None) or of one of its indexes."""
        if index_name is None:
            partition, sort = self.partition, self.sort
        else:
            _, partition, sort = self.indexes[index_name]
        return tuple(key[0] for key in (partition, sort) if key)

    def create_table_kwargs(self, table_name: str) -> dict:
        """`create_table` kwargs for a double of this table (on-demand, projections ALL)."""
        attributes = {}

        def schema(partition, sort):
            attributes[partition[0]] = partition[1]
            entries = [{"AttributeName": partition[0], "KeyType": "HASH"}]
            if sort:
                attributes[sort[0]] = sort[1]
                entries.append({"AttributeName": sort[0], "KeyType": "RANGE"})
            return entries

        kwargs = {"TableName": table_name, "KeySchema": schema(self.partition, self.sort),
                  "BillingMode": "PAY_PER_REQUEST"}
        globals_, locals_ = [], []
        for name, (kind, partition, sort) in self.indexes.items():
            entry = {"IndexName": name, "KeySchema": schema(partition, sort),
                     "Projection": {"ProjectionType": "ALL"}}
            (globals_ if kind == "Global" else locals_).append(entry)
        if globals_:
            kwargs["GlobalSecondaryIndexes"] = globals_
        if locals_:
            kwargs["LocalSecondaryIndexes"] = locals_
        kwargs["AttributeDefinitions"] = [{"AttributeName": name, "AttributeType": kind}
                                          for name, kind in attributes.items()]
        return kwargs


@lru_cache(maxsize=1)
def storage_tables() -> Dict[str, CdkTable]:
    """Every table of the storage stack, by the `param_key` its name is published under
    (`dynamoTables/<key>`). Tables that publish no name are left out."""
    with open(STORAGE_STACK, encoding="utf-8") as handle:
        text = handle.read()
    by_var: Dict[str, CdkTable] = {}
    for match in _TABLE.finditer(text):
        block = _block(text, match.end() - 1)
        by_var[match.group(1)] = CdkTable(match.group(1), match.group(2),
                                          _key(block, "partitionKey"), _key(block, "sortKey"))
    for match in _INDEX.finditer(text):
        table = by_var.get(match.group(1))
        if table is None:
            continue
        block = _block(text, match.end() - 1)
        kind = match.group(2)
        partition = _key(block, "partitionKey") if kind == "Global" else table.partition
        table.indexes[_INDEX_NAME.search(block).group(1)] = (kind, partition, _key(block, "sortKey"))
    dynamo_start = text.index("dynamo: {", text.index("const storageResources"))
    dynamo_block = _block(text, dynamo_start + len("dynamo: "))
    field_to_var = {field: var for field, var in _DYNAMO_FIELD.findall(dynamo_block)
                    if var in by_var}
    tables = {}
    for key, field in _PARAM.findall(text):
        if field in field_to_var:
            tables[f"dynamoTables/{key}"] = by_var[field_to_var[field]]
    return tables


def table_for_resource_key(resource_key) -> CdkTable:
    """The CDK table a backend `ResourceKeys` entry names."""
    return storage_tables()[resource_key.param_key]


def tables_by_test_name() -> Dict[str, Tuple[str, CdkTable]]:
    """`{table name the test environment resolves: (ResourceKeys attribute, CdkTable)}` for every
    `ResourceKeys` table entry resolved from an environment variable and published by the storage
    stack. A handler module's `dynamodb.Table(get_table_name(...))` carries that name."""
    from common.resourceNames import ResourceKeys, ResourceParamKey
    names: Dict[str, Tuple[str, CdkTable]] = {}
    for attribute, key in vars(ResourceKeys).items():
        if not isinstance(key, ResourceParamKey) or key.param_key not in storage_tables():
            continue
        for environment in (os.environ, _ENVIRONMENT_AT_COLLECTION):
            for env_name in key.env_var_names:
                value = environment.get(env_name)
                if value:
                    names.setdefault(value, (attribute, storage_tables()[key.param_key]))
                    break
    return names


def all_tables() -> List[CdkTable]:
    return list(storage_tables().values())


def _is_table_resource(value) -> bool:
    from boto3.resources.base import ServiceResource
    return isinstance(value, ServiceResource) and value.meta.resource_model.name == "Table"


def table_doubles(resource, modules):
    """`(patches, doubles_by_key)` for every DynamoDB table resource a handler module holds as a
    module attribute: a double of the CDK table (created on `resource`, a moto-backed DynamoDB
    resource, under the name the test environment resolves) and an unstarted `patch.object` per
    attribute, so aliases (`asset_state_table = store.asset_state_table`) share one double.
    `doubles_by_key` maps the `dynamoTables/<key>` suffix (`complianceAssetStateStorage`) to the
    double. Raises when a module holds a table the storage stack does not publish."""
    from unittest.mock import patch
    by_name = tables_by_test_name()
    doubles: Dict[str, object] = {}
    patches, unknown = [], []
    for module in modules:
        for attribute, value in vars(module).items():
            if not _is_table_resource(value):
                continue
            if value.name not in by_name:
                unknown.append(f"{module.__name__}.{attribute} ({value.name})")
                continue
            if value.name not in doubles:
                resource.create_table(**by_name[value.name][1].create_table_kwargs(value.name))
                doubles[value.name] = resource.Table(value.name)
            patches.append(patch.object(module, attribute, doubles[value.name]))
    if unknown:
        raise AssertionError(f"tables not published by the storage stack: {sorted(unknown)}")
    keys = {id(table): key.split("/", 1)[1] for key, table in storage_tables().items()}
    doubles_by_key = {keys[id(by_name[name][1])]: double for name, double in doubles.items()}
    return patches, doubles_by_key
