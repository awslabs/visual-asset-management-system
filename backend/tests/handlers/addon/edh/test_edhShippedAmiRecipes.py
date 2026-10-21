# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Every recipe shipped under backendAddons/edh/amiRecipes, read from disk the way the deploy-time
seeding reads its upload: the manifest the seeding builds passes the recipe request model, and every
inline component resolves within the component limit with the longest secret prefix the deployment
accepts. A model failure at seeding is only a warning on the deploy that leaves the old recipe row in
place, and an unresolved placeholder surfaces only when an administrator chooses Build now."""

import os
import re
from unittest.mock import patch

import pytest

from backend.backend.handlers.addon.edh import edhCatalogRegistration as reg
from backend.backend.handlers.addon.edh.common import edhImageBuilder as builder
from backend.backend.models.addon.edh import AmiRecipeRequestModel

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", ".."))
_RECIPES = os.path.join(_REPO_ROOT, "backendAddons", "edh", "amiRecipes")
# The add-on's config module: the EDH defaults and validation getConfig() applies.
_EDH_CONFIG = os.path.join(_REPO_ROOT, "infra", "config", "addon", "edhWorkstationsConfig.ts")
_SHIPPED = sorted(entry.name for entry in os.scandir(_RECIPES) if entry.is_dir())
_BUCKET = "artefacts-bucket"


def _longest_secret_prefix():
    """The longest sessionBootstrap.secretNamePrefix getConfig() accepts, read from the config source."""
    with open(_EDH_CONFIG, encoding="utf-8") as handle:
        match = re.search(r"EDH_SESSION_SECRET_PREFIX_MAX_LENGTH = (\d+);", handle.read())
    assert match, "EDH_SESSION_SECRET_PREFIX_MAX_LENGTH not found in infra/config/addon/edhWorkstationsConfig.ts"
    return "a" * (int(match.group(1)) - 1) + "/"


def _seeded_manifest(recipe_id):
    """The manifest the seeding's _read_bundle builds, with the uploaded bundle served from disk."""
    prefix = f"addon/edh/amiRecipes/bundlehash/{recipe_id}"

    def read_object(bucket, key):
        assert bucket == _BUCKET and key.startswith(prefix + "/"), key
        relative = key[len(prefix) + 1:]
        with open(os.path.join(_RECIPES, recipe_id, relative), encoding="utf-8", newline="") as handle:
            return handle.read()

    with patch.object(reg, "_read_object", side_effect=read_object):
        return reg._read_bundle(_BUCKET, {"prefix": prefix})


def test_the_walk_finds_the_shipped_recipes():
    assert {"al2023-desktop", "isaacsim", "windows2022-desktop"} <= set(_SHIPPED)


@pytest.mark.parametrize("recipe_id", _SHIPPED)
def test_the_seeded_manifest_passes_the_recipe_model(recipe_id):
    model = AmiRecipeRequestModel(**_seeded_manifest(recipe_id))
    assert model.recipeId == recipe_id


@pytest.mark.parametrize("recipe_id", _SHIPPED)
def test_every_inline_component_resolves_with_the_longest_prefix(recipe_id):
    manifest = _seeded_manifest(recipe_id)
    documents = builder.component_documents(
        manifest, _BUCKET, {"sessionSecretPrefix": _longest_secret_prefix()})
    inline = [document for document in documents if document is not None]
    assert inline, f"{recipe_id} carries no inline component"
    assert [document for document in inline if builder._PLACEHOLDER.search(document)] == []
