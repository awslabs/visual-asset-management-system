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


class APIError(Exception):
    pass


class RouteNotDeployedError(APIError):
    pass


@pytest.mark.parametrize("error,status", [
    (server.Denied(), "UNAUTHENTICATED"),
    (AuthenticationError("Authentication failed: 401 Client Error"), "UNAUTHENTICATED"),
    (AuthenticationError("Authentication failed: 403 Forbidden"), "NOT_FOUND"),
    (PermissionDeniedError("denied"), "NOT_FOUND"),
    (AssetNotFoundError("Asset 'x' not found"), "NOT_FOUND"),
    (server.Missing(), "NOT_FOUND"),
    (APIError("Resource not found (404): Asset not found"), "NOT_FOUND"),
    (APIError("Access denied (403): Not Authorized"), "NOT_FOUND"),
    (APIError("Server error (500): boom"), "UNAVAILABLE"),
    (RouteNotDeployedError("Resource not found (404): route"), "UNAVAILABLE"),
    (RuntimeError("boom"), "UNAVAILABLE"),
])
def test_errors_map_to_generic_statuses(error, status):
    pytest.importorskip("grpc")
    assert server.status_for(error).name == status


@pytest.mark.parametrize("value,expected", [
    ("2026-11-24T14:03:29.970036", "2026-11-24T14:03:29.970036+00:00"),
    ("2026-11-24T14:03:29Z", "2026-11-24T14:03:29+00:00"),
    ("November 24 2026 - 13:54:15", "2026-11-24T13:54:15+00:00"),
    ("not a date", None),
    ("", None),
    (None, None),
])
def test_parse_vams_date_reads_the_shapes_vams_writes(value, expected):
    parsed = server.parse_vams_date(value)
    assert (parsed.isoformat() if parsed else None) == expected


def test_the_discovery_document_lists_the_notification_consumer_on_its_own_port():
    doc = server.discovery_document(4321, ["s.Storage"], 4322, ["n.EventConsumerService"])
    storage, events = doc["services"]
    assert storage["grpc"]["url"] == "http://127.0.0.1:4321"
    assert events["grpc"]["url"] == "http://127.0.0.1:4322"
    assert events["grpc"]["endpoints"] == [{"service": "n.EventConsumerService"}]


def test_the_discovery_document_without_events_lists_only_storage():
    assert len(server.discovery_document(4321, ["s.Storage"])["services"]) == 1


class FakeApi:
    def __init__(self):
        self.calls = 0

    def list_databases(self, params=None):
        self.calls += 1
        if params.get("startingToken"):
            return {"Items": [{"databaseId": "b"}]}
        return {"Items": [{"databaseId": "c"}, {"databaseId": "a"}], "NextToken": "t"}


def test_databases_are_read_across_pages_sorted_and_cached_per_caller():
    tree = server.VamsTree("https://api.example.com/api")
    api = FakeApi()
    assert list(tree.databases(api, "who1")) == [["a", "b", "c"]]
    assert list(tree.databases(api, "who1")) == [["a", "b", "c"]]
    assert api.calls == 2
    list(tree.databases(api, "who2"))
    assert api.calls == 4


def test_the_cache_drops_expired_entries_and_stays_bounded(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(server.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(server, "MAX_CACHE_ENTRIES", 3)
    tree = server.VamsTree("https://api.example.com/api")
    tree._cached("old", lambda: 1)
    now[0] += server.CACHE_TTL_SECONDS
    for key in ("a", "b", "c", "d"):
        tree._cached(key, lambda: 2)
        now[0] += 1
    assert sorted(tree._cache) == ["b", "c", "d"]


def test_the_adapter_exits_when_its_parent_closes_the_pipe():
    import io
    import threading

    gone = threading.Event()
    thread = server.exit_when_parent_goes(io.BytesIO(b"anything"), gone.set)
    thread.join(5)
    assert gone.is_set()


def test_the_adapter_keeps_running_while_the_pipe_is_open():
    import os
    import threading

    read_end, write_end = os.pipe()
    gone = threading.Event()
    with os.fdopen(read_end, "rb") as stream:
        server.exit_when_parent_goes(stream, gone.set)
        assert not gone.wait(0.3)
        os.close(write_end)
        assert gone.wait(5)
