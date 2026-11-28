import pytest
from backend.backend.common.auth.apiEvent import normalize_event, strip_custom_domain_base_path


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


# REST API custom domain (app.api.apiGatewayRest.customDomain), base path "api" mapped to the
# "api" stage. Field layout copied from proxy events captured through the custom domain and
# through execute-api on a sandbox deployment; host, API id and account are placeholders.
CUSTOM_HOST = "api.vams.example.com"
EXECUTE_API_HOST = "abc123.execute-api.us-east-1.amazonaws.com"


def _proxy_event(path, domain_name, resource="/database"):
    """A REST (v1) Lambda proxy event as API Gateway delivers it, before normalize_event."""
    domain_prefix = domain_name.split(".")[0]
    return {
        "resource": resource,
        "path": path,
        "httpMethod": "GET",
        "headers": {"Host": domain_name},
        "queryStringParameters": None,
        "pathParameters": None,
        "requestContext": {
            "resourcePath": resource,
            "httpMethod": "GET",
            # requestContext.path is the URL path as sent, on both hosts ("/api/database").
            "path": "/api" + resource,
            "stage": "api",
            "domainPrefix": domain_prefix,
            "domainName": domain_name,
            "apiId": "abc123",
            "accountId": "123456789012",
            "identity": {"sourceIp": "203.0.113.7"},
            "authorizer": {"sub": "user-1"},
        },
    }


@pytest.fixture
def custom_domain_env(monkeypatch):
    """The environment the CDK sets on API Lambdas when the custom domain is enabled."""
    monkeypatch.setenv("API_CUSTOM_DOMAIN_NAME", CUSTOM_HOST)
    monkeypatch.setenv("API_CUSTOM_DOMAIN_BASE_PATH", "api")


@pytest.fixture
def no_custom_domain_env(monkeypatch):
    monkeypatch.delenv("API_CUSTOM_DOMAIN_NAME", raising=False)
    monkeypatch.delenv("API_CUSTOM_DOMAIN_BASE_PATH", raising=False)


@pytest.mark.unit
@pytest.mark.usefixtures("custom_domain_env")
class TestCustomDomainBasePath:
    """Through the custom domain the proxy event's ``path`` keeps the base path
    ("/api/database") where execute-api delivers "/database"; handlers dispatch on
    requestContext.http.path, so normalize_event makes it stage-relative on both hosts."""

    def test_custom_domain_path_is_stripped(self):
        out = normalize_event(_proxy_event("/api/database", CUSTOM_HOST))
        assert out["requestContext"]["http"]["path"] == "/database"
        assert out["requestContext"]["http"]["method"] == "GET"
        assert out["requestContext"]["http"]["sourceIp"] == "203.0.113.7"

    def test_top_level_path_is_left_as_delivered(self):
        out = normalize_event(_proxy_event("/api/database", CUSTOM_HOST))
        assert out["path"] == "/api/database"
        assert out["requestContext"]["path"] == "/api/database"

    def test_execute_api_path_is_untouched(self):
        out = normalize_event(_proxy_event("/database", EXECUTE_API_HOST))
        assert out["requestContext"]["http"]["path"] == "/database"

    def test_execute_api_path_starting_with_the_base_path_is_untouched(self):
        # Through execute-api "/api/version" is the route itself, not a base path.
        evt = _proxy_event("/api/version", EXECUTE_API_HOST, resource="/api/version")
        assert normalize_event(evt)["requestContext"]["http"]["path"] == "/api/version"

    def test_only_one_segment_is_stripped(self):
        # https://{domain}/api/api/version is the "/api/version" route of the stage.
        evt = _proxy_event("/api/api/version", CUSTOM_HOST, resource="/api/version")
        assert normalize_event(evt)["requestContext"]["http"]["path"] == "/api/version"

    def test_multi_segment_route_is_stripped(self):
        evt = _proxy_event(
            "/api/database/db1/assets/a1", CUSTOM_HOST,
            resource="/database/{databaseId}/assets/{assetId}",
        )
        assert normalize_event(evt)["requestContext"]["http"]["path"] == "/database/db1/assets/a1"

    def test_base_path_alone_becomes_the_root(self):
        evt = _proxy_event("/api", CUSTOM_HOST, resource="/")
        assert normalize_event(evt)["requestContext"]["http"]["path"] == "/"

    @pytest.mark.parametrize("path", ["/database", "/apix/database", "/API/database"])
    def test_custom_domain_path_without_the_base_path_is_untouched(self, path):
        # Only "/api" itself or "/api/..." is a base path; a partial segment or other case is not.
        assert normalize_event(_proxy_event(path, CUSTOM_HOST))["requestContext"]["http"]["path"] == path

    def test_other_domain_name_is_untouched(self):
        evt = _proxy_event("/api/database", "other.example.com")
        assert normalize_event(evt)["requestContext"]["http"]["path"] == "/api/database"

    def test_domain_name_compare_is_case_insensitive(self):
        evt = _proxy_event("/api/database", CUSTOM_HOST.upper())
        assert normalize_event(evt)["requestContext"]["http"]["path"] == "/database"

    def test_host_header_does_not_select_the_strip(self):
        # domainName comes from the mapping API Gateway routed through, not from the
        # caller's Host header, so an execute-api request naming the domain is untouched.
        evt = _proxy_event("/api/version", EXECUTE_API_HOST, resource="/api/version")
        evt["headers"]["Host"] = CUSTOM_HOST
        assert normalize_event(evt)["requestContext"]["http"]["path"] == "/api/version"

    def test_missing_domain_name_is_untouched(self):
        evt = _proxy_event("/api/database", CUSTOM_HOST)
        del evt["requestContext"]["domainName"]
        assert normalize_event(evt)["requestContext"]["http"]["path"] == "/api/database"

    def test_idempotent(self):
        evt = _proxy_event("/api/api/version", CUSTOM_HOST, resource="/api/version")
        normalize_event(evt)
        normalize_event(evt)
        assert evt["requestContext"]["http"]["path"] == "/api/version"

    def test_v2_shaped_event_is_not_stripped(self):
        evt = {"requestContext": {"domainName": CUSTOM_HOST,
                                  "http": {"path": "/api/version", "method": "GET",
                                           "sourceIp": "1.2.3.4"}}}
        assert normalize_event(evt)["requestContext"]["http"]["path"] == "/api/version"

    def test_base_path_with_slashes_in_the_environment(self, monkeypatch):
        monkeypatch.setenv("API_CUSTOM_DOMAIN_BASE_PATH", "/api/")
        out = normalize_event(_proxy_event("/api/database", CUSTOM_HOST))
        assert out["requestContext"]["http"]["path"] == "/database"


@pytest.mark.unit
@pytest.mark.usefixtures("no_custom_domain_env")
class TestCustomDomainBasePathDisabled:
    """No custom domain configured: the environment is unset and nothing is stripped."""

    def test_no_environment_is_a_no_op(self):
        out = normalize_event(_proxy_event("/api/database", CUSTOM_HOST))
        assert out["requestContext"]["http"]["path"] == "/api/database"

    def test_domain_without_base_path_is_a_no_op(self, monkeypatch):
        monkeypatch.setenv("API_CUSTOM_DOMAIN_NAME", CUSTOM_HOST)
        out = normalize_event(_proxy_event("/api/database", CUSTOM_HOST))
        assert out["requestContext"]["http"]["path"] == "/api/database"

    def test_base_path_without_domain_is_a_no_op(self, monkeypatch):
        monkeypatch.setenv("API_CUSTOM_DOMAIN_BASE_PATH", "api")
        out = normalize_event(_proxy_event("/api/database", CUSTOM_HOST))
        assert out["requestContext"]["http"]["path"] == "/api/database"

    def test_helper_returns_non_string_paths_unchanged(self):
        assert strip_custom_domain_base_path({}, None) is None
        assert strip_custom_domain_base_path({}, "") == ""
