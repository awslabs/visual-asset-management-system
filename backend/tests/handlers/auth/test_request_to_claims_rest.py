import pytest
from backend.backend.handlers.auth import request_to_claims


@pytest.mark.unit
class TestRequestToClaimsRest:
    def test_flat_rest_authorizer_context(self):
        # REST REQUEST authorizer puts context as a flat string map directly
        # under requestContext.authorizer (no 'lambda'/'jwt' sub-key).
        evt = {"requestContext": {"authorizer": {
            "sub": "user-1",
            "vams:tokens": '["user-1"]',
            "vams:roles": '["admin"]',
        }}}
        out = request_to_claims(evt)
        assert out["tokens"] == ["user-1"]
        assert out["roles"] == ["admin"]

    def test_http_lambda_context_still_works(self):
        evt = {"requestContext": {"authorizer": {"lambda": {
            "sub": "user-2", "vams:tokens": '["user-2"]'}}}}
        out = request_to_claims(evt)
        assert out["tokens"] == ["user-2"]

    def test_request_to_claims_normalizes_rest_event_in_place(self):
        evt = {
            "httpMethod": "GET",
            "path": "/database/db1/assets",
            "requestContext": {"identity": {"sourceIp": "203.0.113.7"},
                               "authorizer": {"sub": "u1", "vams:tokens": '["u1"]'}},
            "headers": {},
        }
        request_to_claims(evt)
        # The shim must have populated the canonical v2 shape handlers rely on.
        assert evt["requestContext"]["http"]["path"] == "/database/db1/assets"
        assert evt["requestContext"]["http"]["method"] == "GET"
