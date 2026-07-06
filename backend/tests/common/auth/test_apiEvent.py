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
