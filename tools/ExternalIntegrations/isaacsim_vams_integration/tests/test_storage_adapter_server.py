"""The storage adapter's address handling and error mapping (no network; gRPC parts skip without grpcio)."""
import pytest

from vams.connector.isaacsim.storage_adapter import server


def test_split_address_returns_the_path_under_the_root():
    assert server.split_address("vams://vams") == []
    assert server.split_address("vams://vams/db1/scene%20(x1)/textures/a.png") == ["db1", "scene (x1)", "textures",
                                                                                  "a.png"]


@pytest.mark.parametrize("address", [
    "vams://vams/db1/../db2/x/y",
    "vams://vams/db1/./x",
    "vams://vams/db1/%2E%2E/x",
    "omniverse://server/db1/x",
    "file:///etc/passwd",
])
def test_split_address_refuses_dot_segments_and_other_schemes(address):
    with pytest.raises(server.Missing):
        server.split_address(address)


def test_join_address_builds_under_the_root():
    assert server.join_address("db1", "scene (x1)", "a.usda") == "vams://vams/db1/scene (x1)/a.usda"


@pytest.mark.parametrize("metadata,expected", [
    ([("authorization", "Bearer vams_abc")], "vams_abc"),
    ([("Authorization", "bearer  vams_abc ")], "vams_abc"),
    ([("authorization", "eyJ.a.b")], "eyJ.a.b"),
    ([("x-other", "1")], None),
    ([("authorization", "")], None),
    (None, None),
])
def test_caller_token_reads_only_the_authorization_metadata(metadata, expected):
    assert server.caller_token(metadata) == expected


def test_tree_refuses_a_call_without_a_token():
    tree = server.VamsTree("https://api.example.com/api")
    with pytest.raises(server.Denied):
        tree.client([("x-other", "1")])


def test_files_under_builds_folders_from_file_paths():
    files = {"scene.usda": {}, "textures/a.png": {}, "textures/deep/b.png": {}, "sub.usda": {}}
    assert server.files_under(files, []) == (["textures"], [("scene.usda", {}), ("sub.usda", {})])
    assert server.files_under(files, ["textures"]) == (["deep"], [("a.png", {})])
    assert server.files_under(files, ["nothing"]) == ([], [])


def test_files_under_does_not_match_a_sibling_prefix():
    files = {"tex/a.png": {}, "textures/b.png": {}}
    assert server.files_under(files, ["tex"]) == ([], [("a.png", {})])


def test_discovery_document_lists_only_loopback_grpc():
    doc = server.discovery_document(4321, ["nvidia.omniverse.storage.fileobject.v1alpha.FileObjectService"])
    assert doc["schema-version"] == 2
    assert doc["services"][0]["grpc"]["url"] == "http://127.0.0.1:4321"


class AuthenticationError(Exception):
    pass


class AssetNotFoundError(Exception):
    pass


class PermissionDeniedError(Exception):
    pass


@pytest.mark.parametrize("error,status", [
    (server.Denied(), "UNAUTHENTICATED"),
    (AuthenticationError("Authentication failed: 401 Client Error"), "UNAUTHENTICATED"),
    (AuthenticationError("Authentication failed: 403 Forbidden"), "NOT_FOUND"),
    (PermissionDeniedError("denied"), "NOT_FOUND"),
    (AssetNotFoundError("Asset 'x' not found"), "NOT_FOUND"),
    (server.Missing(), "NOT_FOUND"),
    (RuntimeError("boom"), "UNAVAILABLE"),
])
def test_errors_map_to_generic_statuses(error, status):
    pytest.importorskip("grpc")
    assert server.status_for(error).name == status
