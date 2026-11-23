# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""common/deployTimeValues.py: the OpenSearch endpoint and index names, the web URL and the map key
ARN, resolved from the resource names (the chunk parameters a cold start already loaded) and read
from the parameters that held them before only when the resource names do not carry a key.

The keys are compared with the openSearch, web and location categories of
infra/common/resourceParamKeys.ts. ``resolve()`` and ``resolve_together()`` are driven with a
recording lookup and reader; the last class runs them over the real resolver and a fake SSM client
that serves chunk parameters, counting the calls.
"""

import importlib.util
import json
import re
from pathlib import Path

import pytest

from backend.tests.ssmParametersStub import client_error

BACKEND = Path(__file__).resolve().parents[2] / "backend" / "common"
CANONICAL_KEYS_TS = Path(__file__).resolve().parents[3] / "infra" / "common" / "resourceParamKeys.ts"
DEPLOY_TIME_CATEGORIES = ("openSearch", "web", "location")


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def dtv():
    """A private copy of the shipped module (the root conftest registers fresh resourceNames and
    ssmParameters copies for every test, which this copy binds)."""
    return _load("deployTimeValues_under_test", BACKEND / "deployTimeValues.py")


class RecordingLogger:
    def __init__(self):
        self.lines = []

    def __getattr__(self, level):
        if level in ("debug", "info", "warning", "error", "exception"):
            return lambda message, *a, **k: self.lines.append((level, str(message)))
        raise AttributeError(level)


class Names:
    """Stands in for get_resource_name: ``values`` by key; a key in ``errors`` raises that error; any
    other key raises KeyError. Records every key looked up."""

    def __init__(self, values=None, errors=None):
        self.values = dict(values or {})
        self.errors = dict(errors or {})
        self.lookups = []

    def __call__(self, key):
        self.lookups.append(key.param_key)
        if key.param_key in self.errors:
            raise self.errors[key.param_key]
        if key.param_key not in self.values:
            raise KeyError("Resource name parameter missing from SSM: " + key.param_key)
        return self.values[key.param_key]


class Reader:
    """Stands in for get_parameter_value: ``values`` by parameter name, KeyError otherwise."""

    def __init__(self, values=None):
        self.values = dict(values or {})
        self.reads = []

    def __call__(self, name, *, ttl_seconds):
        self.reads.append((name, ttl_seconds))
        if name not in self.values:
            raise KeyError(f"Parameter Store parameter not found: {name}")
        return self.values[name]


def _wire(dtv, names, reader):
    dtv.get_resource_name = names
    dtv.get_parameter_value = reader
    dtv.logger = RecordingLogger()
    return dtv.logger


def _canonical_deploy_time_keys():
    source = CANONICAL_KEYS_TS.read_text(encoding="utf-8")
    source = re.sub(r"/\*.*?\*/", "", source, flags=re.DOTALL)
    source = re.sub(r"//[^\n]*", "", source)
    keys = set()
    for category in DEPLOY_TIME_CATEGORIES:
        block = re.search(r"\b" + category + r":\s*\{([^}]*)\}", source)
        assert block, "category %s missing from resourceParamKeys.ts" % category
        keys |= set(re.findall(r'[A-Za-z][A-Za-z0-9_]*\s*:\s*"([^"]+)"', block.group(1)))
    return keys


@pytest.mark.unit
class TestKeys:
    def test_mirror_the_deploy_time_categories(self, dtv):
        canonical = _canonical_deploy_time_keys()
        mirrored = {v.param_key for v in vars(dtv.DeployTimeKeys).values() if isinstance(v, dtv.ResourceParamKey)}
        # Control: both sides populated, so the equality is not two empty sets.
        assert len(canonical) >= 5
        assert {"openSearch/endpoint", "web/deployedUrl", "location/apiKeyArn"} <= canonical
        assert mirrored == canonical

    def test_no_key_takes_an_environment_override(self, dtv):
        keys = [v for v in vars(dtv.DeployTimeKeys).values() if isinstance(v, dtv.ResourceParamKey)]
        assert keys
        assert all(k.env_var_names == () for k in keys)

    def test_keys_are_not_in_resource_keys(self, dtv):
        import common.resourceNames as rn
        mirrored = {v.param_key for v in vars(dtv.DeployTimeKeys).values() if isinstance(v, dtv.ResourceParamKey)}
        resource_keys = {v.param_key for v in vars(rn.ResourceKeys).values() if isinstance(v, rn.ResourceParamKey)}
        assert resource_keys
        assert not mirrored & resource_keys


@pytest.mark.unit
class TestResolve:
    def test_a_key_the_resource_names_carry_reads_no_parameter(self, dtv):
        names, reader = Names({"web/deployedUrl": "https://web.example"}), Reader()
        _wire(dtv, names, reader)
        assert dtv.resolve(dtv.DeployTimeKeys.WEB_DEPLOYED_URL, "/vams/web/deployedUrl") == "https://web.example"
        assert reader.reads == []

    def test_a_key_not_carried_yet_reads_the_legacy_parameter_with_its_ttl_and_logs_once(self, dtv):
        names, reader = Names(), Reader({"/vams/web/deployedUrl": "https://legacy.example"})
        log = _wire(dtv, names, reader)
        for _ in range(3):
            assert dtv.resolve(dtv.DeployTimeKeys.WEB_DEPLOYED_URL, "/vams/web/deployedUrl",
                               legacy_ttl_seconds=300) == "https://legacy.example"
        assert reader.reads == [("/vams/web/deployedUrl", 300)] * 3
        info = [m for level, m in log.lines if level == "info"]
        assert info == ["web/deployedUrl not among the resource names yet; reading /vams/web/deployedUrl"]
        assert "https://legacy.example" not in " ".join(m for _, m in log.lines)

    def test_the_default_legacy_ttl_is_the_parameter_cache_ttl(self, dtv):
        names, reader = Names(), Reader({"/vams/location/apiKeyArn": "arn:aws:geo:r:a:api-key/k"})
        _wire(dtv, names, reader)
        dtv.resolve(dtv.DeployTimeKeys.LOCATION_API_KEY_ARN, "/vams/location/apiKeyArn")
        assert reader.reads == [("/vams/location/apiKeyArn", dtv.PARAMETER_CACHE_TTL_SECONDS)]

    def test_a_lookup_that_cannot_read_parameter_store_reads_no_legacy_parameter(self, dtv):
        import common.resourceNames as rn
        refused = rn.ResourceNameUnavailableError("throttled")
        names = Names(errors={"web/deployedUrl": refused})
        reader = Reader({"/vams/web/deployedUrl": "https://legacy.example"})
        _wire(dtv, names, reader)
        with pytest.raises(rn.ResourceNameUnavailableError):
            dtv.resolve(dtv.DeployTimeKeys.WEB_DEPLOYED_URL, "/vams/web/deployedUrl")
        assert reader.reads == []

    def test_without_a_legacy_name_the_key_error_is_raised(self, dtv):
        names, reader = Names(), Reader()
        _wire(dtv, names, reader)
        with pytest.raises(KeyError):
            dtv.resolve(dtv.DeployTimeKeys.OPENSEARCH_ENDPOINT)
        assert reader.reads == []

    def test_a_missing_legacy_parameter_raises_a_key_error(self, dtv):
        names, reader = Names(), Reader()
        _wire(dtv, names, reader)
        with pytest.raises(KeyError):
            dtv.resolve(dtv.DeployTimeKeys.WEB_DEPLOYED_URL, "/vams/web/deployedUrl")


OS_KEYS = ("openSearch/endpoint", "openSearch/assetIndexName", "openSearch/fileIndexName")
LEGACY = ("/vams/aos/endPoint", "/vams/aos/assetIndexName", "/vams/aos/fileIndexName")


def _os_keys(dtv):
    k = dtv.DeployTimeKeys
    return (k.OPENSEARCH_ENDPOINT, k.OPENSEARCH_ASSET_INDEX_NAME, k.OPENSEARCH_FILE_INDEX_NAME)


@pytest.mark.unit
class TestResolveTogether:
    def test_all_carried_reads_no_parameter(self, dtv):
        names = Names(dict(zip(OS_KEYS, ("https://os", "assets", "files"))))
        reader = Reader()
        _wire(dtv, names, reader)
        assert dtv.resolve_together(_os_keys(dtv), LEGACY) == ["https://os", "assets", "files"]
        assert names.lookups == list(OS_KEYS)
        assert reader.reads == []

    def test_the_first_key_not_carried_reads_every_legacy_parameter_and_looks_up_no_other_key(self, dtv):
        names = Names()
        reader = Reader(dict(zip(LEGACY, ("https://old", "assets-old", "files-old"))))
        log = _wire(dtv, names, reader)
        assert dtv.resolve_together(_os_keys(dtv), LEGACY) == ["https://old", "assets-old", "files-old"]
        assert names.lookups == ["openSearch/endpoint"]
        assert [name for name, _ in reader.reads] == list(LEGACY)
        assert len([m for level, m in log.lines if level == "info"]) == 1

    def test_a_later_key_not_carried_falls_back_for_the_whole_set(self, dtv):
        names = Names({"openSearch/endpoint": "https://new"})
        reader = Reader(dict(zip(LEGACY, ("https://old", "assets-old", "files-old"))))
        _wire(dtv, names, reader)
        assert dtv.resolve_together(_os_keys(dtv), LEGACY) == ["https://old", "assets-old", "files-old"]

    def test_a_refused_lookup_propagates_without_a_legacy_read(self, dtv):
        import common.resourceNames as rn
        names = Names(errors={"openSearch/endpoint": rn.ResourceNameUnavailableError("throttled")})
        reader = Reader(dict(zip(LEGACY, ("a", "b", "c"))))
        _wire(dtv, names, reader)
        with pytest.raises(rn.ResourceNameUnavailableError):
            dtv.resolve_together(_os_keys(dtv), LEGACY)
        assert reader.reads == []

    def test_a_missing_legacy_name_raises_the_key_error(self, dtv):
        names, reader = Names(), Reader()
        _wire(dtv, names, reader)
        with pytest.raises(KeyError):
            dtv.resolve_together(_os_keys(dtv), ("/vams/aos/endPoint", "", "/vams/aos/fileIndexName"))
        assert reader.reads == []

    def test_one_legacy_name_per_key(self, dtv):
        _wire(dtv, Names(), Reader())
        with pytest.raises(ValueError):
            dtv.resolve_together(_os_keys(dtv), LEGACY[:2])


PREFIX = "/vams-test/resourceNames"


class ChunkSsm:
    """One chunk parameter carrying ``names``, plus plain parameters by full name; records calls."""

    def __init__(self, names, parameters=None):
        self.chunk = json.dumps({"v": 1, "n": 1, "i": 0, "l": "layout1", "m": names}, separators=(",", ":"))
        self.parameters = dict(parameters or {})
        self.calls = []

    def get_parameters(self, Names, **_):
        self.calls.append(("GetParameters", list(Names)))
        found = [{"Name": n, "Value": self.chunk, "Type": "String"} for n in Names if n == f"{PREFIX}Chunks/0"]
        return {"Parameters": found, "InvalidParameters": [n for n in Names if n != f"{PREFIX}Chunks/0"]}

    def get_parameter(self, Name, **_):
        self.calls.append(("GetParameter", Name))
        if Name not in self.parameters:
            raise client_error("ParameterNotFound")
        return {"Parameter": {"Name": Name, "Value": self.parameters[Name], "Type": "String"}}

    def count(self, operation):
        return sum(1 for op, _ in self.calls if op == operation)


@pytest.mark.unit
class TestOverTheResolver:
    """resolve() over the real resolver (this test's fresh common.resourceNames) and reader."""

    @pytest.fixture
    def wired(self, monkeypatch):
        import sys
        monkeypatch.setenv("VAMS_RESOURCE_PARAM_PREFIX", PREFIX)

        def wire(ssm):
            rn = sys.modules["common.resourceNames"]
            sp = sys.modules["common.ssmParameters"]
            monkeypatch.setattr(rn, "_ssm_client", ssm)
            monkeypatch.setattr(sp, "_ssm_client", ssm)
            return _load("deployTimeValues_over_resolver", BACKEND / "deployTimeValues.py")
        return wire

    def test_a_carried_key_costs_the_one_chunk_read(self, wired):
        ssm = ChunkSsm({"web/deployedUrl": "https://web.example", "dynamoTables/assetStorage": "t"})
        dtv = wired(ssm)
        assert dtv.resolve(dtv.DeployTimeKeys.WEB_DEPLOYED_URL, "/vams/web/deployedUrl") == "https://web.example"
        assert dtv.resolve(dtv.DeployTimeKeys.WEB_DEPLOYED_URL, "/vams/web/deployedUrl") == "https://web.example"
        assert (ssm.count("GetParameters"), ssm.count("GetParameter")) == (1, 0)

    def test_a_key_not_carried_costs_one_missed_lookup_then_the_legacy_read(self, wired):
        ssm = ChunkSsm({"dynamoTables/assetStorage": "t"}, {"/vams/web/deployedUrl": "https://legacy.example"})
        dtv = wired(ssm)
        assert dtv.resolve(dtv.DeployTimeKeys.WEB_DEPLOYED_URL, "/vams/web/deployedUrl") == "https://legacy.example"
        assert [c for c in ssm.calls if c[0] == "GetParameter"] == [
            ("GetParameter", f"{PREFIX}/web/deployedUrl"), ("GetParameter", "/vams/web/deployedUrl")]
