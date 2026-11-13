# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""documentation/VAMS_API.yaml repeats no key within a mapping (#1498).

YAML forbids a repeated key in one mapping. PyYAML's default loaders keep the last value without a
word, so the contract tests that load the spec would not notice; strict parsers (OpenAPI validators,
client generators, ruamel.yaml) reject the whole document. This loader refuses the repeat and names
the key and both lines.
"""

from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

SPEC_PATH = Path(__file__).resolve().parents[3] / "documentation" / "VAMS_API.yaml"


class _DuplicateKeyRefusingLoader(yaml.SafeLoader):
    """A SafeLoader that raises on a key repeated in one mapping."""


def _construct_mapping_refusing_duplicates(loader, node, deep=False):
    seen = {}
    for key_node, _value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        line = key_node.start_mark.line + 1
        if key in seen:
            raise yaml.constructor.ConstructorError(
                "while constructing a mapping", node.start_mark,
                f"duplicate key {key!r} at line {line} (first at line {seen[key]})",
                key_node.start_mark)
        seen[key] = line
    return loader.construct_mapping(node, deep=deep)


_DuplicateKeyRefusingLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_mapping_refusing_duplicates)


def test_spec_has_no_duplicate_mapping_keys():
    with SPEC_PATH.open(encoding="utf-8") as spec:
        yaml.load(spec, Loader=_DuplicateKeyRefusingLoader)  # raises on a repeated key


def test_loader_refuses_a_repeated_key():
    with pytest.raises(yaml.constructor.ConstructorError, match="duplicate key 'errorMessage'"):
        yaml.load("a:\n  errorMessage: 1\n  errorMessage: 2\n", Loader=_DuplicateKeyRefusingLoader)
