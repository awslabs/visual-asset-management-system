# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""A metadata schema's file-type restriction is matched against the FILE NAME's extension.

`extract_file_extension` read the extension from the whole path (`'.' + file_path.rsplit('.', 1)[-1]`),
so a dot anywhere in a parent folder name was taken for an extension: `folder.v2/LICENSE` resolved to
`.v2/license`. That value matches no `fileKeyTypeRestriction` entry any operator would ever write, so
every restricted schema was silently skipped for such a path -- the fields it declares were not
required, its controlled lists constrained nothing, and `restrictMetadataOutsideSchemas` stayed off
for that file, all with a 200 response. Dotted folder names are ordinary (`rev.2/`, `data.raw/`), and
extension-less files (`LICENSE`, `Dockerfile`, `README`) are ordinary in CAD and data-export bundles.

Root-level `LICENSE` and `folder.v2/LICENSE` must agree, and they do: both have no extension. Which
schemas such a file meets is the maintainers' decision of #1036: a restricted schema applies only to
files with one of its extensions, so an extension-less file meets only the schemas with no file type
restriction. The filter used to run only `if file_extension`, so `None` meant "no filtering" and
every restricted schema applied to `LICENSE` as well; that is the behaviour pinned as fixed below.

Three things this file exists to stop drifting:

* **The returned form stays DOTTED** (`.glb`). It is compared at `get_aggregated_schemas` against
  `fileKeyTypeRestriction` entries, which the schema editor and the CLI both specify dotted. The
  same-named helpers in `handlers/indexing/fileIndexer.py` and
  `handlers/addon/garnetFramework/garnetDataIndexFile.py` return the UNDOTTED form (`glb`) because
  they feed the search index's `str_fileext` field. Unifying the three into one helper needs both
  forms; a verbatim copy of either into the other's place silently stops every stored restriction
  from matching.
* **An undotted stored restriction (`glb`) matches nothing**, because the extracted value is dotted.
  The request models in `models/metadataSchema.py` refuse an undotted entry on create and update,
  but a row already in the table is not rewritten; the matcher applies such a row to no file rather
  than normalising it, pinned below so that changing that is a visible decision.
* **No restriction entry means "no extension".** Entries are compared as stripped, lower-cased
  dotted extensions, an extension-less file has none to compare, and the request models refuse an
  empty entry or a bare dot, so a stored list cannot opt extension-less files in. `.all` and an
  empty restriction are the only ways a schema reaches them.

Sibling coverage over the same module: `test_metadataSchemaValidation_paging.py` (the query pages to
exhaustion), `test_metadataSchemaValidation_fetch_fails_closed.py` (a failed query refuses rather than
returns a partial aggregate), and `test_metadataSchema_required_retroactive.py` (defaults are applied
BEFORE the required-field check, which is why the matcher scenarios below use a required field with no
`defaultMetadataFieldValue`).
"""

import pytest
from unittest.mock import MagicMock

from backend.backend.common import metadataSchemaValidation as msv
from backend.backend.common.metadataSchemaValidation import (
    extract_file_extension,
    get_aggregated_schemas,
)
from backend.tests.pagingStub import Pager

_TABLE = "test-metadata-schema-table"
_ENTITY = "fileMetadata"

# The field carried by the schema restricted to `.glb`, and by the schema with no restriction at all.
# Every scenario asserts the UNRESTRICTED field is present, which is what keeps the "the restricted
# schema was skipped" assertions from being satisfied by a reader that returned nothing.
_RESTRICTED_FIELD = "glbOnlyField"
_UNRESTRICTED_FIELD = "everyFileField"


@pytest.fixture(autouse=True)
def _clear_schema_cache():
    """The 60-second aggregate cache is a module global with no per-test reset.

    It matters more here than elsewhere: the cache key is `f"{dbs}:{entity}:{extension_key}"`, with
    `extension_key` the extension, `"no_ext"` for an extension-less file and `"all_files"` when no
    file filtering applies, and several scenarios below compare aggregates across those keys. A
    leaked entry would answer from another scenario's key.
    """
    msv._schema_cache.clear()
    yield
    msv._schema_cache.clear()


def _schema_item(schema_id, field_name, restriction=None, required=True):
    """One typed DynamoDB schema row, optionally carrying a `fileKeyTypeRestriction`.

    `required=True` with no `defaultMetadataFieldValue`: defaults are applied before the
    required-field check, so a field carrying a default would pass validation whether or not its
    schema applied.
    """
    item = {
        "metadataSchemaId": {"S": schema_id},
        "databaseId": {"S": "db1"},
        "schemaName": {"S": f"{schema_id}-name"},
        "metadataEntityType": {"S": _ENTITY},
        "enabled": {"BOOL": True},
        "fields": {
            "L": [
                {
                    "M": {
                        "metadataFieldKeyName": {"S": field_name},
                        "metadataFieldValueType": {"S": "string"},
                        "required": {"BOOL": required},
                        "sequence": {"N": "1"},
                    }
                }
            ]
        },
    }
    if restriction is not None:
        item["fileKeyTypeRestriction"] = {"S": restriction}
    return item


def _aggregate(file_path, restriction=".glb", entity_type=_ENTITY):
    """The aggregated field names for `file_path`, against a page carrying two schemas.

    The page carries a RESTRICTED schema and an UNRESTRICTED one, and the unrestricted field is
    asserted present before anything is returned. That assertion is the anti-vacuity guard: a stub
    that served nothing (the shape a bare `MagicMock` reader produces) yields an empty aggregate,
    which satisfies every "the restricted schema was skipped" claim below without exercising the
    matcher at all.

    The cache is cleared per call, not just per test. Several scenarios below compare two
    aggregates that share a cache key -- two paths with the same extension, or the same path with
    two different stored restrictions -- and the second call would otherwise be answered from the
    first's entry, which reads as the matcher having applied when nothing ran.
    """
    msv._schema_cache.clear()
    pager = Pager(
        {
            "Items": [
                _schema_item("schema-restricted", _RESTRICTED_FIELD, restriction=restriction),
                _schema_item("schema-any", _UNRESTRICTED_FIELD),
            ]
        },
        name="metadata schema query",
    )
    client = MagicMock()
    client.query.side_effect = pager

    aggregated = get_aggregated_schemas(
        database_ids=["db1"],
        entity_type=entity_type,
        file_path=file_path,
        dynamodb_client=client,
        schema_table_name=_TABLE,
    )

    assert pager.calls, (
        f"nothing read the schema stub for {file_path!r}, so this scenario asserts nothing")
    assert _UNRESTRICTED_FIELD in aggregated, (
        f"the UNRESTRICTED schema is missing for {file_path!r}, so the reader served nothing and "
        f"every restriction assertion here would hold vacuously: {sorted(aggregated)}")
    return aggregated


@pytest.mark.unit
class TestTheExtensionIsReadFromTheFileName:
    """A dot in a folder name is not an extension."""

    @pytest.mark.parametrize("file_path", [
        "folder.v2/LICENSE",
        "/folder.v2/LICENSE",           # the normalized leading-slash form handlers store
        "data.raw/Dockerfile",
        "deep.dir/sub/README",
        "rev.2/Makefile",
    ])
    def test_an_extension_less_file_under_a_dotted_folder_has_no_extension(self, file_path):
        """The defect: each of these resolved to the folder suffix plus the file name.

        `folder.v2/LICENSE` became `.v2/license`, which matches no restriction an operator would
        write, so every restricted schema was skipped for the path.
        """
        assert extract_file_extension(file_path) is None, (
            f"{file_path!r} resolved to {extract_file_extension(file_path)!r}; the extension is read "
            f"from the file name, and this file has none")

    @pytest.mark.parametrize("file_path", ["folder/", "folder.with.dots/", "a.b/c.d/"])
    def test_a_folder_key_has_no_extension(self, file_path):
        """A trailing slash is a folder marker whatever dots the name carries."""
        assert extract_file_extension(file_path) is None

    @pytest.mark.parametrize("file_path", ["LICENSE", "Dockerfile", "/README"])
    def test_a_root_level_extension_less_file_has_no_extension(self, file_path):
        """Unchanged by the fix, and the behaviour the dotted-folder paths now agree with."""
        assert extract_file_extension(file_path) is None

    def test_an_empty_path_has_no_extension(self):
        assert extract_file_extension("") is None


@pytest.mark.unit
class TestTheExtractedFormIsStillTheDottedLowercasedExtension:
    """Positive control for the class above: a fix that returned None for everything fails here.

    It also pins the DOTTED form. The undotted spelling (`glb`) is what the fileIndexer and Garnet
    helpers return, and adopting it here would make every stored `fileKeyTypeRestriction` entry --
    which the UI and CLI both specify dotted -- stop matching, silently and completely.
    """

    @pytest.mark.parametrize("file_path,expected", [
        ("model.glb", ".glb"),
        ("/folder/part.stp", ".stp"),
        ("folder.v2/model.GLB", ".glb"),          # case-folded, and the folder dot is ignored
        ("deep.dir/sub/scan.E57", ".e57"),
        ("archive.tar.gz", ".gz"),                # the LAST dot wins
        (".gitignore", ".gitignore"),             # a dotfile is its own extension today
    ])
    def test_a_real_extension_resolves_to_the_dotted_lowercased_form(self, file_path, expected):
        assert extract_file_extension(file_path) == expected


@pytest.mark.unit
class TestWhichSchemasApplyToAFile:
    """The matcher itself, through `get_aggregated_schemas`."""

    @pytest.mark.parametrize("file_path", [
        "README",
        "/README",
        "LICENSE",
        "folder.v2/LICENSE",
        "/a.b/README",
        "deep.dir/sub/Dockerfile",
    ])
    def test_a_restricted_schema_does_not_apply_to_an_extension_less_file(self, file_path):
        """#1036: an extension-less file meets only the schemas with no file type restriction.

        The filter used to run only `if file_extension`, so every restricted schema applied to these
        paths: a `.glb`-only required field was required on `/README`.
        """
        aggregated = _aggregate(file_path)

        assert _RESTRICTED_FIELD not in aggregated, (
            f"the .glb-restricted schema applied to {file_path!r}, which has no extension: "
            f"{sorted(aggregated)}")

    def test_an_extension_less_file_under_a_dotted_folder_matches_the_root_level_file(self):
        """Stated as an equality so neither path can drift from the other."""
        assert sorted(_aggregate("folder.v2/LICENSE")) == sorted(_aggregate("LICENSE"))

    def test_a_restricted_schema_still_applies_to_a_matching_extension(self):
        """Paired arm. Passes pre- and post-fix, which is what makes the negatives mean something.

        A fix that returned `None` for every path, or that dropped the leading dot, fails here.
        """
        assert _RESTRICTED_FIELD in _aggregate("folder.v2/part.GLB")
        assert _RESTRICTED_FIELD in _aggregate("part.glb")

    def test_a_restricted_schema_is_skipped_for_a_different_extension(self):
        """Paired arm: the restriction still discriminates rather than degrading to match-all."""
        aggregated = _aggregate("folder.v2/part.stp")

        assert _RESTRICTED_FIELD not in aggregated, (
            f"a .glb-restricted schema applied to a .stp file: {sorted(aggregated)}")

    def test_the_all_restriction_applies_to_every_file(self):
        for path in ("folder.v2/LICENSE", "part.stp", "part.glb"):
            assert _RESTRICTED_FIELD in _aggregate(path, restriction=".all"), path

    @pytest.mark.parametrize("restriction", [None, "", "   "])
    def test_an_absent_or_blank_restriction_applies_to_every_file(self, restriction):
        for path in ("folder.v2/LICENSE", "part.stp"):
            assert _RESTRICTED_FIELD in _aggregate(path, restriction=restriction), path

    def test_a_restriction_stored_without_a_leading_dot_matches_nothing(self):
        """Pinned so a normalisation on comparison is a visible change.

        The request models in `models/metadataSchema.py` refuse `glb` on create and update, but a
        row already in the table can carry it, and the extracted value is `.glb`, which never equals
        it. Normalising on comparison would widen which schemas apply and is a separate decision.
        """
        assert _RESTRICTED_FIELD not in _aggregate("part.glb", restriction="glb")
        assert _RESTRICTED_FIELD in _aggregate("part.glb", restriction=".glb")

    def test_the_fileAttribute_entity_type_is_filtered_the_same_way(self):
        """The CDK seeds a GLOBAL `fileAttribute` schema restricted to `.glb,.usd,.obj,...`.

        It is the only restricted schema present on every default deployment, so an extension-less
        file's attributes are where the #1036 change is reachable out of the box.
        """
        for path in ("folder.v2/LICENSE", "README"):
            aggregated = _aggregate(path, restriction=".glb,.usd", entity_type="fileAttribute")
            assert _RESTRICTED_FIELD not in aggregated, (path, sorted(aggregated))
        assert _RESTRICTED_FIELD not in _aggregate(
            "folder.v2/part.stp", restriction=".glb,.usd", entity_type="fileAttribute")
        assert _RESTRICTED_FIELD in _aggregate(
            "folder.v2/part.USD", restriction=".glb,.usd", entity_type="fileAttribute")

    def test_the_restricted_field_set_for_an_extension_less_file_is_the_unrestricted_one(self):
        """Stated as an equality with a file whose extension no restriction lists: an extension-less
        file is treated like any file the restriction does not name, not given its own rule."""
        assert sorted(_aggregate("README")) == sorted(_aggregate("part.stp")) == [_UNRESTRICTED_FIELD]

    @pytest.mark.parametrize("stored", [".glb,", ",.glb", " , ", ".glb, ,.usd"])
    def test_an_empty_stored_entry_does_not_opt_extension_less_files_in(self, stored):
        """The request models refuse an empty entry; a row already stored with one still cannot make
        a restricted schema apply to a file with no extension, because there is nothing to equal."""
        assert _RESTRICTED_FIELD not in _aggregate("README", restriction=stored)

    def test_a_file_metadata_lookup_without_a_file_path_is_not_filtered(self):
        """No path names no file, so every schema applies; the metadata service always passes the
        file's path, and this is the aggregate a caller gets when it asks for all of them."""
        aggregated = _aggregate(None)

        assert _RESTRICTED_FIELD in aggregated, sorted(aggregated)


@pytest.mark.unit
class TestEdgeCaseFileNames:
    """What the extension helper returns for the edge cases, and which schemas each one meets.

    The helper decides "extension-less": `None` for a name with no dot (or a folder key), otherwise
    the text after the LAST dot of the name, dotted and lower-cased.
    """

    @pytest.mark.parametrize("file_path,expected", [
        ("README", None),
        ("/a.b/README", None),             # the folder's dot is not the file's
        ("folder/", None),                 # a folder key; file metadata refuses folder paths anyway
        (".gitignore", ".gitignore"),      # a dotfile's name after its dot is its extension
        ("name.", "."),                    # a trailing dot yields a bare dot, which no entry may be
        ("archive.tar.gz", ".gz"),
        ("MODEL.GLB", ".glb"),
    ])
    def test_the_extension_helper(self, file_path, expected):
        assert extract_file_extension(file_path) == expected

    def test_a_dotfile_meets_a_schema_restricted_to_its_own_name(self):
        assert _RESTRICTED_FIELD in _aggregate(".gitignore", restriction=".gitignore")
        assert _RESTRICTED_FIELD not in _aggregate(".gitignore", restriction=".glb")

    def test_a_trailing_dot_name_meets_no_restricted_schema(self):
        """`name.` reads as the bare-dot extension `.`, which the request models refuse as an entry,
        so it meets the unrestricted schemas only, the same as an extension-less name."""
        assert _RESTRICTED_FIELD not in _aggregate("name.", restriction=".glb")

    def test_a_double_extension_is_matched_by_its_last_part(self):
        assert _RESTRICTED_FIELD in _aggregate("archive.tar.gz", restriction=".gz")
        assert _RESTRICTED_FIELD not in _aggregate("archive.tar.gz", restriction=".tar")

    def test_an_upper_case_extension_meets_a_lower_case_restriction(self):
        assert _RESTRICTED_FIELD in _aggregate("MODEL.GLB", restriction=".glb")
        assert _RESTRICTED_FIELD in _aggregate("model.glb", restriction=".GLB")


@pytest.mark.unit
class TestTheCacheKeySeparatesTheAggregates:
    """The 60-second aggregate cache is keyed per database set, entity type and extension key.

    An extension-less file's aggregate leaves the restricted schemas out, so it must not share a key
    with the unfiltered aggregate or with any extension's.
    """

    @staticmethod
    def _cached_keys(file_path):
        """The cache keys one lookup leaves behind (`_aggregate` clears the cache before it reads)."""
        _aggregate(file_path)
        return set(msv._schema_cache)

    def test_the_keys_for_readme_and_a_glb_file(self):
        assert self._cached_keys("README") == {"db1:fileMetadata:no_ext"}
        assert self._cached_keys("/a.b/README") == {"db1:fileMetadata:no_ext"}
        assert self._cached_keys("x.glb") == {"db1:fileMetadata:.glb"}

    def test_the_key_for_an_unfiltered_lookup(self):
        assert self._cached_keys(None) == {"db1:fileMetadata:all_files"}

    def test_an_unfiltered_entry_is_not_served_to_an_extension_less_file(self):
        """A lookup with no file path caches the aggregate WITH the restricted schema; README asked
        next, within the TTL, must not read that entry."""
        # One page, served again for every first read (pages are keyed on the cursor).
        pager = Pager(
            {"Items": [_schema_item("schema-restricted", _RESTRICTED_FIELD, restriction=".glb"),
                       _schema_item("schema-any", _UNRESTRICTED_FIELD)]},
            name="metadata schema query",
        )
        client = MagicMock()
        client.query.side_effect = pager

        def lookup(path):
            return get_aggregated_schemas(
                database_ids=["db1"], entity_type=_ENTITY, file_path=path,
                dynamodb_client=client, schema_table_name=_TABLE)

        assert _RESTRICTED_FIELD in lookup(None)
        reads_before = len(pager.calls)
        assert _RESTRICTED_FIELD not in lookup("README")
        assert len(pager.calls) > reads_before, "README was answered from the unfiltered entry"

    def test_a_non_file_entity_type_never_filters_by_extension(self):
        """`assetMetadata` passes no file path, so a stored restriction is irrelevant to it."""
        pager = Pager(
            {"Items": [_schema_item("schema-restricted", _RESTRICTED_FIELD, restriction=".glb")]},
            name="metadata schema query",
        )
        client = MagicMock()
        client.query.side_effect = pager

        aggregated = get_aggregated_schemas(
            database_ids=["db1"],
            entity_type="assetMetadata",
            file_path=None,
            dynamodb_client=client,
            schema_table_name=_TABLE,
        )

        assert pager.calls, "nothing read the schema stub"
        assert _RESTRICTED_FIELD in aggregated, sorted(aggregated)
