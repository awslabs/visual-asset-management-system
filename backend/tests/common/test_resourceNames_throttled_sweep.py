# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The resource-name sweep under Parameter Store throttling and outages.

Parameter Store's default throughput is shared by every application in an account and Region, so
many Lambda containers starting together can throttle the GetParametersByPath sweep each of them
runs. ``common/resourceNames.py`` bounds what that costs a container:

* a failed sweep opens a back-off window in which no sweep starts, so the next lookups (the nine
  audit log groups at import, the handler's own names, the authorizer's tables) do not each sweep
  again;
* one sweep runs at a time per container, and a thread that waited re-checks the cache first;
* a sweep is bounded by ``SWEEP_BUDGET_SECONDS``: its page loop retries throttling, server faults
  and timeouts from the page's own NextToken, and its client has short timeouts and no botocore
  retries;
* a refused lookup raises ``ResourceNameUnavailableError``, never a KeyError, and a stale cached
  name is still served.

The SSM client is a fake that serves pages with string-offset NextTokens. It also answers
``get_paginator``, so these tests run unchanged against a resolver that pages with the boto3
paginator. Time is a fake clock driven through the module's ``_monotonic`` and ``_sleep``.
"""

import threading

import pytest
from botocore.exceptions import ClientError, ReadTimeoutError

PREFIX = "/vams-test/resourceNames"


class FakeClock:
    """Monotonic time that moves only when the code sleeps or a call is charged time."""

    def __init__(self, now=1000.0):
        self.now = now
        self.sleeps = []

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        assert seconds >= 0
        self.sleeps.append((self.now, seconds))
        self.now += seconds

    def advance(self, seconds):
        self.now += seconds


class FakeSsm:
    """Serves ``parameters`` (key below the prefix -> value) in pages of MaxResults, sorted by key.

    ``fault(call_index, next_token)`` runs before every call and may raise; ``call_seconds`` is
    charged to ``clock`` per call, before the fault, as a call that times out takes its time first.
    ``tokens`` records the NextToken of every call (None for a first page).
    """

    def __init__(self, parameters=None, fault=None, clock=None, call_seconds=0.0):
        self.parameters = dict(parameters or {})
        self.fault = fault
        self.clock = clock
        self.call_seconds = call_seconds
        self.tokens = []

    def get_parameters_by_path(self, Path, Recursive=False, MaxResults=10, NextToken=None, **_):
        index = len(self.tokens)
        self.tokens.append(NextToken)
        if self.clock is not None and self.call_seconds:
            self.clock.advance(self.call_seconds)
        if self.fault is not None:
            self.fault(index, NextToken)
        names = sorted(self.parameters)
        start = int(NextToken or 0)
        page = {"Parameters": [{"Name": f"{Path}/{name}", "Value": self.parameters[name], "Type": "String"}
                               for name in names[start:start + MaxResults]]}
        if start + MaxResults < len(names):
            page["NextToken"] = str(start + MaxResults)
        return page

    def get_paginator(self, operation):
        assert operation == "get_parameters_by_path"
        fake = self

        class _Paginator:
            def paginate(self, **kwargs):
                token = None
                while True:
                    request = dict(kwargs)
                    if token:
                        request["NextToken"] = token
                    page = fake.get_parameters_by_path(**request)
                    yield page
                    token = page.get("NextToken")
                    if not token:
                        return

        return _Paginator()


class RecordingLogger:
    """Records (level, message) for every call, at every level safeLogger exposes."""

    def __init__(self):
        self.lines = []

    def _record(self, level):
        def write(message, *args, **kwargs):
            self.lines.append((level, str(message)))
        return write

    def __getattr__(self, level):
        if level in ("debug", "info", "warning", "warn", "error", "critical", "exception"):
            return self._record(level)
        raise AttributeError(level)

    def at(self, *levels):
        return [message for level, message in self.lines if level in levels]


class CountingLock:
    """Wraps a lock and counts every acquisition."""

    def __init__(self, lock):
        self._lock = lock
        self.acquired = 0

    def __enter__(self):
        self.acquired += 1
        return self._lock.__enter__()

    def __exit__(self, *exc):
        return self._lock.__exit__(*exc)

    def acquire(self, *args, **kwargs):
        self.acquired += 1
        return self._lock.acquire(*args, **kwargs)

    def release(self):
        return self._lock.release()


def throttled():
    return ClientError({"Error": {"Code": "ThrottlingException", "Message": "Rate exceeded"},
                        "ResponseMetadata": {"HTTPStatusCode": 400}}, "GetParametersByPath")


def access_denied():
    return ClientError({"Error": {"Code": "AccessDeniedException", "Message": "not authorized"},
                        "ResponseMetadata": {"HTTPStatusCode": 400}}, "GetParametersByPath")


def always(make_error):
    def fault(index, token):
        raise make_error()
    return fault


def _mirror_keys(rn):
    return [value for value in vars(rn.ResourceKeys).values() if isinstance(value, rn.ResourceParamKey)]


@pytest.fixture
def rn(monkeypatch):
    """The resourceNames module with the prefix set, every break-glass override cleared and its
    state emptied, so each lookup below reaches the SSM path."""
    import common.resourceNames as resourceNames
    monkeypatch.setenv("VAMS_RESOURCE_PARAM_PREFIX", PREFIX)
    for key in _mirror_keys(resourceNames):
        for env_name in key.env_var_names:
            monkeypatch.delenv(env_name, raising=False)
    resourceNames._cache = {}
    resourceNames._cache_fetched_at = 0.0
    resourceNames._ssm_client = None
    resourceNames._missing_keys = {}
    resourceNames._failed_sweeps = 0
    resourceNames._sweep_retry_after = 0.0
    return resourceNames


@pytest.fixture
def clock(rn, monkeypatch):
    """A fake clock behind the module's sweep and window, with the jitter at its upper bound."""
    fake = FakeClock()
    monkeypatch.setattr(rn, "_monotonic", fake.monotonic, raising=False)
    monkeypatch.setattr(rn, "_sleep", fake.sleep, raising=False)
    monkeypatch.setattr(rn, "_uniform", lambda low, high: high, raising=False)
    return fake


def install(rn, monkeypatch, fake):
    monkeypatch.setattr(rn, "_get_ssm_client", lambda: fake)
    return fake


def count_sweeps(rn, monkeypatch):
    """Wrap _refresh_cache so the sweeps a test starts can be counted."""
    real_refresh = rn._refresh_cache
    sweeps = []

    def counted():
        sweeps.append(1)
        real_refresh()

    monkeypatch.setattr(rn, "_refresh_cache", counted)
    return sweeps


def fail_one_sweep(rn, key=None):
    """Run one lookup against the installed failing fake, so the back-off window is open."""
    with pytest.raises(Exception):
        rn.get_resource_name(key or rn.ResourceKeys.ASSET_STORAGE_TABLE)


def window_left(rn, clock):
    return rn._sweep_retry_after - clock.now


@pytest.mark.unit
class TestOneSweepPerBackoffWindow:
    """R1: a throttled cold start sweeps once, not once per name it needs."""

    def test_the_audit_and_table_lookups_of_a_throttled_cold_start_start_one_sweep(self, rn, clock, monkeypatch):
        fake = install(rn, monkeypatch, FakeSsm(fault=always(throttled)))
        sweeps = count_sweeps(rn, monkeypatch)
        audit_keys = [key for key in _mirror_keys(rn) if key.param_key.startswith("cloudwatchLogGroups/")]
        assert len(audit_keys) == 9
        lookups = audit_keys + [rn.ResourceKeys.USER_ROLES_STORAGE_TABLE, rn.ResourceKeys.API_KEY_STORAGE_TABLE]

        for key in lookups:
            with pytest.raises(Exception):
                rn.get_resource_name(key)

        assert len(sweeps) == 1, f"{len(sweeps)} sweeps for {len(lookups)} lookups inside one back-off window"
        assert fake.tokens, "the fake was never called, so the count above proves nothing"


@pytest.mark.unit
class TestOneSweepAtATime:
    """R1: concurrent lookups in one container share the sweep in flight."""

    def test_two_threads_on_a_cold_cache_share_one_sweep(self, rn, monkeypatch):
        entered, release = threading.Event(), threading.Event()

        def hold_first_call(index, token):
            if index == 0:
                entered.set()
                assert release.wait(5), "the test never released the first call"

        install(rn, monkeypatch, FakeSsm(
            parameters={"dynamoTables/assetStorage": "asset-table", "dynamoTables/databaseStorage": "database-table"},
            fault=hold_first_call))
        sweeps = count_sweeps(rn, monkeypatch)
        results = {}

        def look_up(name, key):
            try:
                results[name] = rn.get_resource_name(key)
            except Exception as e:  # recorded for the assertion below
                results[name] = e

        first = threading.Thread(target=look_up, args=("first", rn.ResourceKeys.ASSET_STORAGE_TABLE))
        first.start()
        assert entered.wait(5), "the first lookup never reached SSM"
        second = threading.Thread(target=look_up, args=("second", rn.ResourceKeys.DATABASE_STORAGE_TABLE))
        second.start()
        # Long enough for the second lookup to run a sweep of its own if nothing stops it.
        second.join(0.3)
        release.set()
        first.join(5)
        second.join(5)

        assert not first.is_alive() and not second.is_alive()
        assert results == {"first": "asset-table", "second": "database-table"}
        assert len(sweeps) == 1


@pytest.mark.unit
class TestSweepBudget:
    """R2: a sweep returns or fails inside SWEEP_BUDGET_SECONDS plus at most one call."""

    def test_constant_throttling_gives_up_inside_the_budget(self, rn, clock, monkeypatch):
        fake = install(rn, monkeypatch, FakeSsm(fault=always(throttled)))
        started = clock.now

        with pytest.raises(rn.ResourceNameUnavailableError) as refused:
            rn.get_resource_name(rn.ResourceKeys.ASSET_STORAGE_TABLE)

        assert isinstance(refused.value.__cause__, ClientError)
        assert len(fake.tokens) > 1, "the throttled page was never retried"
        assert clock.now - started <= rn.SWEEP_BUDGET_SECONDS
        deadline = started + rn.SWEEP_BUDGET_SECONDS
        assert clock.sleeps, "no retry sleep ran"
        for slept_at, seconds in clock.sleeps:
            assert seconds <= rn.SWEEP_RETRY_MAX_SLEEP_SECONDS
            assert slept_at + seconds < deadline, "a retry sleep ran past the sweep budget"

    def test_an_endpoint_that_never_answers_costs_at_most_the_budget_plus_one_timeout(self, rn, clock, monkeypatch):
        read_timeout = rn.sweep_client_config.read_timeout
        fake = install(rn, monkeypatch, FakeSsm(
            fault=always(lambda: ReadTimeoutError(endpoint_url="https://ssm.example.invalid/")),
            clock=clock, call_seconds=read_timeout))
        started = clock.now

        with pytest.raises(rn.ResourceNameUnavailableError) as refused:
            rn.get_resource_name(rn.ResourceKeys.ASSET_STORAGE_TABLE)

        assert isinstance(refused.value.__cause__, ReadTimeoutError)
        assert len(fake.tokens) >= 2, "a read timeout was not retried"
        assert clock.now - started <= rn.SWEEP_BUDGET_SECONDS + read_timeout

    def test_a_non_retryable_error_ends_the_sweep_at_once(self, rn, clock, monkeypatch):
        fake = install(rn, monkeypatch, FakeSsm(fault=always(access_denied)))

        with pytest.raises(rn.ResourceNameUnavailableError) as refused:
            rn.get_resource_name(rn.ResourceKeys.ASSET_STORAGE_TABLE)

        assert isinstance(refused.value.__cause__, ClientError)
        assert fake.tokens == [None]
        assert clock.sleeps == []


@pytest.mark.unit
class TestSweepClient:
    """R2: the client gives up a call quickly and leaves the retries to the page loop."""

    def test_the_sweep_client_has_short_timeouts_and_one_attempt(self, rn, monkeypatch):
        monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
        config = rn._get_ssm_client().meta.config
        assert config.connect_timeout == 1
        assert config.read_timeout == 2
        assert config.retries.get("total_max_attempts") == 1


@pytest.mark.unit
class TestProgressInsideTheBudget:
    """R3: a throttled page is retried from its own NextToken, and the pages already read are kept."""

    PARAMETERS = dict({f"dynamoTables/t{i:02d}": f"v{i}" for i in range(25)},
                      **{"dynamoTables/assetStorage": "asset-table"})

    @pytest.mark.parametrize("throttled_calls, expected_tokens", [
        ((0, 1, 2), [None, None, None, None, "10", "20"]),
        ((1, 2, 3), [None, "10", "10", "10", "10", "20"]),
    ])
    def test_a_short_throttle_completes_the_sweep(self, rn, clock, monkeypatch, throttled_calls, expected_tokens):
        def fault(index, token):
            if index in throttled_calls:
                raise throttled()

        fake = install(rn, monkeypatch, FakeSsm(parameters=self.PARAMETERS, fault=fault))

        assert rn.get_resource_name(rn.ResourceKeys.ASSET_STORAGE_TABLE) == "asset-table"
        assert rn._cache == self.PARAMETERS
        assert fake.tokens == expected_tokens
        assert rn._failed_sweeps == 0


@pytest.mark.unit
class TestBackoffWindow:
    """R1, R5, R6: inside the window nothing reads SSM; a refusal is not a KeyError."""

    def test_lookups_inside_the_window_make_no_call_and_are_not_key_errors(self, rn, clock, monkeypatch):
        fake = install(rn, monkeypatch, FakeSsm(fault=always(throttled)))
        fail_one_sweep(rn)
        fake.tokens.clear()

        refusals = []
        for key in (rn.ResourceKeys.ASSET_STORAGE_TABLE, rn.ResourceKeys.DATABASE_STORAGE_TABLE,
                    rn.ResourceKeys.AUDIT_LOG_ACTIONS):
            with pytest.raises(Exception) as refused:
                rn.get_resource_name(key)
            refusals.append(refused.value)

        assert fake.tokens == [], "a lookup inside the back-off window read SSM"
        assert all(isinstance(error, rn.ResourceNameUnavailableError) for error in refusals)
        assert not any(isinstance(error, KeyError) for error in refusals)
        assert not rn._missing_keys, "a refused lookup was recorded as a missing parameter"

    def test_the_failed_sweep_itself_raises_the_dedicated_error_with_its_cause(self, rn, clock, monkeypatch):
        install(rn, monkeypatch, FakeSsm(fault=always(throttled)))
        with pytest.raises(rn.ResourceNameUnavailableError) as refused:
            rn.get_resource_name(rn.ResourceKeys.ASSET_STORAGE_TABLE)
        assert not isinstance(refused.value, KeyError)
        assert isinstance(refused.value.__cause__, ClientError)

    def test_a_stale_name_is_served_inside_the_window(self, rn, clock, monkeypatch):
        fake = install(rn, monkeypatch, FakeSsm(fault=always(throttled)))
        rn._cache = {"dynamoTables/assetStorage": "stale-asset-table"}
        rn._cache_fetched_at = 0.0

        assert rn.get_resource_name(rn.ResourceKeys.ASSET_STORAGE_TABLE) == "stale-asset-table"
        fake.tokens.clear()
        assert rn.get_resource_name(rn.ResourceKeys.ASSET_STORAGE_TABLE) == "stale-asset-table"
        assert fake.tokens == []

    def test_the_env_override_wins_inside_the_window(self, rn, clock, monkeypatch):
        fake = install(rn, monkeypatch, FakeSsm(fault=always(throttled)))
        fail_one_sweep(rn)
        fake.tokens.clear()
        monkeypatch.setenv("ASSET_STORAGE_TABLE_NAME", "break-glass")

        assert rn.get_resource_name(rn.ResourceKeys.ASSET_STORAGE_TABLE) == "break-glass"
        assert fake.tokens == []

    def test_one_sweep_runs_once_the_window_has_passed(self, rn, clock, monkeypatch):
        fake = install(rn, monkeypatch, FakeSsm(parameters={"dynamoTables/assetStorage": "asset-table"},
                                                fault=always(throttled)))
        fail_one_sweep(rn)
        sweeps = count_sweeps(rn, monkeypatch)
        fake.fault = None
        clock.advance(window_left(rn, clock) + 0.01)

        assert rn.get_resource_name(rn.ResourceKeys.ASSET_STORAGE_TABLE) == "asset-table"
        assert rn.get_resource_name(rn.ResourceKeys.ASSET_STORAGE_TABLE) == "asset-table"
        assert len(sweeps) == 1

    # The window's jitter is uniform(0.5, 1.0); a retry sleep's starts at 0 and stays at its upper
    # bound here, so the fake clock still reaches each sweep's budget.
    @pytest.mark.parametrize("jitter, expected", [
        (lambda low, high: high, [2.0, 4.0, 8.0, 16.0, 30.0, 30.0]),
        (lambda low, high: low if low > 0 else high, [1.0, 2.0, 4.0, 8.0, 15.0, 15.0]),
    ])
    def test_consecutive_failures_grow_the_window_to_its_cap(self, rn, clock, monkeypatch, jitter, expected):
        monkeypatch.setattr(rn, "_uniform", jitter, raising=False)
        install(rn, monkeypatch, FakeSsm(fault=always(throttled)))
        windows = []
        for _ in expected:
            fail_one_sweep(rn)
            windows.append(round(window_left(rn, clock), 6))
            clock.advance(window_left(rn, clock) + 0.01)
        assert windows == expected

    def test_a_completed_sweep_resets_the_window(self, rn, clock, monkeypatch):
        fake = install(rn, monkeypatch, FakeSsm(parameters={"dynamoTables/assetStorage": "asset-table"},
                                                fault=always(throttled)))
        for _ in range(3):
            fail_one_sweep(rn)
            clock.advance(window_left(rn, clock) + 0.01)
        fake.fault = None
        assert rn.get_resource_name(rn.ResourceKeys.ASSET_STORAGE_TABLE) == "asset-table"
        assert rn._failed_sweeps == 0

        rn._cache_fetched_at = 0.0
        fake.fault = always(throttled)
        assert rn.get_resource_name(rn.ResourceKeys.ASSET_STORAGE_TABLE) == "asset-table"
        assert window_left(rn, clock) == pytest.approx(rn.SWEEP_BACKOFF_BASE_SECONDS)

    def test_a_failed_sweep_leaves_the_cache_and_the_negative_record_as_they_were(self, rn, clock, monkeypatch):
        install(rn, monkeypatch, FakeSsm(fault=always(throttled)))
        rn._cache = {"dynamoTables/assetStorage": "stale-asset-table"}
        rn._cache_fetched_at = 0.0
        rn._missing_keys = {"dynamoTables/tagStorage": 0.0}

        fail_one_sweep(rn, rn.ResourceKeys.DATABASE_STORAGE_TABLE)

        assert rn._cache == {"dynamoTables/assetStorage": "stale-asset-table"}
        assert rn._cache_fetched_at == 0.0
        assert rn._missing_keys == {"dynamoTables/tagStorage": 0.0}


@pytest.mark.unit
class TestMissingPrefix:
    """R5: an unset VAMS_RESOURCE_PARAM_PREFIX is a KeyError, as before, and never a failed sweep."""

    def test_an_unset_prefix_raises_key_error_and_records_no_failure(self, rn, clock, monkeypatch):
        fake = install(rn, monkeypatch, FakeSsm(fault=always(throttled)))
        monkeypatch.delenv("VAMS_RESOURCE_PARAM_PREFIX")

        for _ in range(2):
            with pytest.raises(KeyError):
                rn.get_resource_name(rn.ResourceKeys.ASSET_STORAGE_TABLE)

        assert getattr(rn, "_failed_sweeps", 0) == 0
        assert fake.tokens == []


@pytest.mark.unit
class TestWarmPath:
    """R7: a cache hit takes no lock and reads no SSM."""

    def test_cached_lookups_make_no_call_and_take_no_lock(self, rn, monkeypatch):
        fake = install(rn, monkeypatch, FakeSsm(parameters={
            "dynamoTables/assetStorage": "asset-table",
            "dynamoTables/databaseStorage": "database-table",
            "cloudwatchLogGroups/auditActions": "actions-group",
        }))
        lock = CountingLock(getattr(rn, "_sweep_lock", threading.Lock()))
        monkeypatch.setattr(rn, "_sweep_lock", lock, raising=False)
        assert rn.get_resource_name(rn.ResourceKeys.ASSET_STORAGE_TABLE) == "asset-table"
        fake.tokens.clear()
        lock.acquired = 0

        keys = (rn.ResourceKeys.ASSET_STORAGE_TABLE, rn.ResourceKeys.DATABASE_STORAGE_TABLE,
                rn.ResourceKeys.AUDIT_LOG_ACTIONS)
        for i in range(1000):
            rn.get_resource_name(keys[i % len(keys)])

        assert fake.tokens == []
        assert lock.acquired == 0


@pytest.mark.unit
class TestSweepLogging:
    """R8: one line per sweep, counts and timings only; refused lookups log once per window."""

    def test_a_completed_sweep_logs_one_line_with_counts_and_no_values(self, rn, clock, monkeypatch):
        recorder = RecordingLogger()
        monkeypatch.setattr(rn, "logger", recorder)
        parameters = {f"dynamoTables/table{i:02d}": f"value-marker-{i:02d}" for i in range(11)}
        parameters["dynamoTables/assetStorage"] = "value-marker-asset"
        install(rn, monkeypatch, FakeSsm(parameters=parameters))

        assert rn.get_resource_name(rn.ResourceKeys.ASSET_STORAGE_TABLE) == "value-marker-asset"

        infos = recorder.at("info")
        assert len(infos) == 1, f"a completed sweep logged {infos}"
        for count in ("12", "2 pages", "2 calls"):
            assert count in infos[0]
        assert not recorder.at("warning", "error", "exception")
        logged = " ".join(message for _, message in recorder.lines)
        assert "value-marker" not in logged
        assert "table0" not in logged

    def test_a_failed_sweep_logs_one_error_and_its_window_one_warning(self, rn, clock, monkeypatch):
        recorder = RecordingLogger()
        monkeypatch.setattr(rn, "logger", recorder)
        install(rn, monkeypatch, FakeSsm(fault=always(throttled)))

        fail_one_sweep(rn)
        for _ in range(5):
            with pytest.raises(rn.ResourceNameUnavailableError):
                rn.get_resource_name(rn.ResourceKeys.DATABASE_STORAGE_TABLE)

        errors = recorder.at("error", "exception")
        assert len(errors) == 1
        for detail in ("ThrottlingException", "0 pages", "7 calls"):
            assert detail in errors[0]
        assert len(recorder.at("warning")) == 1

        clock.advance(window_left(rn, clock) + 0.01)
        fail_one_sweep(rn)
        with pytest.raises(rn.ResourceNameUnavailableError):
            rn.get_resource_name(rn.ResourceKeys.DATABASE_STORAGE_TABLE)
        assert len(recorder.at("error", "exception")) == 2
        assert len(recorder.at("warning")) == 2
        assert "Rate exceeded" not in " ".join(message for _, message in recorder.lines)
