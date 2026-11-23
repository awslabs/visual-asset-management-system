# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The workflow output step resolves the copy engine function name through the resource-name resolver (#1820).

The engine references the output step, so the output step cannot take the engine's name as an
environment variable; the name is published under the resource-name prefix
(``lambdaFunctions/largeObjectCopyEngine``). The output step used to read it with its own Rule 6
client on its first copy-engine call: a throttled read waited out five retries, answered "", and
``call_engine`` then waited out its deploy window and failed the run with "The copy engine function
is not known to this function" (3 of 1,000 runs in the #1187 burst test).

It now resolves the name at import like its table names. A lookup Parameter Store refuses fails init
with ``ResourceNameUnavailableError``, which the workflow definition retries before the handler ran;
a name not published yet is "" and is read again on the next copy-engine call.

The output step is loaded by path with the resolver pointed at a fake Parameter Store: every other
resource name it resolves at import comes from its break-glass environment variable, so only the
engine name reaches the fake.
"""

import importlib.util
import io
import itertools
import json
import os
import re
import sys
import time
import types
from unittest.mock import MagicMock

import pytest
from botocore.exceptions import ClientError

from backend.tests.ssmParametersStub import FakeClock, install_clock

BACKEND = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "backend"))
OUTPUT_STEP_PATH = os.path.join(BACKEND, "handlers", "workflows", "sfn", "processWorkflowExecutionOutput.py")
RESOURCE_PARAM_KEYS_PATH = os.path.abspath(os.path.join(BACKEND, "..", "..", "infra", "common", "resourceParamKeys.ts"))
PREFIX = "/vams-test/resourceNames"
ENGINE_KEY = "lambdaFunctions/largeObjectCopyEngine"

_loads = itertools.count()


def throttled(operation):
    return ClientError({"Error": {"Code": "ThrottlingException", "Message": "Rate exceeded"},
                        "ResponseMetadata": {"HTTPStatusCode": 400}}, operation)


class FakeParameterStore:
    """The three reads the resolver makes: chunk parameters, the prefix sweep and a single
    GetParameter. ``published`` maps a key below the prefix to its value; ``chunk_names``, when given,
    is the name map of one published chunk parameter (``<prefix>Chunks/0``), so a key it does not
    carry costs one GetParameter (the resolver's ``_read_one``); without it no chunk is published and
    the resolver sweeps the prefix. ``throttle`` refuses every call. ``calls`` records each operation."""

    def __init__(self, published=None, throttle=False, chunk_names=None):
        self.published = dict(published or {})
        self.throttle = throttle
        self.chunk_names = chunk_names
        self.calls = []

    def _call(self, operation):
        self.calls.append(operation)
        if self.throttle:
            raise throttled(operation)

    def get_parameters(self, Names, **_):
        self._call("GetParameters")
        chunk = f"{PREFIX}Chunks/0"
        if self.chunk_names is None or chunk not in Names:
            return {"Parameters": [], "InvalidParameters": list(Names)}
        body = json.dumps({"v": 1, "n": 1, "i": 0, "l": "layout-test", "m": self.chunk_names})
        return {"Parameters": [{"Name": chunk, "Value": body, "Type": "String"}],
                "InvalidParameters": [name for name in Names if name != chunk]}

    def get_parameters_by_path(self, Path, **_):
        self._call("GetParametersByPath")
        return {"Parameters": [{"Name": f"{Path}/{key}", "Value": value, "Type": "String"}
                               for key, value in sorted(self.published.items())]}

    def get_parameter(self, Name, **_):
        self._call("GetParameter")
        key = Name[len(PREFIX) + 1:]
        if key not in self.published:
            raise ClientError({"Error": {"Code": "ParameterNotFound", "Message": "missing"},
                               "ResponseMetadata": {"HTTPStatusCode": 400}}, "GetParameter")
        return {"Parameter": {"Name": Name, "Value": self.published[key], "Type": "String"}}


@pytest.fixture
def resolver(monkeypatch):
    """This test's resolver (common.resourceNames), with the prefix set, every ResourceKeys name
    answered by its break-glass variable, the engine variable unset, and a fake clock."""
    rn = sys.modules["common.resourceNames"]
    monkeypatch.setenv("VAMS_RESOURCE_PARAM_PREFIX", PREFIX)
    for key in vars(rn.ResourceKeys).values():
        if isinstance(key, rn.ResourceParamKey) and not any(os.environ.get(name) for name in key.env_var_names):
            monkeypatch.setenv(key.env_var_names[0], f"test-{key.param_key.replace('/', '-')}")
    monkeypatch.delenv("LARGE_OBJECT_COPY_FUNCTION_NAME", raising=False)
    monkeypatch.delenv("LARGE_OBJECT_COPY_FUNCTION_NAME_PARAMETER", raising=False)
    monkeypatch.setenv("METADATA_SERVICE_LAMBDA_FUNCTION_NAME", "test-metadata-service")
    monkeypatch.setenv("FILE_UPLOAD_LAMBDA_FUNCTION_NAME", "test-file-upload")
    for name, value in (("_cache", {}), ("_cache_fetched_at", 0.0), ("_ssm_client", None),
                        ("_missing_keys", {}), ("_failed_sweeps", 0), ("_sweep_retry_after", 0.0),
                        ("_window_logged_for", None), ("_cache_complete", True)):
        monkeypatch.setattr(rn, name, value, raising=False)
    clock = FakeClock()
    monkeypatch.setattr(rn, "_monotonic", clock.monotonic)
    monkeypatch.setattr(rn, "_sleep", clock.sleep)
    monkeypatch.setattr(rn, "_uniform", lambda low, high: high)
    return rn


def install(rn, monkeypatch, fake):
    """Point the resolver and this test's common.ssmParameters (the copy the output step binds when
    it loads) at the fake, with the helper on a fake clock too."""
    monkeypatch.setattr(rn, "_get_ssm_client", lambda: fake)
    helper = sys.modules["common.ssmParameters"]
    monkeypatch.setattr(helper, "_get_ssm_client", lambda: fake)
    install_clock(helper, FakeClock(), monkeypatch)
    return fake


def load_output_step(monkeypatch):
    """A private copy of the output step, loaded with the resolver above and stubbed auth modules."""
    authz = types.ModuleType("handlers.authz")
    authz.CasbinEnforcer = MagicMock()
    auth = types.ModuleType("handlers.auth")
    auth.request_to_claims = MagicMock(return_value={"tokens": ["SYSTEM_USER"], "roles": ["SYSTEM"]})
    monkeypatch.setitem(sys.modules, "handlers.authz", authz)
    monkeypatch.setitem(sys.modules, "handlers.auth", auth)
    spec = importlib.util.spec_from_file_location(f"processOutput_engine_name_{next(_loads)}", OUTPUT_STEP_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.unit
class TestTheNameIsResolvedAtImport:
    def test_a_refused_lookup_fails_init_with_the_retried_error(self, resolver, monkeypatch):
        fake = install(resolver, monkeypatch, FakeParameterStore(throttle=True))

        with pytest.raises(resolver.ResourceNameUnavailableError) as refused:
            load_output_step(monkeypatch)

        assert ENGINE_KEY in str(refused.value), "the refusal came from another lookup than the engine name"
        assert fake.calls, "the fake was never called, so the refusal proves nothing"

    def test_a_published_name_is_resolved_once_at_import(self, resolver, monkeypatch):
        fake = install(resolver, monkeypatch, FakeParameterStore({ENGINE_KEY: "vams-copy-engine"}))

        module = load_output_step(monkeypatch)

        assert module.copy_engine_function_name == "vams-copy-engine"
        calls = len(fake.calls)
        assert module._copy_engine_function_name() == "vams-copy-engine"
        assert len(fake.calls) == calls

    def test_a_name_not_published_is_empty(self, resolver, monkeypatch):
        install(resolver, monkeypatch, FakeParameterStore({"dynamoTables/other": "x"}))

        module = load_output_step(monkeypatch)

        assert module.copy_engine_function_name == ""

    def test_the_environment_variable_overrides_the_parameter(self, resolver, monkeypatch):
        fake = install(resolver, monkeypatch, FakeParameterStore(throttle=True))
        monkeypatch.setenv("LARGE_OBJECT_COPY_FUNCTION_NAME", "env-engine")

        module = load_output_step(monkeypatch)

        assert module.copy_engine_function_name == "env-engine"
        assert fake.calls == []


@pytest.mark.unit
class TestAnEmptyNameIsReadAgainOnTheNextCall:
    """A container that started before the deployment published the name (it may still be applying)
    reads it again on its next copy-engine call instead of keeping "" for its life."""

    @pytest.fixture
    def output_step(self, resolver, monkeypatch):
        install(resolver, monkeypatch, FakeParameterStore())
        module = load_output_step(monkeypatch)
        assert module.copy_engine_function_name == ""
        return module

    def test_a_name_published_since_init_is_used(self, output_step, monkeypatch):
        monkeypatch.setattr(output_step, "get_resource_name", lambda key: "late-engine")
        assert output_step._copy_engine_function_name() == "late-engine"
        assert output_step.copy_engine_function_name == "late-engine"

    @pytest.mark.parametrize("error", [KeyError("not yet"), RuntimeError("refused")])
    def test_a_lookup_that_fails_at_call_time_answers_empty_and_raises_nothing(self, output_step, monkeypatch, error):
        def refuse(key):
            raise error

        monkeypatch.setattr(output_step, "get_resource_name", refuse)
        assert output_step._copy_engine_function_name() == ""

    def test_the_resolver_refusal_at_call_time_is_caught(self, output_step, resolver, monkeypatch):
        def refuse(key):
            raise resolver.ResourceNameUnavailableError("throttled")

        monkeypatch.setattr(output_step, "get_resource_name", refuse)
        assert output_step._copy_engine_function_name() == ""

    def test_the_engine_call_resolves_the_name_through_it(self, output_step, monkeypatch):
        seen = []

        def call_engine(client, function_name, operation, **fields):
            seen.append(function_name())
            return {"ok": True}

        monkeypatch.setattr(output_step, "copy_engine_function_name", "vams-copy-engine")
        monkeypatch.setattr(output_step.loc, "call_engine", call_engine)
        assert output_step._engine(output_step.loc.OUTPUT_STEP_CANCEL, batchId="E1") == {"ok": True}
        assert seen == ["vams-copy-engine"]


# The two ways the real resolver records the engine key as missing at import: a chunk-loaded cache
# that does not carry it (one GetParameter, _read_one), or a prefix sweep that did not find it.
MISS_PATHS = {"chunk read": {}, "prefix sweep": None}
# The parameter's full name as CDK sets it on the output step, or unset (derived from the prefix).
PARAMETER_ENV = {"from the environment": f"{PREFIX}/{ENGINE_KEY}", "from the prefix": None}


@pytest.mark.unit
class TestANamePublishedAfterTheFirstMissIsFound:
    """The resolver remembers a key it found missing for MISSING_KEY_TTL_SECONDS (300 s), longer than
    call_engine waits for a name (ENGINE_CALL_DEPLOY_WAIT_SECONDS, 120 s). An output step container
    that started before the deployment published the engine name must still find it seconds later,
    so the call-time lookup reads the parameter itself. Against the real resolver and the real helper."""

    @pytest.fixture(params=sorted(MISS_PATHS))
    def missed(self, request, resolver, monkeypatch):
        fake = install(resolver, monkeypatch, FakeParameterStore(chunk_names=MISS_PATHS[request.param]))
        module = load_output_step(monkeypatch)
        assert module.copy_engine_function_name == ""
        assert ENGINE_KEY in resolver._missing_keys, "the resolver recorded no miss, so this proves nothing"
        # The miss was recorded 5 s ago; the deployment has published the name since.
        resolver._missing_keys[ENGINE_KEY] = time.time() - 5
        fake.published[ENGINE_KEY] = "vams-copy-engine"
        return module, fake

    @pytest.mark.parametrize("parameter", sorted(PARAMETER_ENV))
    def test_the_next_call_reads_the_parameter_and_finds_the_name(self, missed, resolver, monkeypatch, parameter):
        module, fake = missed
        if PARAMETER_ENV[parameter]:
            monkeypatch.setenv("LARGE_OBJECT_COPY_FUNCTION_NAME_PARAMETER", PARAMETER_ENV[parameter])
        calls = len(fake.calls)
        with pytest.raises(KeyError):
            resolver.get_resource_name(resolver.ResourceParamKey(ENGINE_KEY, ()))
        assert len(fake.calls) == calls, "the resolver read again, so its miss record blocks nothing here"

        assert module._copy_engine_function_name() == "vams-copy-engine"
        assert fake.calls[calls:] == ["GetParameter"]
        assert module._copy_engine_function_name() == "vams-copy-engine"
        assert fake.calls[calls:] == ["GetParameter"], "a found name was read again"

    def test_a_name_still_unpublished_answers_empty_and_is_read_on_each_call(self, missed):
        module, fake = missed
        del fake.published[ENGINE_KEY]
        calls = len(fake.calls)
        assert module._copy_engine_function_name() == ""
        assert module._copy_engine_function_name() == ""
        assert fake.calls[calls:] == ["GetParameter", "GetParameter"]

    def test_a_throttled_read_answers_empty(self, missed):
        module, fake = missed
        fake.throttle = True
        assert module._copy_engine_function_name() == ""

    def test_the_engine_call_waits_for_the_name_and_then_invokes_it(self, missed, monkeypatch):
        """End to end through loc.call_engine: the first resolve finds nothing, call_engine waits its
        first deploy-window delay, the name is published meanwhile, and the next resolve finds it."""
        module, fake = missed
        del fake.published[ENGINE_KEY]
        sleeps = []

        def sleep(seconds):
            sleeps.append(seconds)
            fake.published[ENGINE_KEY] = "vams-copy-engine"

        monkeypatch.setattr(time, "sleep", sleep)
        invoke = MagicMock(return_value={"Payload": io.BytesIO(json.dumps({"ok": True}).encode())})
        monkeypatch.setattr(module, "engine_invoke_client", MagicMock(invoke=invoke))

        assert module._engine(module.loc.OUTPUT_STEP_CANCEL, batchId="E1") == {"ok": True}
        assert sleeps == [module.loc.ENGINE_CALL_DEPLOY_DELAYS[0]]
        assert invoke.call_args.kwargs["FunctionName"] == "vams-copy-engine"


@pytest.mark.unit
class TestTheKeyMatchesTheDeployment:
    def test_the_backend_key_is_the_infra_key(self):
        """Drift pin: CDK publishes the name under RESOURCE_PARAM_KEYS.lambdaFunctions.largeObjectCopyEngine."""
        with open(RESOURCE_PARAM_KEYS_PATH, encoding="utf-8") as handle:
            match = re.search(r'largeObjectCopyEngine:\s*"([^"]+)"', handle.read())
        assert match, "RESOURCE_PARAM_KEYS.lambdaFunctions.largeObjectCopyEngine not found"
        loc = sys.modules["common.largeObjectCopy"]
        assert loc.ENGINE_FUNCTION_RESOURCE_KEY.param_key == match.group(1) == ENGINE_KEY

    def test_the_output_step_builds_no_ssm_client_of_its_own(self):
        with open(OUTPUT_STEP_PATH, encoding="utf-8") as handle:
            source = handle.read()
        assert not re.search(r"boto3\.client\(\s*['\"]ssm['\"]", source)
