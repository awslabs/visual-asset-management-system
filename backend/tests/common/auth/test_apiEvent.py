import pytest
from backend.backend.common.auth.apiEvent import normalize_event


@pytest.mark.unit
class TestNormalizeEvent:
    def test_rest_event_gets_http_block(self):
        evt = {
            "httpMethod": "GET",
            "path": "/database/db1/assets",
            "requestContext": {"identity": {"sourceIp": "203.0.113.7"},
                               "authorizer": {"sub": "user-1"}},
            "headers": {},
        }
        out = normalize_event(evt)
        assert out["requestContext"]["http"]["path"] == "/database/db1/assets"
        assert out["requestContext"]["http"]["method"] == "GET"
        assert out["requestContext"]["http"]["sourceIp"] == "203.0.113.7"

    def test_v2_event_unchanged(self):
        evt = {"requestContext": {"http": {"path": "/x", "method": "POST",
                                           "sourceIp": "1.2.3.4"}}}
        out = normalize_event(evt)
        assert out["requestContext"]["http"]["method"] == "POST"

    def test_cross_call_event_unchanged(self):
        evt = {"lambdaCrossCall": {"userName": "SYSTEM_USER"}}
        out = normalize_event(evt)
        assert "http" not in out.get("requestContext", {})

    def test_idempotent(self):
        evt = {"httpMethod": "GET", "path": "/x",
               "requestContext": {"identity": {"sourceIp": "1.2.3.4"}}}
        normalize_event(evt)
        normalize_event(evt)
        assert evt["requestContext"]["http"]["path"] == "/x"

    def test_null_path_and_query_params_coerced_to_empty_dict(self):
        # REST API (v1) sends these as explicit null when absent; handlers read them
        # without an `or {}` guard, so normalize must coerce null -> {}.
        evt = {
            "httpMethod": "GET",
            "path": "/subscriptions",
            "pathParameters": None,
            "queryStringParameters": None,
            "requestContext": {"identity": {"sourceIp": "203.0.113.7"}},
        }
        out = normalize_event(evt)
        assert out["pathParameters"] == {}
        assert out["queryStringParameters"] == {}

    def test_present_params_preserved(self):
        # Non-null values must pass through untouched.
        evt = {
            "httpMethod": "GET",
            "path": "/metadataschema",
            "pathParameters": {"databaseId": "GLOBAL"},
            "queryStringParameters": {"maxItems": "100"},
            "requestContext": {"identity": {"sourceIp": "203.0.113.7"}},
        }
        out = normalize_event(evt)
        assert out["pathParameters"] == {"databaseId": "GLOBAL"}
        assert out["queryStringParameters"] == {"maxItems": "100"}

    def test_v2_event_with_null_params_still_coerced(self):
        # Even an already-v2-shaped event gets null params coerced before the early return.
        evt = {
            "requestContext": {"http": {"path": "/x", "method": "GET", "sourceIp": "1.2.3.4"}},
            "queryStringParameters": None,
        }
        out = normalize_event(evt)
        assert out["queryStringParameters"] == {}


@pytest.mark.unit
class TestPathParameterDecoding:
    """API Gateway REST (v1) delivers path parameters percent-encoded; HTTP API (v2)
    delivered them decoded. Handlers use these values directly as S3 object keys, and an
    S3 key holds raw characters — so an undecoded value raises NoSuchKey on a file whose
    name contains a space."""

    def _rest_event(self, proxy):
        return {
            "httpMethod": "GET",
            "path": "/database/db1/assets/a1/auxiliaryPreviewAssets/stream/" + proxy,
            "pathParameters": {"databaseId": "db1", "assetId": "a1", "proxy": proxy},
            "requestContext": {"identity": {"sourceIp": "203.0.113.7"}},
            "headers": {},
        }

    def test_spaces_in_proxy_path_are_decoded(self):
        encoded = "a1/ATTC%20Cleaned%20-%20no%20roof.e57/preview/PotreeViewer/metadata.json"
        out = normalize_event(self._rest_event(encoded))
        assert out["pathParameters"]["proxy"] == (
            "a1/ATTC Cleaned - no roof.e57/preview/PotreeViewer/metadata.json"
        )

    def test_other_escapes_are_decoded(self):
        # Parentheses, ampersands, hashes and commas all appear in real file names.
        encoded = "a1/scan%20%28v2%29%20%26%20final%231%2Cb.e57/preview/PotreeViewer/metadata.json"
        out = normalize_event(self._rest_event(encoded))
        assert out["pathParameters"]["proxy"] == (
            "a1/scan (v2) & final#1,b.e57/preview/PotreeViewer/metadata.json"
        )

    def test_plus_is_preserved_as_literal(self):
        # In a URL *path* "+" is a literal plus, not a space (only query strings use "+"
        # for space), so unquote_plus would corrupt this file name.
        out = normalize_event(self._rest_event("a1/scan+v2.e57/preview/PotreeViewer/metadata.json"))
        assert out["pathParameters"]["proxy"] == "a1/scan+v2.e57/preview/PotreeViewer/metadata.json"

    def test_unencoded_path_unchanged(self):
        plain = "a1/model.e57/preview/PotreeViewer/metadata.json"
        out = normalize_event(self._rest_event(plain))
        assert out["pathParameters"]["proxy"] == plain

    def test_decoding_is_not_repeated_on_second_normalize(self):
        # A file whose decoded name legitimately contains a percent escape arrives
        # double-encoded; decoding twice would turn "a%20b" into "a b".
        out = normalize_event(self._rest_event("a1/a%2520b.e57/preview/PotreeViewer/metadata.json"))
        assert out["pathParameters"]["proxy"] == "a1/a%20b.e57/preview/PotreeViewer/metadata.json"
        out2 = normalize_event(out)
        assert out2["pathParameters"]["proxy"] == "a1/a%20b.e57/preview/PotreeViewer/metadata.json"

    def test_scalar_params_decoded_too(self):
        evt = {
            "httpMethod": "GET",
            "path": "/database/db1/assets/a1",
            "pathParameters": {"databaseId": "my%20db", "assetId": "a1"},
            "requestContext": {"identity": {"sourceIp": "1.2.3.4"}},
            "headers": {},
        }
        out = normalize_event(evt)
        assert out["pathParameters"]["databaseId"] == "my db"

    def test_null_path_params_still_safe(self):
        evt = {
            "httpMethod": "GET",
            "path": "/x",
            "pathParameters": None,
            "requestContext": {"identity": {"sourceIp": "1.2.3.4"}},
            "headers": {},
        }
        out = normalize_event(evt)
        assert out["pathParameters"] == {}

    def test_cross_call_event_not_decoded(self):
        evt = {"lambdaCrossCall": {"userName": "SYSTEM_USER"},
               "pathParameters": {"proxy": "a%20b"}}
        out = normalize_event(evt)
        assert out["pathParameters"]["proxy"] == "a%20b"
