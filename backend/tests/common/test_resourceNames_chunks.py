# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Loading the resource names from the chunk parameters.

The deployment publishes every registered resource name a second time, as JSON in numbered chunk
parameters ``<prefix>Chunks/<i>`` (infra ``resourceNameChunks.ts``). A refresh in
``common/resourceNames.py`` reads chunks 0-9 with one GetParameters call, takes the chunk count from
chunk 0 and reads only the batches that remain, so a cold start costs ceil(n / 10) calls instead of
one GetParametersByPath call per 10 parameters. When the chunks cannot answer (none published, one
missing, a mismatch, unreadable JSON) it reads the whole prefix as before. A key the chunks do not
carry costs one GetParameter.

The SSM client is a fake that records every call; time is a fake clock behind the module's
``_monotonic`` and ``_sleep``.
"""

import json

import pytest
from botocore.exceptions import ClientError

PREFIX = "/vams-test/resourceNames"
CHUNKS = f"{PREFIX}Chunks"


def client_error(code, operation="GetParameters"):
    return ClientError({"Error": {"Code": code, "Message": code},
                        "ResponseMetadata": {"HTTPStatusCode": 400}}, operation)


def chunk_value(index, count, names, layout="layout1", version=1):
    return json.dumps({"v": version, "n": count, "i": index, "l": layout, "m": names},
                      separators=(",", ":"))


def chunked(names: dict, count: int, layout="layout1") -> dict:
    """``names`` spread over ``count`` chunks, round robin: chunk index -> parameter value."""
    keys = sorted(names)
    parts = {i: {} for i in range(count)}
    for position, key in enumerate(keys):
        parts[position % count][key] = names[key]
    return {i: chunk_value(i, count, parts[i], layout) for i in range(count)}


class FakeClock:
    def __init__(self, now=1000.0):
        self.now = now

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        assert seconds >= 0
        self.now += seconds


class FakeSsm:
    """Chunk parameters (index -> value), plain parameters (key below the prefix -> value) and a
    record of every call. ``fault(operation, call_number)`` runs before each call and may raise."""

    def __init__(self, chunks=None, parameters=None, fault=None):
        self.chunks = dict(chunks or {})
        self.parameters = dict(parameters or {})
        self.fault = fault
        self.calls = []

    def _enter(self, operation, detail):
        self.calls.append((operation, detail))
        if self.fault is not None:
            self.fault(operation, len(self.calls))

    def get_parameters(self, Names, **_):
        assert len(Names) <= 10, "GetParameters takes at most 10 names"
        self._enter("get_parameters", list(Names))
        found, invalid = [], []
        for name in Names:
            index = name[len(CHUNKS) + 1:]
            if name.startswith(f"{CHUNKS}/") and index.isdigit() and int(index) in self.chunks:
                found.append({"Name": name, "Value": self.chunks[int(index)], "Type": "String"})
            else:
                invalid.append(name)
        return {"Parameters": found, "InvalidParameters": invalid}

    def get_parameter(self, Name, **_):
        self._enter("get_parameter", Name)
        key = Name[len(PREFIX) + 1:]
        if key not in self.parameters:
            raise client_error("ParameterNotFound", "GetParameter")
        return {"Parameter": {"Name": Name, "Value": self.parameters[key], "Type": "String"}}

    def get_parameters_by_path(self, Path, Recursive=False, MaxResults=10, NextToken=None, **_):
        self._enter("get_parameters_by_path", NextToken)
        assert Path == PREFIX
        keys = sorted(self.parameters)
        start = int(NextToken or 0)
        page = {"Parameters": [{"Name": f"{Path}/{k}", "Value": self.parameters[k], "Type": "String"}
                               for k in keys[start:start + MaxResults]]}
        if start + MaxResults < len(keys):
            page["NextToken"] = str(start + MaxResults)
        return page

    def count(self, operation):
        return sum(1 for op, _ in self.calls if op == operation)

    def names_requested(self):
        return [names for op, names in self.calls if op == "get_parameters"]


class RecordingLogger:
    def __init__(self):
        self.lines = []

    def __getattr__(self, level):
        if level in ("debug", "info", "warning", "warn", "error", "critical", "exception"):
            return lambda message, *a, **k: self.lines.append((level, str(message)))
        raise AttributeError(level)

    def at(self, *levels):
        return [message for level, message in self.lines if level in levels]


class CountingLock:
    def __init__(self, lock):
        self._lock = lock
        self.acquired = 0

    def __enter__(self):
        self.acquired += 1
        return self._lock.__enter__()

    def __exit__(self, *exc):
        return self._lock.__exit__(*exc)


def _mirror_keys(rn):
    return [value for value in vars(rn.ResourceKeys).values() if isinstance(value, rn.ResourceParamKey)]


@pytest.fixture
def rn(monkeypatch):
    """The resourceNames module with the prefix set, every override cleared and its state emptied,
    restored by monkeypatch after the test."""
    import common.resourceNames as resourceNames
    monkeypatch.setenv("VAMS_RESOURCE_PARAM_PREFIX", PREFIX)
    for key in _mirror_keys(resourceNames):
        for env_name in key.env_var_names:
            monkeypatch.delenv(env_name, raising=False)
    for name, value in (("_cache", {}), ("_cache_fetched_at", 0.0), ("_ssm_client", None),
                        ("_missing_keys", {}), ("_failed_sweeps", 0), ("_sweep_retry_after", 0.0),
                        ("_window_logged_for", None), ("_cache_complete", True)):
        monkeypatch.setattr(resourceNames, name, value, raising=False)
    clock = FakeClock()
    monkeypatch.setattr(resourceNames, "_monotonic", clock.monotonic, raising=False)
    monkeypatch.setattr(resourceNames, "_sleep", clock.sleep, raising=False)
    monkeypatch.setattr(resourceNames, "_uniform", lambda low, high: high, raising=False)
    log = RecordingLogger()
    monkeypatch.setattr(resourceNames, "logger", log)
    # Handles for the test body, removed again by monkeypatch.
    monkeypatch.setattr(resourceNames, "test_log", log, raising=False)
    monkeypatch.setattr(resourceNames, "test_clock", clock, raising=False)
    return resourceNames


def install(rn, monkeypatch, fake):
    monkeypatch.setattr(rn, "_get_ssm_client", lambda: fake)
    return fake


def published_names(rn, count=None):
    """Every mirrored key with a fake name, or the first ``count`` of them."""
    keys = _mirror_keys(rn)
    if count is not None:
        keys = keys[:count]
    return {key.param_key: f"name-of-{key.param_key.replace('/', '-')}" for key in keys}


ASSET = "dynamoTables/assetStorage"
DATABASE = "dynamoTables/databaseStorage"


@pytest.mark.unit
class TestCallsPerChunkCount:
    @pytest.mark.parametrize("count,expected_calls", [(1, 1), (10, 1), (11, 2), (25, 3)])
    def test_one_getparameters_call_per_ten_chunks(self, rn, monkeypatch, count, expected_calls):
        names = published_names(rn)
        fake = install(rn, monkeypatch, FakeSsm(chunks=chunked(names, count), parameters=names))

        assert rn.get_resource_name(rn.ResourceKeys.ASSET_STORAGE_TABLE) == names[ASSET]

        requested = fake.names_requested()
        assert len(requested) == expected_calls
        assert fake.calls == [("get_parameters", names) for names in requested]
        assert all(len(batch) <= 10 for batch in requested)
        # The first call asks for chunks 0-9; later calls only for chunks below the count.
        assert requested[0] == [f"{CHUNKS}/{i}" for i in range(10)]
        later = [name for batch in requested[1:] for name in batch]
        assert later == [f"{CHUNKS}/{i}" for i in range(10, count)]
        assert rn._cache == names
        assert rn._cache_complete is False

    def test_every_mirrored_key_then_resolves_without_a_call(self, rn, monkeypatch):
        names = published_names(rn)
        fake = install(rn, monkeypatch, FakeSsm(chunks=chunked(names, 7), parameters=names))
        for key in _mirror_keys(rn):
            assert rn.get_resource_name(key) == names[key.param_key]
        assert len(fake.calls) == 1

    def test_a_chunk_numbered_at_or_above_the_count_is_ignored(self, rn, monkeypatch):
        names = published_names(rn, 4)
        chunks = chunked(names, 2)
        # Left over from a larger layout while a shrinking deployment updates.
        chunks[2] = chunk_value(2, 3, {"dynamoTables/leftover": "old"}, layout="old")
        fake = install(rn, monkeypatch, FakeSsm(chunks=chunks, parameters=names))

        rn.get_resource_name(rn.ResourceKeys.ASSET_STORAGE_TABLE)

        assert "dynamoTables/leftover" not in rn._cache
        assert rn._cache == names
        assert fake.count("get_parameters_by_path") == 0

    def test_logs_one_line_with_names_chunks_and_calls(self, rn, monkeypatch):
        names = published_names(rn)
        install(rn, monkeypatch, FakeSsm(chunks=chunked(names, 12), parameters=names))

        rn.get_resource_name(rn.ResourceKeys.ASSET_STORAGE_TABLE)

        info = rn.test_log.at("info")
        assert info == [f"Loaded {len(names)} resource names from 12 SSM chunk parameters in 2 calls, 0.00s"]
        assert not any(value in line for line in info for value in names.values())


@pytest.mark.unit
class TestFallbackToTheSweep:
    def _assert_swept(self, rn, fake, names, reason):
        assert fake.count("get_parameters") == 1
        assert fake.count("get_parameters_by_path") >= 1
        assert rn._cache == names
        assert rn._cache_complete is True
        warnings = rn.test_log.at("warning")
        assert len(warnings) == 1
        assert reason in warnings[0]
        assert "reading every parameter under the prefix" in warnings[0]

    def test_no_chunk_0_reads_the_prefix(self, rn, monkeypatch):
        names = published_names(rn)
        fake = install(rn, monkeypatch, FakeSsm(chunks={}, parameters=names))
        assert rn.get_resource_name(rn.ResourceKeys.ASSET_STORAGE_TABLE) == names[ASSET]
        self._assert_swept(rn, fake, names, "chunk 0 is not published")

    def test_a_missing_chunk_reads_the_prefix(self, rn, monkeypatch):
        names = published_names(rn)
        chunks = chunked(names, 4)
        del chunks[2]
        fake = install(rn, monkeypatch, FakeSsm(chunks=chunks, parameters=names))
        assert rn.get_resource_name(rn.ResourceKeys.ASSET_STORAGE_TABLE) == names[ASSET]
        self._assert_swept(rn, fake, names, "chunk 2 of 4 is not published")

    def test_a_missing_chunk_in_a_later_batch_reads_the_prefix(self, rn, monkeypatch):
        names = published_names(rn)
        chunks = chunked(names, 12)
        del chunks[11]
        fake = install(rn, monkeypatch, FakeSsm(chunks=chunks, parameters=names))
        rn.get_resource_name(rn.ResourceKeys.ASSET_STORAGE_TABLE)
        assert fake.count("get_parameters") == 2
        assert fake.count("get_parameters_by_path") >= 1
        assert rn._cache_complete is True

    @pytest.mark.parametrize("field,value", [("l", "other-layout"), ("n", 5)])
    def test_a_chunk_of_another_layout_reads_the_prefix(self, rn, monkeypatch, field, value):
        names = published_names(rn)
        chunks = chunked(names, 3)
        other = json.loads(chunks[1])
        other[field] = value
        chunks[1] = json.dumps(other)
        fake = install(rn, monkeypatch, FakeSsm(chunks=chunks, parameters=names))
        assert rn.get_resource_name(rn.ResourceKeys.ASSET_STORAGE_TABLE) == names[ASSET]
        self._assert_swept(rn, fake, names, "chunk 1 belongs to another layout")

    @pytest.mark.parametrize("index", [0, 1])
    def test_unreadable_json_reads_the_prefix(self, rn, monkeypatch, index):
        names = published_names(rn)
        chunks = chunked(names, 2)
        chunks[index] = chunks[index][:-3]
        fake = install(rn, monkeypatch, FakeSsm(chunks=chunks, parameters=names))
        assert rn.get_resource_name(rn.ResourceKeys.ASSET_STORAGE_TABLE) == names[ASSET]
        self._assert_swept(rn, fake, names, f"chunk {index} is not valid JSON")

    @pytest.mark.parametrize("header,reason", [
        ({"v": 2}, "format version 2"),
        ({"n": 0}, "chunk count 0"),
        ({"n": 101}, "chunk count 101"),
        ({"i": 4}, "carries index 4"),
        ({"m": ["not", "a", "map"]}, "no name map"),
    ])
    def test_a_chunk_0_this_module_cannot_read_reads_the_prefix(self, rn, monkeypatch, header, reason):
        names = published_names(rn)
        chunk = json.loads(chunk_value(0, 1, names))
        chunk.update(header)
        fake = install(rn, monkeypatch, FakeSsm(chunks={0: json.dumps(chunk)}, parameters=names))
        assert rn.get_resource_name(rn.ResourceKeys.ASSET_STORAGE_TABLE) == names[ASSET]
        self._assert_swept(rn, fake, names, reason)

    def test_access_denied_on_the_chunks_reads_the_prefix(self, rn, monkeypatch):
        names = published_names(rn)

        def deny_chunks(operation, call):
            if operation == "get_parameters":
                raise client_error("AccessDeniedException")

        fake = install(rn, monkeypatch, FakeSsm(chunks=chunked(names, 2), parameters=names, fault=deny_chunks))
        assert rn.get_resource_name(rn.ResourceKeys.ASSET_STORAGE_TABLE) == names[ASSET]
        self._assert_swept(rn, fake, names, "GetParameters failed (AccessDeniedException)")

    def test_after_a_sweep_an_unpublished_key_is_a_miss_without_a_getparameter(self, rn, monkeypatch):
        names = published_names(rn)
        del names[DATABASE]
        fake = install(rn, monkeypatch, FakeSsm(chunks={}, parameters=names))
        with pytest.raises(KeyError):
            rn.get_resource_name(rn.ResourceKeys.DATABASE_STORAGE_TABLE)
        calls = len(fake.calls)
        with pytest.raises(KeyError):
            rn.get_resource_name(rn.ResourceKeys.DATABASE_STORAGE_TABLE)
        assert len(fake.calls) == calls
        assert fake.count("get_parameter") == 0


@pytest.mark.unit
class TestKeyTheChunksDoNotCarry:
    def test_costs_one_getparameter_and_is_cached(self, rn, monkeypatch):
        names = published_names(rn)
        chunk_names = {k: v for k, v in names.items() if k != DATABASE}
        fake = install(rn, monkeypatch, FakeSsm(chunks=chunked(chunk_names, 3), parameters=names))

        assert rn.get_resource_name(rn.ResourceKeys.DATABASE_STORAGE_TABLE) == names[DATABASE]
        assert [op for op, _ in fake.calls] == ["get_parameters", "get_parameter"]
        assert fake.calls[1] == ("get_parameter", f"{PREFIX}/{DATABASE}")

        assert rn.get_resource_name(rn.ResourceKeys.DATABASE_STORAGE_TABLE) == names[DATABASE]
        assert rn.get_resource_name(rn.ResourceKeys.ASSET_STORAGE_TABLE) == names[ASSET]
        assert len(fake.calls) == 2

    def test_on_a_current_chunk_cache_costs_one_getparameter_and_no_refresh(self, rn, monkeypatch):
        names = published_names(rn)
        chunk_names = {k: v for k, v in names.items() if k != DATABASE}
        fake = install(rn, monkeypatch, FakeSsm(chunks=chunked(chunk_names, 3), parameters=names))
        rn.get_resource_name(rn.ResourceKeys.ASSET_STORAGE_TABLE)
        assert len(fake.calls) == 1

        assert rn.get_resource_name(rn.ResourceKeys.DATABASE_STORAGE_TABLE) == names[DATABASE]
        assert [op for op, _ in fake.calls] == ["get_parameters", "get_parameter"]

    def test_parameter_not_found_goes_into_the_negative_record(self, rn, monkeypatch):
        names = published_names(rn)
        del names[DATABASE]
        fake = install(rn, monkeypatch, FakeSsm(chunks=chunked(names, 2), parameters=names))

        with pytest.raises(KeyError, match="Resource name parameter not found in SSM: dynamoTables/databaseStorage"):
            rn.get_resource_name(rn.ResourceKeys.DATABASE_STORAGE_TABLE)
        assert DATABASE in rn._missing_keys
        calls = len(fake.calls)
        assert fake.count("get_parameter") == 1

        with pytest.raises(KeyError):
            rn.get_resource_name(rn.ResourceKeys.DATABASE_STORAGE_TABLE)
        assert len(fake.calls) == calls

    def test_a_throttled_getparameter_opens_the_back_off_window(self, rn, monkeypatch):
        names = published_names(rn)
        chunk_names = {k: v for k, v in names.items() if k != DATABASE}

        def throttle_single(operation, call):
            if operation == "get_parameter":
                raise client_error("ThrottlingException", "GetParameter")

        fake = install(rn, monkeypatch, FakeSsm(chunks=chunked(chunk_names, 2), parameters=names,
                                                fault=throttle_single))
        with pytest.raises(rn.ResourceNameUnavailableError):
            rn.get_resource_name(rn.ResourceKeys.DATABASE_STORAGE_TABLE)
        assert rn._failed_sweeps == 1
        assert DATABASE not in rn._missing_keys
        calls = len(fake.calls)

        with pytest.raises(rn.ResourceNameUnavailableError):
            rn.get_resource_name(rn.ResourceKeys.DATABASE_STORAGE_TABLE)
        assert len(fake.calls) == calls
        # Names the chunks carried are still served inside the window.
        assert rn.get_resource_name(rn.ResourceKeys.ASSET_STORAGE_TABLE) == names[ASSET]


@pytest.mark.unit
class TestBackoffAndOverrides:
    def test_a_throttled_chunk_read_is_a_failed_sweep_and_does_not_fall_back(self, rn, monkeypatch):
        names = published_names(rn)

        def throttle_chunks(operation, call):
            if operation == "get_parameters":
                raise client_error("ThrottlingException")

        fake = install(rn, monkeypatch, FakeSsm(chunks=chunked(names, 2), parameters=names, fault=throttle_chunks))

        with pytest.raises(rn.ResourceNameUnavailableError):
            rn.get_resource_name(rn.ResourceKeys.ASSET_STORAGE_TABLE)
        assert fake.count("get_parameters_by_path") == 0
        assert rn._failed_sweeps == 1
        assert rn._sweep_retry_after > rn.test_clock.now
        # Retried inside the budget, never past it.
        assert rn.test_clock.now - 1000.0 <= rn.SWEEP_BUDGET_SECONDS
        assert fake.count("get_parameters") > 1
        calls = len(fake.calls)

        with pytest.raises(rn.ResourceNameUnavailableError):
            rn.get_resource_name(rn.ResourceKeys.DATABASE_STORAGE_TABLE)
        assert len(fake.calls) == calls

    def test_a_chunk_read_throttled_once_is_retried_inside_the_budget(self, rn, monkeypatch):
        names = published_names(rn)

        def throttle_first(operation, call):
            if call == 1:
                raise client_error("ThrottlingException")

        fake = install(rn, monkeypatch, FakeSsm(chunks=chunked(names, 2), parameters=names, fault=throttle_first))
        assert rn.get_resource_name(rn.ResourceKeys.ASSET_STORAGE_TABLE) == names[ASSET]
        assert fake.count("get_parameters") == 2
        assert fake.count("get_parameters_by_path") == 0
        assert rn._failed_sweeps == 0

    def test_the_env_override_wins(self, rn, monkeypatch):
        names = published_names(rn)
        fake = install(rn, monkeypatch, FakeSsm(chunks=chunked(names, 2), parameters=names))
        monkeypatch.setenv("ASSET_STORAGE_TABLE_NAME", "from-env")
        assert rn.get_resource_name(rn.ResourceKeys.ASSET_STORAGE_TABLE) == "from-env"
        assert fake.calls == []
        rn.get_resource_name(rn.ResourceKeys.DATABASE_STORAGE_TABLE)
        assert rn.get_resource_name(rn.ResourceKeys.ASSET_STORAGE_TABLE) == "from-env"

    def test_the_warm_path_makes_no_call_and_takes_no_lock(self, rn, monkeypatch):
        names = published_names(rn)
        fake = install(rn, monkeypatch, FakeSsm(chunks=chunked(names, 3), parameters=names))
        rn.get_resource_name(rn.ResourceKeys.ASSET_STORAGE_TABLE)
        calls = len(fake.calls)
        lock = CountingLock(rn._sweep_lock)
        monkeypatch.setattr(rn, "_sweep_lock", lock)

        for _ in range(1000):
            for key in (rn.ResourceKeys.ASSET_STORAGE_TABLE, rn.ResourceKeys.DATABASE_STORAGE_TABLE):
                assert rn.get_resource_name(key) == names[key.param_key]

        assert len(fake.calls) == calls
        assert lock.acquired == 0

    def test_the_cache_ttl_reads_the_chunks_again(self, rn, monkeypatch):
        names = published_names(rn)
        fake = install(rn, monkeypatch, FakeSsm(chunks=chunked(names, 3), parameters=names))
        rn.get_resource_name(rn.ResourceKeys.ASSET_STORAGE_TABLE)
        monkeypatch.setattr(rn, "_cache_fetched_at", rn._cache_fetched_at - rn.CACHE_TTL_SECONDS - 1)
        rn.get_resource_name(rn.ResourceKeys.ASSET_STORAGE_TABLE)
        assert fake.count("get_parameters") == 2
        assert fake.count("get_parameters_by_path") == 0


COMMENT = "dynamoTables/commentStorage"


@pytest.mark.unit
class TestFailureClassesOnTheChunkPath:
    """Which error class a failed lookup raises, and whether it opens the back-off window, for the
    chunk read, the sweep it falls back to and the single-key read."""

    @pytest.mark.parametrize("code,needle", [
        ("AccessDeniedException", f"ssm:GetParameter on <prefix>/{DATABASE}"),
        ("ValidationException", "a retry cannot fix it"),
    ])
    def test_a_refused_getparameter_opens_no_window(self, rn, monkeypatch, code, needle):
        names = published_names(rn)
        chunk_names = {k: v for k, v in names.items() if k != DATABASE}

        def refuse_single(operation, call):
            if operation == "get_parameter":
                raise client_error(code, "GetParameter")

        fake = install(rn, monkeypatch, FakeSsm(chunks=chunked(chunk_names, 2), parameters=names,
                                                fault=refuse_single))
        with pytest.raises(rn.ResourceNameAccessError) as raised:
            rn.get_resource_name(rn.ResourceKeys.DATABASE_STORAGE_TABLE)
        assert needle in str(raised.value)
        assert "GetParametersByPath" not in str(raised.value)
        assert isinstance(raised.value.__cause__, ClientError)
        assert rn._failed_sweeps == 0
        assert rn._sweep_retry_after == 0.0
        # Refused at once, not retried inside the budget.
        assert fake.count("get_parameter") == 1
        assert rn.test_clock.now == 1000.0
        assert f"Resource name read failed ({code}) after 1 calls: {DATABASE}" in rn.test_log.at("error")
        assert DATABASE not in rn._missing_keys

        # No window: the next lookup reads again and meets the same refusal.
        with pytest.raises(rn.ResourceNameAccessError):
            rn.get_resource_name(rn.ResourceKeys.DATABASE_STORAGE_TABLE)
        assert fake.count("get_parameter") == 2
        assert rn.get_resource_name(rn.ResourceKeys.ASSET_STORAGE_TABLE) == names[ASSET]

    def test_a_getparameter_after_a_late_refresh_has_only_the_rest_of_its_budget(self, rn, monkeypatch):
        names = published_names(rn)
        chunk_names = {k: v for k, v in names.items() if k != DATABASE}
        clock = rn.test_clock

        def late_chunks_then_throttled_single(operation, call):
            # The chunk read is throttled until 3.4 s into the lookup and then succeeds.
            if operation == "get_parameters" and clock.now < 1003.4:
                raise client_error("ThrottlingException")
            if operation == "get_parameter":
                raise client_error("ThrottlingException", "GetParameter")

        fake = install(rn, monkeypatch, FakeSsm(chunks=chunked(chunk_names, 2), parameters=names,
                                                fault=late_chunks_then_throttled_single))
        with pytest.raises(rn.ResourceNameUnavailableError):
            rn.get_resource_name(rn.ResourceKeys.DATABASE_STORAGE_TABLE)

        assert fake.count("get_parameters") > 1
        assert fake.count("get_parameter") >= 1
        assert rn._cache == chunk_names
        # One lookup, one budget: the refresh and the single-key read together stay inside it.
        assert clock.now - 1000.0 <= rn.SWEEP_BUDGET_SECONDS
        assert rn._failed_sweeps == 1

    @pytest.mark.parametrize("single_throttled", [False, True])
    def test_a_refresh_that_ends_past_its_budget_still_leaves_one_getparameter(
            self, rn, monkeypatch, single_throttled):
        names = published_names(rn)
        chunk_names = {k: v for k, v in names.items() if k != DATABASE}
        clock = rn.test_clock

        def slow_last_chunk_call(operation, call):
            # The chunk read is throttled until 3.4 s; its successful call then takes 0.6 s and
            # ends at 4.1 s, past the 4 s budget.
            if operation == "get_parameters":
                if clock.now < 1003.4:
                    raise client_error("ThrottlingException")
                clock.now += 0.6
            if operation == "get_parameter" and single_throttled:
                raise client_error("ThrottlingException", "GetParameter")

        fake = install(rn, monkeypatch, FakeSsm(chunks=chunked(chunk_names, 2), parameters=names,
                                                fault=slow_last_chunk_call))
        if single_throttled:
            with pytest.raises(rn.ResourceNameUnavailableError):
                rn.get_resource_name(rn.ResourceKeys.DATABASE_STORAGE_TABLE)
            assert rn._failed_sweeps == 1
        else:
            assert rn.get_resource_name(rn.ResourceKeys.DATABASE_STORAGE_TABLE) == names[DATABASE]
            assert rn._failed_sweeps == 0

        assert clock.now - 1000.0 > rn.SWEEP_BUDGET_SECONDS
        # One call even though the budget had run out, and no retry or sleep after it.
        assert fake.count("get_parameter") == 1
        assert fake.calls[-1] == ("get_parameter", f"{PREFIX}/{DATABASE}")
        assert clock.now == pytest.approx(1004.1)

    def test_a_successful_getparameter_resets_the_failure_count(self, rn, monkeypatch):
        names = published_names(rn)
        chunk_names = {k: v for k, v in names.items() if k not in (DATABASE, COMMENT)}
        throttled = {"single": True}

        def throttle_single(operation, call):
            if operation == "get_parameter" and throttled["single"]:
                raise client_error("ThrottlingException", "GetParameter")

        install(rn, monkeypatch, FakeSsm(chunks=chunked(chunk_names, 2), parameters=names, fault=throttle_single))
        with pytest.raises(rn.ResourceNameUnavailableError):
            rn.get_resource_name(rn.ResourceKeys.DATABASE_STORAGE_TABLE)
        assert rn._failed_sweeps == 1

        # Past the window, on the same chunk-loaded cache, the read succeeds.
        rn.test_clock.now = rn._sweep_retry_after + 0.1
        throttled["single"] = False
        assert rn.get_resource_name(rn.ResourceKeys.DATABASE_STORAGE_TABLE) == names[DATABASE]
        assert rn._failed_sweeps == 0
        assert rn._sweep_retry_after == 0.0

        # The next failure opens the first window again, not a doubled one.
        throttled["single"] = True
        with pytest.raises(rn.ResourceNameUnavailableError):
            rn.get_resource_name(rn.ResourceKeys.COMMENT_STORAGE_TABLE)
        assert rn._failed_sweeps == 1
        assert rn._sweep_retry_after - rn.test_clock.now == pytest.approx(rn.SWEEP_BACKOFF_BASE_SECONDS)

    @pytest.mark.parametrize("chunk_code,sweep_code,error,failed_sweeps", [
        ("AccessDeniedException", "AccessDeniedException", "ResourceNameAccessError", 0),
        ("AccessDeniedException", "ThrottlingException", "ResourceNameUnavailableError", 1),
        ("ThrottlingException", None, "ResourceNameUnavailableError", 1),
    ])
    def test_a_refresh_is_classed_by_the_read_that_ended_it(
            self, rn, monkeypatch, chunk_code, sweep_code, error, failed_sweeps):
        names = published_names(rn)

        def fault(operation, call):
            if operation == "get_parameters":
                raise client_error(chunk_code)
            if operation == "get_parameters_by_path" and sweep_code:
                raise client_error(sweep_code, "GetParametersByPath")

        fake = install(rn, monkeypatch, FakeSsm(chunks=chunked(names, 2), parameters=names, fault=fault))
        with pytest.raises(getattr(rn, error)) as raised:
            rn.get_resource_name(rn.ResourceKeys.ASSET_STORAGE_TABLE)

        assert rn._failed_sweeps == failed_sweeps
        assert (rn._sweep_retry_after > rn.test_clock.now) == (failed_sweeps > 0)
        assert rn.test_clock.now - 1000.0 <= rn.SWEEP_BUDGET_SECONDS
        assert fake.count("get_parameter") == 0
        warnings = rn.test_log.at("warning")
        if chunk_code == "AccessDeniedException":
            # A denied chunk read falls back to the sweep, which shares its budget.
            assert fake.count("get_parameters") == 1
            assert fake.count("get_parameters_by_path") >= 1
            assert "chunk parameters not used" in warnings[0]
            assert "GetParameters failed (AccessDeniedException)" in warnings[0]
            # The denied chunk read logs no error; the sweep logs one line, with no page read.
            errors = rn.test_log.at("error")
            assert len(errors) == 1
            assert f"({sweep_code}) after 0 pages" in errors[0]
        else:
            # A throttled chunk read is a failed refresh and reads no further.
            assert fake.count("get_parameters") > 1
            assert fake.count("get_parameters_by_path") == 0
            assert not any("chunk parameters not used" in line for line in warnings)
        if error == "ResourceNameAccessError":
            assert fake.calls == [("get_parameters", [f"{CHUNKS}/{i}" for i in range(10)]),
                                  ("get_parameters_by_path", None)]
            assert "ssm:GetParametersByPath" in str(raised.value)
