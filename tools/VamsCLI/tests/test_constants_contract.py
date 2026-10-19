"""Contract tests for `vamscli/constants.py`.

`constants.py` is the authority a new consumer trusts (CLAUDE.md Rule 7): the feature-switch names
it declares are the gates the CLI can express, and the endpoint paths it declares are the routes the
CLI can reach. Neither is validated by anything else — an incomplete feature mirror is silently
unexpressible, and a path the API never registered fails only at runtime, against a live deployment.
"""

import ast
import re
from pathlib import Path
from unittest.mock import Mock

from vamscli import addon_constants
from vamscli import constants
from vamscli import custom_constants
from vamscli.utils.api_client import APIClient


REPO_ROOT = Path(__file__).resolve().parents[3]
VAMS_APP_FEATURES_TS = REPO_ROOT / "infra/common/vamsAppFeatures.ts"
ADDON_APP_FEATURES_TS = REPO_ROOT / "infra/common/addonAppFeatures.ts"
CUSTOM_APP_FEATURES_TS = REPO_ROOT / "infra/common/customAppFeatures.ts"
CLI_CONSTANTS_PY = Path(constants.__file__)
BACKEND_API_ROUTES = REPO_ROOT / "backend/backend/common/apiRoutes.py"
# The route lists apiRoutes.py splices into ALL_API_ROUTES from other files.
BACKEND_SPLICED_ROUTE_LISTS = (
    REPO_ROOT / "backend/backend/common/addonApiRoutes.py",
    REPO_ROOT / "backend/backend/custom/customApiRoutes.py",
)


def _enum_members(source: str, enum_name: str = "VAMS_APP_FEATURES") -> set:
    """The string values of the members of the named feature enum."""
    body = re.search(rf"export enum {enum_name}\s*\{{(.*?)\}}", source, re.S)
    assert body, f"{enum_name} enum not found"
    return set(re.findall(r'=\s*"([A-Z0-9_]+)"', body.group(1)))


def _all_published_switches() -> set:
    """Every value a deployment can publish: the core, add-on and custom enums together."""
    return (
        _enum_members(VAMS_APP_FEATURES_TS.read_text(encoding="utf-8"))
        | _enum_members(ADDON_APP_FEATURES_TS.read_text(encoding="utf-8"), "ADDON_APP_FEATURES")
        | _enum_members(CUSTOM_APP_FEATURES_TS.read_text(encoding="utf-8"), "CUSTOM_APP_FEATURES")
    )


def _feature_values(module) -> set:
    """The FEATURE_* string values a module exposes, re-exported names included."""
    return {
        value for name, value in vars(module).items()
        if name.startswith("FEATURE_") and isinstance(value, str)
    }


def _feature_values_assigned(source: str) -> set:
    """The FEATURE_* string values assigned at the top level of a module's own source."""
    values = set()
    for node in ast.parse(source).body:
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id.startswith("FEATURE_"):
                    values.add(node.value.value)
    return values


def _feature_values_assigned_in(path: Path) -> set:
    return _feature_values_assigned(path.read_text(encoding="utf-8"))


class TestFeatureSwitchMirror:
    """The CLI's FEATURE_* constants mirror the published feature switches, lane by lane.

    A deployment publishes the core VAMS_APP_FEATURES, the add-on ADDON_APP_FEATURES and a fork's
    CUSTOM_APP_FEATURES values through /secure-config. A member the CLI does not declare is a gate no
    command can name, and the documented switch table inherits the same gap. Each lane has its own
    CLI file (`constants.py`, `addon_constants.py`, `custom_constants.py`); `constants.py` re-exports
    the add-on names so existing imports keep working.
    """

    def test_the_enum_source_is_present(self):
        # Without this the parse below would yield an empty set and every assertion would pass
        # vacuously on a checkout where the infra directory moved.
        for source_file in (VAMS_APP_FEATURES_TS, ADDON_APP_FEATURES_TS, CUSTOM_APP_FEATURES_TS):
            assert source_file.is_file(), (
                f"the CDK feature enum was not found at {source_file}; this test cannot "
                "compare the CLI mirror against anything"
            )

    def test_every_published_switch_has_a_cli_constant_and_vice_versa(self):
        """CLI feature constants equal core + add-on + custom, in both directions."""
        declared = _all_published_switches()
        assert declared, "parsed no members out of the feature enums"
        mirrored = _feature_values(constants) | _feature_values(custom_constants)
        assert declared - mirrored == set(), (
            "feature switches published by a deployment that the CLI cannot name: "
            f"{sorted(declared - mirrored)}"
        )
        assert mirrored - declared == set(), (
            "CLI feature-switch constants naming a gate no deployment publishes: "
            f"{sorted(mirrored - declared)}"
        )

    def test_constants_py_declares_exactly_the_core_switches(self):
        """`constants.py` declares the core lane itself; the add-on lane only arrives re-exported."""
        declared = _enum_members(VAMS_APP_FEATURES_TS.read_text(encoding="utf-8"))
        own = _feature_values_assigned_in(CLI_CONSTANTS_PY)
        assert declared - own == set(), f"core switches the CLI cannot name: {sorted(declared - own)}"
        assert own - declared == set(), (
            "constants.py declares FEATURE_* values that are not core switches; an add-on switch "
            f"goes in vamscli/addon_constants.py: {sorted(own - declared)}"
        )

    def test_addon_constants_mirror_the_addon_enum(self):
        declared = _enum_members(ADDON_APP_FEATURES_TS.read_text(encoding="utf-8"), "ADDON_APP_FEATURES")
        assert "PHYSNA_ADDON" in declared, "parsed no Physna member out of ADDON_APP_FEATURES"
        mirrored = _feature_values(addon_constants)
        assert declared - mirrored == set(), (
            f"add-on feature switches the CLI cannot name: {sorted(declared - mirrored)}"
        )
        assert mirrored - declared == set(), (
            f"add-on CLI feature constants naming no published switch: {sorted(mirrored - declared)}"
        )

    def test_constants_py_re_exports_the_addon_names(self):
        """Existing `from vamscli.constants import FEATURE_PHYSNA_ADDON` imports keep working."""
        for name, value in vars(addon_constants).items():
            if name.startswith("FEATURE_"):
                assert getattr(constants, name) == value

    def test_the_assignment_parser_reads_constants(self):
        # Control: the source parse used above sees a top-level FEATURE_ assignment and ignores an
        # import and a non-FEATURE name.
        sample = 'from x import *\nFEATURE_A = "A"\nAPI_B = "/b"\n'
        assert _feature_values_assigned(sample) == {"A"}


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
    """Every route path declared in the backend's master registry and the lists it splices in."""
    routes = set()
    for source_file in (BACKEND_API_ROUTES, *BACKEND_SPLICED_ROUTE_LISTS):
        source = source_file.read_text(encoding="utf-8")
        routes |= set(re.findall(r'ApiRoute\(\s*"([^"]+)"', source))
    return routes


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
        # Positive control on the parse itself: two routes of different shapes must be present,
        # and one from the spliced add-on list.
        assert "/database" in routes
        assert "/database/{databaseId}/assets/{assetId}" in routes
        assert "/addon/physna/viewer" in routes

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


class TestCustomConstantsContract:
    """`custom_constants.py` (fork-owned, shipped empty) is held to the same contract as `constants.py`.

    Every endpoint path a fork declares there must be a route the backend declares (core, add-on or
    `custom/customApiRoutes.py`), and its `FEATURE_*` names must mirror
    `infra/common/customAppFeatures.ts` in both directions. The assertions hold for any content, so
    they keep holding once a fork fills the files in.
    """

    def _custom_path_constants(self) -> dict:
        return {
            name: value for name, value in vars(custom_constants).items()
            if name.startswith("API_") and isinstance(value, str)
        }

    def _custom_feature_constants(self) -> set:
        return {
            value for name, value in vars(custom_constants).items()
            if name.startswith("FEATURE_") and isinstance(value, str)
        }

    def test_the_custom_sources_are_present(self):
        # Without these the comparisons below would run against empty sets for the wrong reason.
        assert CUSTOM_APP_FEATURES_TS.is_file(), f"custom feature enum not found at {CUSTOM_APP_FEATURES_TS}"
        _enum_members(CUSTOM_APP_FEATURES_TS.read_text(encoding="utf-8"), "CUSTOM_APP_FEATURES")
        for source_file in BACKEND_SPLICED_ROUTE_LISTS:
            assert source_file.is_file(), f"spliced route list not found at {source_file}"

    def test_every_custom_path_constant_is_a_path(self):
        offenders = {n: v for n, v in self._custom_path_constants().items() if not v.startswith("/")}
        assert offenders == {}, f"custom API path constants that are not paths: {offenders}"

    def test_every_custom_path_constant_is_a_registered_route(self):
        routes = _registry_routes()
        unmatched = {n: v for n, v in self._custom_path_constants().items() if v not in routes}
        assert unmatched == {}, (
            "custom CLI endpoint constants naming a path the backend does not declare (add the "
            f"route to backend/backend/custom/customApiRoutes.py): {unmatched}"
        )

    def test_custom_feature_constants_mirror_the_custom_enum(self):
        declared = _enum_members(
            CUSTOM_APP_FEATURES_TS.read_text(encoding="utf-8"), "CUSTOM_APP_FEATURES"
        )
        mirrored = self._custom_feature_constants()
        assert declared - mirrored == set(), (
            f"custom feature switches the CLI cannot name: {sorted(declared - mirrored)}"
        )
        assert mirrored - declared == set(), (
            f"custom CLI feature constants naming no published switch: {sorted(mirrored - declared)}"
        )

    def test_the_enum_parser_reads_members(self):
        # Control: the parser used above returns members for a populated enum of the same shape.
        sample = 'export enum CUSTOM_APP_FEATURES {\n    CUSTOM_WIDGETS = "CUSTOM_WIDGETS",\n}\n'
        assert _enum_members(sample, "CUSTOM_APP_FEATURES") == {"CUSTOM_WIDGETS"}
