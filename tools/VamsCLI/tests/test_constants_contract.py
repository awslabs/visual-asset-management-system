"""Contract tests for `vamscli/constants.py`.

`constants.py` is the authority a new consumer trusts (CLAUDE.md Rule 7): the feature-switch names
it declares are the gates the CLI can express, and the endpoint paths it declares are the routes the
CLI can reach. Neither is validated by anything else — an incomplete feature mirror is silently
unexpressible, and a path the API never registered fails only at runtime, against a live deployment.
"""

import re
from pathlib import Path
from unittest.mock import Mock

from vamscli import constants
from vamscli.utils.api_client import APIClient


REPO_ROOT = Path(__file__).resolve().parents[3]
VAMS_APP_FEATURES_TS = REPO_ROOT / "infra/common/vamsAppFeatures.ts"
BACKEND_API_ROUTES = REPO_ROOT / "backend/backend/common/apiRoutes.py"


def _enum_members(source: str) -> set:
    """The string values of the VAMS_APP_FEATURES enum members."""
    body = re.search(r"export enum VAMS_APP_FEATURES\s*\{(.*?)\}", source, re.S)
    assert body, "VAMS_APP_FEATURES enum not found in vamsAppFeatures.ts"
    return set(re.findall(r'=\s*"([A-Z0-9_]+)"', body.group(1)))


class TestFeatureSwitchMirror:
    """Every VAMS_APP_FEATURES member has a FEATURE_* constant, and vice versa.

    A deployment publishes the enum values through /secure-config. A member the CLI does not declare
    is a gate no command can name, and the documented switch table inherits the same gap.
    """

    def test_the_enum_source_is_present(self):
        # Without this the parse below would yield an empty set and every assertion would pass
        # vacuously on a checkout where the infra directory moved.
        assert VAMS_APP_FEATURES_TS.is_file(), (
            f"the CDK feature enum was not found at {VAMS_APP_FEATURES_TS}; this test cannot "
            "compare the CLI mirror against anything"
        )

    def test_every_enum_member_has_a_cli_constant(self):
        declared = _enum_members(VAMS_APP_FEATURES_TS.read_text(encoding="utf-8"))
        assert declared, "parsed no members out of the VAMS_APP_FEATURES enum"
        mirrored = {
            value for name, value in vars(constants).items()
            if name.startswith("FEATURE_") and isinstance(value, str)
        }
        assert declared - mirrored == set(), (
            "feature switches published by a deployment that the CLI cannot name: "
            f"{sorted(declared - mirrored)}"
        )

    def test_the_cli_declares_no_switch_the_enum_does_not(self):
        declared = _enum_members(VAMS_APP_FEATURES_TS.read_text(encoding="utf-8"))
        mirrored = {
            value for name, value in vars(constants).items()
            if name.startswith("FEATURE_") and isinstance(value, str)
        }
        assert mirrored - declared == set(), (
            "CLI feature-switch constants naming a gate no deployment publishes: "
            f"{sorted(mirrored - declared)}"
        )


class TestMetadataSchemaEndpointExists:
    """No constant may name a /metadataschema path the API does not register.

    The API registers the collection route `/metadataschema` and the id-scoped
    `/database/{databaseId}/metadataSchema/{metadataSchemaId}`. A request to a path-scoped
    `/metadataschema/{databaseId}` matches no API Gateway route, so it is rejected before any
    handler runs — nothing in the CLI catches that at import or build time.
    """

    def test_no_constant_declares_a_path_scoped_metadataschema_route(self):
        offenders = {
            name: value for name, value in vars(constants).items()
            if name.startswith("API_") and isinstance(value, str)
            and re.match(r"^/metadataschema/\{", value)
        }
        assert offenders == {}, (
            f"constants naming an unregistered path-scoped metadata-schema route: {offenders}"
        )

    def test_the_backend_registry_defines_only_the_two_routes(self):
        # Anchors the assertion above to the master registry rather than to this test's belief
        # about it, so a backend that later adds the path-scoped route fails here instead of
        # leaving the CLI wrongly constrained.
        source = BACKEND_API_ROUTES.read_text(encoding="utf-8")
        assert 'ApiRoute("/metadataschema"' in source
        assert '"/metadataschema/{databaseId}"' not in source

    def test_get_metadata_schema_requests_the_collection_route_with_a_filter(self, monkeypatch):
        """The deprecated per-database getter reaches a route that exists, filtering by databaseId."""
        # A real ProfileManager is constructed when none is supplied, which reads the developer's
        # config directory — pass a stub so the assertion depends only on the endpoint built.
        client = APIClient("https://api.example.com", profile_manager=Mock())
        seen = {}

        class _Response:
            def json(self):
                return {"Items": []}

        def _fake_get(endpoint, include_auth=True, params=None, **kwargs):
            seen["endpoint"] = endpoint
            seen["params"] = params
            return _Response()

        monkeypatch.setattr(client, "get", _fake_get)
        client.get_metadata_schema("my-database")

        assert seen["endpoint"] == constants.API_METADATA_SCHEMA_LIST == "/metadataschema"
        assert seen["params"]["databaseId"] == "my-database"


def _registry_routes() -> set:
    """Every route path declared in the backend's master registry."""
    source = BACKEND_API_ROUTES.read_text(encoding="utf-8")
    return set(re.findall(r'ApiRoute\(\s*"([^"]+)"', source))


def _cli_path_constants() -> dict:
    return {
        name: value for name, value in vars(constants).items()
        if name.startswith("API_") and isinstance(value, str) and value.startswith("/")
    }


# Constants that are a path PREFIX rather than a whole route, and are concatenated at the call site
# instead of formatted. Each entry has to name why, because an unexplained exemption is how a dead
# path constant survives this check. Prefer a format-string constant matching a registered route.
_PREFIX_ONLY_CONSTANTS = {
    "API_LOGIN_PROFILE": (
        "used as f'{API_LOGIN_PROFILE}/{user_id}' in APIClient.call_login_profile, so the request "
        "reaches the registered /auth/loginProfile/{userId}"
    ),
}


class TestEveryEndpointConstantIsAPath:
    """A path constant that lost its leading slash silently builds a request against the wrong URL."""

    def test_api_constants_start_with_a_slash(self):
        offenders = {
            name: value for name, value in vars(constants).items()
            if name.startswith("API_") and isinstance(value, str) and not value.startswith("/")
        }
        assert offenders == {}, f"API path constants that are not paths: {offenders}"


class TestEveryEndpointConstantMatchesARegisteredRoute:
    """Generalizes the metadata-schema check to the whole endpoint surface.

    A constant naming a path the API never registered is rejected by API Gateway before any handler
    runs, and nothing in the CLI catches it at import or build time. Two have shipped in this release
    (`/metadataschema/{databaseId}`, formatted by a live method, and
    `/asset-links/{assetLinkId}/metadata/{metadataKey}`, imported but never formatted), so the class
    is checked mechanically rather than one instance at a time.

    Path-parameter names are compared verbatim: a constant whose placeholder is spelled differently
    from the registry's would not resolve against the deployed route either.
    """

    def test_the_registry_parse_is_not_vacuous(self):
        # Without this the regex could match nothing on a refactored registry and every assertion
        # below would pass against an empty set.
        assert BACKEND_API_ROUTES.is_file(), f"master route registry not found at {BACKEND_API_ROUTES}"
        routes = _registry_routes()
        assert len(routes) > 80, (
            f"parsed only {len(routes)} routes out of the master registry; the ApiRoute declaration "
            "shape has changed and this check is no longer reading it"
        )
        # Positive control on the parse itself: two routes of different shapes must be present.
        assert "/database" in routes
        assert "/database/{databaseId}/assets/{assetId}" in routes

    def test_every_cli_path_constant_is_a_registered_route(self):
        routes = _registry_routes()
        unmatched = {
            name: value for name, value in _cli_path_constants().items()
            if value not in routes and name not in _PREFIX_ONLY_CONSTANTS
        }
        assert unmatched == {}, (
            "CLI endpoint constants naming a path the API does not register (a request to one is "
            f"rejected by API Gateway before any handler runs): {unmatched}"
        )

    def test_the_prefix_exemptions_have_not_rotted(self):
        """An exemption that outlives its constant, or whose constant stops being a real prefix,
        silently widens the check above. Fail so it gets deleted rather than carried."""
        cli = _cli_path_constants()
        routes = _registry_routes()
        for name, reason in _PREFIX_ONLY_CONSTANTS.items():
            assert name in cli, (
                f"{name} is exempted from the route check but no longer exists — delete the "
                f"_PREFIX_ONLY_CONSTANTS entry. Reason recorded: {reason}"
            )
            value = cli[name]
            assert value not in routes, (
                f"{name} now matches a registered route exactly, so it no longer needs an "
                "exemption — delete the _PREFIX_ONLY_CONSTANTS entry"
            )
            assert any(route.startswith(value + "/") for route in routes), (
                f"{name} = {value!r} is exempted as a path prefix, but no registered route starts "
                "with it — it is a dead path, not a prefix"
            )
