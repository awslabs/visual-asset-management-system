#  SPDX-License-Identifier: Apache-2.0

"""3dRecon/splatToolbox container: the input is reconstructed from the version the run was launched on (#994).

constructPipeline puts the version on the stage's ``inputFile.versionId``. The entry point checks the
version still exists (a deleted one fails the run naming the file and version, before ``main.py``
starts), then hands the object and version to ``main.py``'s process, where the launcher's routing
client adds ``VersionId`` to every read of that object -- ``main.py``'s ``download_file`` of S3_INPUT
included. Any other object, and a run without a version, is read exactly as before."""

import json
import os
import sys
from unittest.mock import MagicMock

import pytest
from botocore.exceptions import ClientError

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_splat_s3_region_routing import boto3, entry, restore_boto3_client, s3, s3mod  # noqa: E402,F401

VERSION = "3HL4kqtJlcpXroDTDmJ.rmSpXd3dIbrHY"


@pytest.fixture
def versions():
    saved = dict(s3._input_versions)
    s3._input_versions.clear()
    yield s3
    s3._input_versions.clear()
    s3._input_versions.update(saved)


@pytest.fixture
def routed(s3mod, versions, monkeypatch):
    default_client = MagicMock(name="default")
    monkeypatch.setattr(s3mod, "client", default_client)
    monkeypatch.setattr(s3mod, "client_for_bucket", lambda bucket: default_client)
    return s3mod.region_routing_client(), default_client


@pytest.mark.unit
class TestTheRoutingClientReadsTheInputVersion:
    def test_download_file_of_the_input_reads_its_version(self, routed, versions):
        client, target = routed
        versions.register_input_version("in-bkt", "xid/capture.zip", VERSION)
        client.download_file("in-bkt", "xid/capture.zip", "/tmp/input/train/capture.zip")
        target.download_file.assert_called_once_with(
            "in-bkt", "xid/capture.zip", "/tmp/input/train/capture.zip", ExtraArgs={"VersionId": VERSION})

    def test_extra_args_are_merged_and_an_explicit_version_wins(self, routed, versions):
        client, target = routed
        versions.register_input_version("in-bkt", "xid/capture.zip", VERSION)
        client.download_file("in-bkt", "xid/capture.zip", "/tmp/f", ExtraArgs={"RequestPayer": "requester"})
        assert target.download_file.call_args.kwargs["ExtraArgs"] == {"RequestPayer": "requester", "VersionId": VERSION}
        client.download_file("in-bkt", "xid/capture.zip", "/tmp/f", ExtraArgs={"VersionId": "other"})
        assert target.download_file.call_args.kwargs["ExtraArgs"] == {"VersionId": "other"}

    def test_api_reads_of_the_input_read_its_version(self, routed, versions):
        client, target = routed
        versions.register_input_version("in-bkt", "xid/capture.zip", VERSION)
        client.head_object(Bucket="in-bkt", Key="xid/capture.zip")
        target.head_object.assert_called_once_with(Bucket="in-bkt", Key="xid/capture.zip", VersionId=VERSION)
        client.get_object(Bucket="in-bkt", Key="xid/capture.zip")
        target.get_object.assert_called_once_with(Bucket="in-bkt", Key="xid/capture.zip", VersionId=VERSION)

    def test_other_objects_and_writes_are_unchanged(self, routed, versions):
        client, target = routed
        versions.register_input_version("in-bkt", "xid/capture.zip", VERSION)
        client.download_file("in-bkt", "xid/models.tar.gz", "/tmp/m")
        target.download_file.assert_called_once_with("in-bkt", "xid/models.tar.gz", "/tmp/m")
        client.upload_file("/tmp/out.ply", "out-bkt", "files/out.ply")
        target.upload_file.assert_called_once_with("/tmp/out.ply", "out-bkt", "files/out.ply")
        client.head_object(Bucket="in-bkt", Key="xid/other.zip")
        target.head_object.assert_called_once_with(Bucket="in-bkt", Key="xid/other.zip")

    def test_no_registered_version_reads_the_current_object(self, routed, versions):
        client, target = routed
        versions.register_input_version("in-bkt", "xid/capture.zip", "")
        client.download_file("in-bkt", "xid/capture.zip", "/tmp/f")
        target.download_file.assert_called_once_with("in-bkt", "xid/capture.zip", "/tmp/f")


@pytest.mark.unit
class TestTheLauncherInstallsTheVersion:
    def test_a_version_alone_installs_the_routing_client(self, entry, s3mod, versions, restore_boto3_client):
        original = boto3.client
        entry.install_region_routing_s3_clients(
            "", json.dumps({"bucket": "in-bkt", "key": "xid/capture.zip", "versionId": VERSION}))
        assert boto3.client is not original
        assert isinstance(boto3.client("s3"), s3mod.RegionRoutingClient)
        assert versions.input_version_for("in-bkt", "xid/capture.zip") == VERSION

    def test_no_version_and_no_map_leave_boto3_untouched(self, entry, s3mod, versions, restore_boto3_client):
        original = boto3.client
        entry.install_region_routing_s3_clients(
            "", json.dumps({"bucket": "in-bkt", "key": "xid/capture.zip", "versionId": ""}))
        assert boto3.client is original
        assert versions._input_versions == {}

    def test_the_launcher_hands_the_version_to_main_py(self, entry):
        assert "_entry.INPUT_VERSION_ENV" in entry._GUARDED_MAIN_LAUNCHER

    def test_the_entry_exports_the_version_before_launching(self):
        source_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "__main__.py")
        with open(source_path, encoding="utf-8") as handle:
            main_body = handle.read().split("def main():", 1)[1]
        export = main_body.index("os.environ[INPUT_VERSION_ENV]")
        check = main_body.index("vams_s3.input_version_missing_cause(")
        assert export < check < main_body.index("subprocess.run(")


@pytest.mark.unit
class TestADeletedVersionIsNamed:
    @pytest.mark.parametrize("code", ["404", "405", "NoSuchVersion", "NoSuchKey", "MethodNotAllowed"])
    def test_a_missing_version_names_the_file_and_version(self, s3mod, monkeypatch, code):
        client = MagicMock()
        client.head_object.side_effect = ClientError({"Error": {"Code": code}}, "HeadObject")
        monkeypatch.setattr(s3mod, "client_for_bucket", lambda bucket: client)
        cause = s3mod.input_version_missing_cause("in-bkt", "xid/capture.zip", VERSION)
        assert "xid/capture.zip" in cause and VERSION in cause
        client.head_object.assert_called_once_with(Bucket="in-bkt", Key="xid/capture.zip", VersionId=VERSION)

    def test_an_existing_version_or_another_error_is_not_a_cause(self, s3mod, monkeypatch):
        client = MagicMock()
        monkeypatch.setattr(s3mod, "client_for_bucket", lambda bucket: client)
        assert s3mod.input_version_missing_cause("in-bkt", "k", VERSION) == ""
        client.head_object.side_effect = ClientError({"Error": {"Code": "403"}}, "HeadObject")
        assert s3mod.input_version_missing_cause("in-bkt", "k", VERSION) == ""

    def test_no_version_makes_no_request(self, s3mod, monkeypatch):
        client = MagicMock()
        monkeypatch.setattr(s3mod, "client_for_bucket", lambda bucket: client)
        assert s3mod.input_version_missing_cause("in-bkt", "k", "") == ""
        client.head_object.assert_not_called()
