# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""A single-file asset's derived data and listings stay with the keys they belong to.

The object of a single-file location ``p1/model.glb`` keeps its auxiliary (viewer and preview) data at
``{databaseId}/p1/model.glb/preview/...``; the entries of its companion folder ``p1/model.glb/`` keep theirs
at ``{databaseId}/p1/model.glb/{entry}/preview/...``, inside the object's base. Deleting, reverting or copying
the object therefore touches ``.../preview/`` only, and the auxiliary stream route reads the object's data
when the viewer asks for ``/model.glb/preview/...``. Listing the asset and sampling it for its asset type
read the object, its own previews and ``p1/model.glb/`` by their own prefixes, so keys that only share the
location's characters (``p1/model.glb-old/...``, ``p1/model.glb.bak/...``) are never read and cannot fill a
page or the sample.
"""

import os

import boto3
import pytest
from moto import mock_aws

os.environ.setdefault("LARGE_OBJECT_COPY_JOBS_STORAGE_TABLE_NAME", "t-copy-jobs")
os.environ.setdefault("ASSET_UPLOAD_TABLE_NAME", "t-uploads")
os.environ.setdefault("WORKFLOW_EXECUTION_STORAGE_TABLE_V2_NAME", "t-executions")
os.environ.setdefault("SEND_EMAIL_FUNCTION_NAME", "sendEmailFunction")
os.environ.setdefault("PROCESS_WORKFLOW_OUTPUT_LAMBDA_FUNCTION_NAME", "processOutput")

from tests.handlers.assets.test_assetFiles_single_file_location import (  # noqa: E402,F401
    BUCKET, CLAIMS, COMPANION, FILE_LOCATION, FOLDER_LOCATION, OWN_KEY, OWN_PREVIEW, SHADOWED, af,
)
from tests.handlers.assets.test_streamAuxiliaryPreviewAsset_range_header_casing import (  # noqa: E402
    _load as _load_aux_stream,
)


# The viewer data of a companion entry {key}/preview/x.glb, inside the object's preview/ folder
OBJECT_PREVIEW_COMPANION_DATA = "db1/p1/model.glb/preview/x.glb/preview/PotreeViewer/r.bin"


@pytest.mark.unit
class TestAuxiliaryDataOfTheObject:
    @pytest.mark.parametrize("location,key,expected", [
        (FILE_LOCATION, OWN_KEY, "db1/p1/model.glb/preview/"),
        (FILE_LOCATION, "/" + OWN_KEY, "db1/p1/model.glb/preview/"),
        (FILE_LOCATION, COMPANION, "db1/p1/model.glb/sub/x.obj/"),
        (FILE_LOCATION, SHADOWED, "db1/p1/model.glb/model.glb/"),
        (FOLDER_LOCATION, "proj/a/x.txt", "db1/proj/a/x.txt/"),
    ])
    def test_each_file_has_its_own_auxiliary_prefix(self, af, location, key, expected):
        assert af.aux_file_base("db1", location, key) == expected

    def test_deleting_the_object_deletes_its_preview_data_only(self, af, monkeypatch):
        calls = []
        monkeypatch.setattr(af, "delete_assetAuxiliary_files",
                            lambda prefix, keep=None: calls.append((prefix, keep)))
        af.delete_file("db1", "a1", "/sub/x.obj", False, True, CLAIMS)
        af.delete_file("db1", "a1", "/model.glb", False, True, CLAIMS)
        # A companion entry's own data goes with it; the object's stops at preview/ and leaves the data of
        # companion entries in {key}/preview/
        assert [prefix for prefix, _ in calls] == ["db1/p1/model.glb/sub/x.obj/", "db1/p1/model.glb/preview/"]
        assert calls[0][1] is None
        assert calls[1][1]("db1/p1/model.glb/preview/r.bin") and not calls[1][1](OBJECT_PREVIEW_COMPANION_DATA)

    def test_reverting_the_object_deletes_its_preview_data_only(self, af, monkeypatch):
        first = af.test_client.list_object_versions(Bucket=BUCKET, Prefix=OWN_KEY)["Versions"]
        first_id = [v for v in first if v["Key"] == OWN_KEY][0]["VersionId"]
        af.test_client.put_object(Bucket=BUCKET, Key=OWN_KEY, Body=b"second")
        calls = []
        monkeypatch.setattr(af, "delete_assetAuxiliary_files",
                            lambda prefix, keep=None: calls.append((prefix, keep)))
        af.revert_file_version("db1", "a1", "/model.glb", first_id, CLAIMS)
        assert [prefix for prefix, _ in calls] == ["db1/p1/model.glb/preview/"]
        assert calls[0][1]("db1/p1/model.glb/preview/r.bin") and not calls[0][1](OBJECT_PREVIEW_COMPANION_DATA)

    def test_the_object_and_a_companion_folder_named_preview_keep_their_viewer_data_apart(self, af, monkeypatch):
        """The object's data and that of companion entries in {key}/preview/ share db/{key}/preview/."""
        calls = []
        monkeypatch.setattr(af, "delete_assetAuxiliary_files",
                            lambda prefix, keep=None: calls.append((prefix, keep)))
        af.test_client.put_object(Bucket=BUCKET, Key="p1/model.glb/preview/x.glb", Body=b"x")
        af.delete_file("db1", "a1", "/model.glb", False, True, CLAIMS)
        af.delete_file("db1", "a1", "/preview/", True, True, CLAIMS)
        (object_prefix, object_keep), (folder_prefix, folder_keep) = calls
        object_data = "db1/p1/model.glb/preview/PotreeViewer/r.bin"
        companion_data = "db1/p1/model.glb/preview/x.glb/preview/PotreeViewer/r.bin"
        assert object_prefix == folder_prefix == "db1/p1/model.glb/preview/"
        assert object_keep(object_data) and not object_keep(companion_data)
        assert folder_keep(companion_data) and not folder_keep(object_data)
        # A copy of the companion entry carries its own data only
        keep = af.aux_owner_filter("db1", FILE_LOCATION, "p1/model.glb/preview/x.glb",
                                   "db1/p1/model.glb/preview/x.glb/")
        assert keep("db1/p1/model.glb/preview/x.glb/preview/a.bin") and not keep("db1/p1/model.glb/preview/x.glb/a.bin")
        # A folder asset needs no filter
        assert af.aux_owner_filter("db1", FOLDER_LOCATION, "proj/a/x.txt", "db1/proj/a/x.txt/") is None

    @pytest.mark.parametrize("source,dest,expected", [
        # The object copied out: its preview data lands in the destination file's preview folder
        ((FILE_LOCATION, OWN_KEY), (FOLDER_LOCATION, "proj/a/model.glb"),
         ("db1/p1/model.glb/preview/", "db2/proj/a/model.glb/preview/")),
        # A file copied onto the object's path: into the object's preview folder, not over its companions
        ((FOLDER_LOCATION, "proj/a/x.txt"), (FILE_LOCATION, OWN_KEY),
         ("db1/proj/a/x.txt/preview/", "db2/p1/model.glb/preview/")),
        # A companion entry keeps its whole base
        ((FILE_LOCATION, COMPANION), (FILE_LOCATION, "p1/model.glb/sub/y.obj"),
         ("db1/p1/model.glb/sub/x.obj/", "db2/p1/model.glb/sub/y.obj/")),
        ((FOLDER_LOCATION, "proj/a/x.txt"), (FOLDER_LOCATION, "proj/a/y.txt"),
         ("db1/proj/a/x.txt/", "db2/proj/a/y.txt/")),
    ])
    def test_a_copy_or_move_keeps_the_object_data_in_its_preview_folder(self, af, source, dest, expected):
        assert af.aux_file_bases("db1", source[0], source[1], "db2", dest[0], dest[1]) == expected


@pytest.mark.unit
class TestAuxiliaryStreamPaths:
    @pytest.mark.parametrize("path,expected", [
        ("/model.glb/preview/cloud/r.bin", "db1/p1/model.glb/preview/cloud/r.bin"),
        ("model.glb/preview/cloud/r.bin", "db1/p1/model.glb/preview/cloud/r.bin"),
        ("/sub/x.obj/preview/a.bin", "db1/p1/model.glb/sub/x.obj/preview/a.bin"),
        ("/model.glb/model.glb/preview/a.bin", "db1/p1/model.glb/model.glb/preview/a.bin"),
        ("/preview/a.glb/preview/a.bin", "db1/p1/model.glb/preview/a.glb/preview/a.bin"),
        ("db1/p1/model.glb/preview/a.bin", "db1/p1/model.glb/preview/a.bin"),
    ])
    def test_a_single_file_asset_reads_the_data_of_the_file_named(self, path, expected):
        assert _load_aux_stream().resolve_aux_preview_key("db1", FILE_LOCATION, path) == expected

    def test_a_path_without_preview_data_is_refused(self):
        module = _load_aux_stream()
        with pytest.raises(module.VAMSGeneralErrorResponse):
            module.resolve_aux_preview_key("db1", FILE_LOCATION, "/model.glb")

    @pytest.mark.parametrize("path,expected", [
        ("/x.txt/preview/a.bin", "db1/proj/a/x.txt/preview/a.bin"),
        ("db1/proj/a/x.txt/preview/a.bin", "db1/proj/a/x.txt/preview/a.bin"),
    ])
    def test_control_a_folder_asset_resolves_as_before(self, path, expected):
        assert _load_aux_stream().resolve_aux_preview_key("db1", FOLDER_LOCATION, path) == expected


def _pages(af, page_size, include_archived=False, extra=None):
    pages, token = [], None
    while True:
        query = {"pageSize": page_size, **(extra or {})}
        if token:
            query["startingToken"] = token
        result = af.list_s3_objects_with_archive_status(
            BUCKET, FILE_LOCATION, query, include_archived=include_archived, basic_mode=True)
        pages.append([i["key"] for i in result["items"]])
        token = result.get("NextToken")
        if not token:
            return pages


@pytest.mark.unit
class TestListingReadsOnlyTheLocationsKeys:
    def test_neighbour_keys_are_never_read_and_fill_no_page(self, af, monkeypatch):
        for i in range(12):
            af.test_client.put_object(Bucket=BUCKET, Key=f"p1/model.glb-old/n{i:02}", Body=b"x")
            af.test_client.put_object(Bucket=BUCKET, Key=f"p1/model.glb.bak/n{i:02}", Body=b"x")
        listed_prefixes = []
        real = af.test_client.list_objects_v2

        def spy(**kwargs):
            listed_prefixes.append((kwargs["Prefix"], kwargs.get("MaxKeys")))
            return real(**kwargs)

        monkeypatch.setattr(af.s3_client, "list_objects_v2", spy)
        pages = _pages(af, 2)
        assert sorted(k for page in pages for k in page) == sorted([OWN_KEY, OWN_PREVIEW, COMPANION, SHADOWED])
        assert all(page for page in pages[:-1])
        assert len(pages) <= 3
        for prefix, max_keys in listed_prefixes:
            assert prefix in (FILE_LOCATION, FILE_LOCATION + ".previewFile.", FILE_LOCATION + "/"), prefix
            if prefix == FILE_LOCATION:
                assert max_keys == 1


@pytest.fixture
def engine(monkeypatch):
    from backend.backend.handlers.assets import largeObjectCopy as module

    with mock_aws():
        client = boto3.client("s3", region_name="us-east-1")
        client.create_bucket(Bucket=BUCKET)
        client.put_bucket_versioning(Bucket=BUCKET, VersioningConfiguration={"Status": "Enabled"})
        client.put_object(Bucket=BUCKET, Key=OWN_KEY, Body=b"x")
        for i in range(8):
            client.put_object(Bucket=BUCKET, Key=f"p1/model.glb-old/n{i:02}", Body=b"x")
        monkeypatch.setattr(module, "s3", client)
        monkeypatch.setattr(module, "ASSET_TYPE_DETECTION_SAMPLE_SIZE", 4)
        module.test_client = client
        yield module


@pytest.mark.unit
class TestAssetTypeSampleReadsOnlyTheLocationsKeys:
    def test_an_archived_object_and_one_companion_file_count_that_file_once(self, engine):
        """With the object archived and no neighbour key, the one-key read of the location returns the
        companion file, which the companion folder listing returns again; counted twice, the asset read as
        a folder (D4)."""
        client = engine.test_client
        for version in client.list_object_versions(Bucket=BUCKET, Prefix="p1/model.glb-old/").get("Versions", []):
            client.delete_object(Bucket=BUCKET, Key=version["Key"], VersionId=version["VersionId"])
        client.delete_object(Bucket=BUCKET, Key=OWN_KEY)
        client.put_object(Bucket=BUCKET, Key="p1/model.glb/sub/part.obj", Body=b"x")
        assert engine._determine_asset_type(BUCKET, FILE_LOCATION) == ".obj"

    def test_neighbours_do_not_fill_the_sample(self, engine):
        engine.test_client.put_object(Bucket=BUCKET, Key="p1/model.glb/sub/part.obj", Body=b"x")
        assert engine._determine_asset_type(BUCKET, FILE_LOCATION) == "folder"
